"""Playwright end-to-end harness for bargames.

Boots a real uvicorn process (one worker, as production pins it), drives it
with real Chromium contexts — one per simulated phone — and speaks to the API
directly where the spec asks for a forged request.

Environment is the plain local install: cookie path "/" and Secure off.
"""

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from metrics import METRICS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VENV_PY = ROOT / ".venv" / "bin" / "python"
ADMIN_PASSWORD = "testpw"

# The first question of the seeded deck, read from the seed rather than
# hard-coded, so editing seed/questions.json does not quietly rot the barrier
# tests into clicking a button that is no longer there.
_SEED = json.loads(
    (Path(__file__).resolve().parents[2] / "app" / "seed" / "questions.json")
    .read_text())["questions"]
_Q1 = _SEED[0]
assert _Q1["type"] == "multiple_choice", (
    "the barrier and bubble tests click a multiple-choice option on the first "
    f"question; it is now {_Q1['type']!r}")
Q1_ID = _Q1["id"]
Q1_CORRECT = _Q1["correct"]
Q1_WRONG = next(o for o in _Q1["options"] if o != Q1_CORRECT)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AppServer:
    """A uvicorn process we can stop and restart under the tests' feet."""

    def __init__(self, port: int | None = None, workdir: Path | None = None):
        self.port = port or free_port()
        self.dir = Path(workdir or tempfile.mkdtemp(prefix="bargames-e2e-"))
        # BARGAMES_SELFIE_DIR is the app's DATA directory; photographs live in
        # a "photos" subdirectory of it, apart from the game snapshot and the
        # admin secret, because delete_all() wipes the photo directory whole.
        self.data_dir = self.dir / "selfies"
        self.selfie_dir = self.data_dir / "photos"
        self.state_file = self.dir / "game-state.json"
        self.log = self.dir / "server.log"
        self.proc: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict:
        e = dict(os.environ)
        e.update({
            "BARGAMES_ADMIN_PASSWORD": ADMIN_PASSWORD,
            "BARGAMES_COOKIE_PATH": "/",
            "BARGAMES_COOKIE_SECURE": "0",
            "BARGAMES_SELFIE_DIR": str(self.data_dir),
            "BARGAMES_STATE_FILE": str(self.state_file),
            "PYTHONUNBUFFERED": "1",
        })
        e.update(getattr(self, "extra_env", {}) or {})
        return e

    def start(self) -> "AppServer":
        self.selfie_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fh = open(self.log, "ab")
        self.proc = subprocess.Popen(
            [str(VENV_PY), "-m", "uvicorn", "main:app", "--app-dir", "app",
             "--port", str(self.port), "--workers", "1", "--host", "127.0.0.1"],
            cwd=str(ROOT), env=self.env(), stdout=fh, stderr=fh,
        )
        self.wait_ready()
        return self

    def wait_ready(self, timeout: float = 25.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = httpx.get(self.base_url + "/healthz", timeout=1.0)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(
                    f"server died on boot:\n{self.log.read_text()[-4000:]}")
            time.sleep(0.15)
        raise RuntimeError(f"server not ready:\n{self.log.read_text()[-4000:]}")

    last_stop_seconds: float = 0.0
    last_stop_forced: bool = False

    def stop(self, grace: float = 10.0) -> None:
        self.last_stop_forced = False
        t0 = time.time()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()                 # SIGTERM, as docker sends
            try:
                self.proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                self.last_stop_forced = True
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.last_stop_seconds = time.time() - t0
        self.proc = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def wipe_state(self) -> None:
        try:
            self.state_file.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ admin API

class Admin:
    """The organizer, over HTTP. Used to set up and steer every test."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.c = httpx.Client(base_url=base_url, timeout=20.0)

    def login(self, password: str = ADMIN_PASSWORD) -> httpx.Response:
        return self.c.post("/api/admin/login", data={"password": password})

    def create(self, timer=15.0, per_round=8, reveal=5.0) -> str:
        r = self.c.post("/api/admin/create", json={
            "timer_seconds": timer, "questions_per_round": per_round,
            "reveal_seconds": reveal})
        r.raise_for_status()
        return r.json()["pin"]

    def start(self, expect_ok: bool = True):
        """START. `expect_ok=False` returns the response unraised, for the
        cases where refusing is the correct behaviour (an empty lobby)."""
        r = self.c.post("/api/admin/start")
        if expect_ok:
            r.raise_for_status()
        return r

    def end_question(self):
        r = self.c.post("/api/admin/end_question")
        r.raise_for_status()
        return r

    def next_round(self):
        r = self.c.post("/api/admin/next_round")
        r.raise_for_status()
        return r

    def delete_selfies(self):
        r = self.c.post("/api/admin/delete_selfies")
        r.raise_for_status()
        return r.json()

    def state(self) -> dict:
        r = self.c.get("/api/admin/state")
        r.raise_for_status()
        return r.json()

    def wait_phase(self, phase: str, timeout: float = 20.0) -> dict:
        end = time.time() + timeout
        last = None
        while time.time() < end:
            last = self.state()
            if last["phase"] == phase:
                return last
            time.sleep(0.05)
        raise AssertionError(
            f"phase {phase!r} never reached (last={last and last['phase']!r})")

    def review(self, player_id: str, question_id: str, accept: bool):
        r = self.c.post("/api/admin/review", json={
            "player_id": player_id, "question_id": question_id,
            "accept": accept})
        r.raise_for_status()
        return r.json()

    def skip_to(self, question_id: str, timeout: float = 120.0) -> None:
        """Walk the room forward to a given seeded question, scoring nothing."""
        end = time.time() + timeout
        while time.time() < end:
            s = self.state()
            if s["phase"] == "question":
                if (s.get("question") or {}).get("id") == question_id:
                    return
                self.end_question()
                self.wait_phase_change("question", timeout=10)
            elif s["phase"] == "round_leaderboard":
                self.next_round()
            elif s["phase"] in ("final", "lobby", "none"):
                raise AssertionError(
                    f"room is {s['phase']!r}; never reached {question_id}")
            else:
                time.sleep(0.05)
        raise AssertionError(f"never reached {question_id}")

    def wait_phase_change(self, frm: str, timeout: float = 30.0):
        """Return (new_state, wall_clock_when_seen)."""
        end = time.time() + timeout
        while time.time() < end:
            s = self.state()
            if s["phase"] != frm:
                return s, time.time()
            time.sleep(0.05)
        raise AssertionError(f"phase never left {frm!r}")


# ----------------------------------------------------------------- the phones

ANSWER_TIMING_SCRIPT = """
window.__answerCalls = [];
window.__revealAt = null;
(() => {
  const orig = window.fetch;
  window.fetch = async function (...args) {
    const url = String(args[0] || "");
    const t0 = Date.now();
    const p0 = performance.now();
    const r = await orig.apply(this, args);
    if (url.indexOf("api/answer") !== -1) {
      window.__answerCalls.push({
        sent_at: t0, rtt_ms: performance.now() - p0, status: r.status,
      });
    }
    return r;
  };
  // When did the reveal actually appear on THIS phone?
  const watch = () => {
    const el = document.getElementById("screen-reveal");
    if (!el) return;
    const mark = () => {
      if (!el.hidden && window.__revealAt === null) window.__revealAt = Date.now();
    };
    new MutationObserver(mark).observe(
      el, {attributes: true, attributeFilter: ["hidden"]});
    mark();
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch);
  } else {
    watch();
  }
})();
"""


@dataclass
class Phone:
    name: str
    token: str
    pid: str
    server: "AppServer"
    context: object = None
    page: object = None

    # ---- server truth about me
    def state(self) -> dict:
        r = httpx.get(self.server.base_url + "/api/state",
                      params={"t": self.token}, timeout=10.0)
        r.raise_for_status()
        return r.json()

    def deadline(self, timeout: float = 10.0) -> float:
        end = time.time() + timeout
        while time.time() < end:
            s = self.state()
            if s.get("you_deadline"):
                return s["you_deadline"]
            time.sleep(0.05)
        raise AssertionError(f"{self.name} never got a deadline")

    def score(self) -> int:
        return self.state()["you"]["score"]

    # ---- raw API actions (bypassing the UI on purpose)
    def api_rendered(self) -> httpx.Response:
        return httpx.post(self.server.base_url + "/api/rendered",
                          params={"t": self.token}, timeout=10.0)

    def api_answer(self, value) -> httpx.Response:
        return httpx.post(self.server.base_url + "/api/answer",
                          params={"t": self.token}, json={"answer": value},
                          timeout=10.0)

    # ---- UI actions
    def open(self, browser=None) -> "Phone":
        self.page.goto(f"{self.server.base_url}/play?t={self.token}")
        return self

    def wait_question(self, timeout: float = 15000):
        self.page.wait_for_selector("#screen-question:not([hidden])",
                                    timeout=timeout)

    def wait_waiting(self, timeout: float = 15000):
        self.page.wait_for_selector("#screen-waiting:not([hidden])",
                                    timeout=timeout)

    def click_option(self, value: str):
        self.page.click(f'#q-options button[data-value="{value}"]')

    def visible_screen(self) -> str | None:
        return self.page.evaluate("""() => {
          const ids = ["screen-lobby","screen-question","screen-waiting",
                       "screen-reveal","screen-leaderboard","screen-final"];
          return ids.find(i => { const e = document.getElementById(i);
                                 return e && !e.hidden; }) || null;
        }""")


def join_api(server: AppServer, pin: str, name: str,
             selfie_bytes: bytes | None = None) -> Phone:
    files = {"selfie": ("s.jpg", selfie_bytes, "image/jpeg")} if selfie_bytes else None
    r = httpx.post(server.base_url + "/api/join",
                   data={"pin": pin, "name": name}, files=files, timeout=20.0)
    r.raise_for_status()
    d = r.json()
    return Phone(name=name, token=d["token"], pid=d["id"], server=server)


def jpeg_bytes(colour=(200, 40, 40), size=(400, 300)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="JPEG")
    return buf.getvalue()


# -------------------------------------------------------------------- fixtures

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Thirty pages must all keep real timers.

    Chromium throttles setTimeout in pages it considers backgrounded, and with
    thirty contexts open that is nearly all of them — which would make a
    "everyone answers within 200ms" test measure Chromium's scheduler rather
    than the server. A known deviation from a real
    phone.
    """
    return {**browser_type_launch_args, "args": [
        *(browser_type_launch_args.get("args") or []),
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]}


@pytest.fixture(scope="session")
def server():
    s = AppServer()
    s.wipe_state()
    s.start()
    yield s
    s.stop()


@pytest.fixture
def fresh_server():
    """A server with no game at all, for the 'no game yet' route checks."""
    s = AppServer()
    s.wipe_state()
    s.start()
    yield s
    s.stop()
    shutil.rmtree(s.dir, ignore_errors=True)


@pytest.fixture
def admin(server):
    a = Admin(server.base_url)
    r = a.login()
    assert r.status_code == 200, r.text
    yield a
    a.c.close()


@pytest.fixture
def phones(server, browser):
    """Factory for simulated phones. Closes every context afterwards."""
    made: list[Phone] = []

    def make(pin: str, count: int = 1, prefix: str = "P", open_pages: bool = True,
             selfie: bytes | None = None, start_index: int = 0) -> list[Phone]:
        out = []
        for i in range(count):
            name = f"{prefix}{start_index + i:02d}"
            ph = join_api(server, pin, name, selfie_bytes=selfie)
            ctx = browser.new_context(viewport={"width": 390, "height": 844})
            ctx.add_init_script(ANSWER_TIMING_SCRIPT)
            ctx.add_cookies([{"name": "bg_session", "value": ph.token,
                              "url": server.base_url}])
            ph.context = ctx
            ph.page = ctx.new_page()
            made.append(ph)
            out.append(ph)
            if open_pages:
                ph.open()
        return out

    def bare_context():
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        ctx.add_init_script(ANSWER_TIMING_SCRIPT)
        made.append(Phone(name="bare", token="", pid="", server=server,
                          context=ctx, page=ctx.new_page()))
        return made[-1]

    make.bare_context = bare_context
    yield make
    for ph in made:
        try:
            ph.context.close()
        except Exception:
            pass


# ------------------------------------------------------------- results summary

_RESULTS: list[tuple[str, str]] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" or (rep.when == "setup" and rep.outcome != "passed"):
        _RESULTS.append((item.nodeid, rep.outcome))


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: the long pressure tests")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    tr = terminalreporter
    tr.write_sep("=", "bargames end-to-end summary")
    seen = {}
    for nodeid, outcome in _RESULTS:
        seen[nodeid] = outcome
    passed = [n for n, o in seen.items() if o == "passed"]
    failed = [n for n, o in seen.items() if o == "failed"]
    skipped = [n for n, o in seen.items() if o == "skipped"]
    for n in failed:
        tr.write_line(f"FAIL  {n}", red=True)
    for n in skipped:
        tr.write_line(f"SKIP  {n}", yellow=True)
    tr.write_line(f"passed={len(passed)}  failed={len(failed)}  "
                  f"skipped={len(skipped)}")
    if METRICS:
        tr.write_sep("-", "measured numbers")
        for k in sorted(METRICS):
            tr.write_line(f"{k}: {METRICS[k]}")
        try:
            out = Path(config.rootdir) / "tests" / "e2e" / "last-metrics.json"
            out.write_text(json.dumps(METRICS, indent=2))
        except Exception:
            pass
