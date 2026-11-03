"""Thirty simulated phones — HOW-IT-WORKS section 10, 'the real pressure test'.

Thirty Chromium contexts, each one a phone: real page, real EventSource, real
POST. The synchronised answer is scheduled in-page so all thirty fire inside
the same few hundred milliseconds rather than one after another.
"""

import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import Q1_CORRECT, Q1_WRONG, join_api
from expected import points, speed_fraction
from metrics import record

N = 30
TIMER = 25.0
REVEAL = 6.0


class PhaseWatcher(threading.Thread):
    """Polls the organizer's view and records every phase it ever sees."""

    def __init__(self, admin, interval=0.04):
        super().__init__(daemon=True)
        self.admin, self.interval = admin, interval
        self.sequence: list[tuple[str, float]] = []
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            try:
                s = self.admin.state()
            except Exception:
                continue
            if not self.sequence or self.sequence[-1][0] != s["phase"]:
                self.sequence.append((s["phase"], time.time()))
            time.sleep(self.interval)

    def stop(self):
        self._halt.set()
        self.join(timeout=3)

    def transitions(self, frm, to):
        return sum(1 for i in range(1, len(self.sequence))
                   if self.sequence[i - 1][0] == frm and self.sequence[i][0] == to)


@pytest.mark.slow
def test_thirty_phones(server, admin, browser):
    pin = admin.create(timer=TIMER, per_round=8, reveal=REVEAL)

    # ---- join: thirty phones hitting /api/join at once -------------------
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=N) as pool:
        players = list(pool.map(
            lambda i: join_api(server, pin, f"Guest{i:02d}"), range(N)))
    join_wall = time.time() - t0
    record("concurrency.join_wall_s", round(join_wall, 2))
    assert join_wall < 10, f"thirty joins took {join_wall:.1f}s"

    roster = admin.state()["players"]
    assert len(roster) == N, f"roster shows {len(roster)}, expected {N}"
    assert len({p["id"] for p in roster}) == N, "duplicate player ids"
    assert len({p["name"] for p in roster}) == N, "names collided"

    contexts, pages = [], []
    try:
        from conftest import ANSWER_TIMING_SCRIPT
        t0 = time.time()
        for ph in players:
            ctx = browser.new_context(viewport={"width": 390, "height": 844})
            ctx.add_init_script(ANSWER_TIMING_SCRIPT)
            ctx.add_cookies([{"name": "bg_session", "value": ph.token,
                              "url": server.base_url}])
            page = ctx.new_page()
            page.goto(f"{server.base_url}/play?t={ph.token}")
            contexts.append(ctx)
            pages.append(page)
            ph.page, ph.context = page, ctx
        record("concurrency.context_open_wall_s", round(time.time() - t0, 2))

        watcher = PhaseWatcher(admin)
        watcher.start()
        admin.start()
        for ph in players:
            ph.wait_question(timeout=30000)

        # ---- everyone's own deadline, read from the server ---------------
        with ThreadPoolExecutor(max_workers=N) as pool:
            deadlines = dict(zip(
                [p.pid for p in players],
                pool.map(lambda p: p.deadline(), players)))

        # ---- one synchronised volley -------------------------------------
        correctness = {p.pid: (i % 3 != 0) for i, p in enumerate(players)}
        fire_at = int(time.time() * 1000) + 3000
        for ph in players:
            value = Q1_CORRECT if correctness[ph.pid] else Q1_WRONG
            ph.page.evaluate(
                """([ts, sel]) => {
                     const go = () => {
                       const b = document.querySelector(sel);
                       if (b) b.click();
                     };
                     const d = ts - Date.now();
                     if (d <= 0) go(); else setTimeout(go, d);
                   }""",
                [fire_at, f'#q-options button[data-value="{value}"]'])

        time.sleep(max(0.0, fire_at / 1000 - time.time()) + 3.0)

        calls = {}
        for ph in players:
            c = ph.page.evaluate("() => window.__answerCalls")
            assert c, f"{ph.name} never POSTed an answer"
            assert len(c) == 1, f"{ph.name} POSTed {len(c)} times"
            assert c[0]["status"] == 200, f"{ph.name} got {c[0]['status']}"
            calls[ph.pid] = c[0]

        sent = [c["sent_at"] for c in calls.values()]
        spread = (max(sent) - min(sent))
        rtts = sorted(c["rtt_ms"] for c in calls.values())
        p95 = rtts[int(0.95 * (len(rtts) - 1))]
        record("concurrency.answer_send_spread_ms", round(spread, 1))
        record("concurrency.answer_post_p50_ms", round(statistics.median(rtts), 1))
        record("concurrency.answer_post_p95_ms", round(p95, 1))
        record("concurrency.answer_post_max_ms", round(rtts[-1], 1))
        assert spread < 600, f"the volley spread over {spread:.0f}ms, not ~200ms"

        # ---- exactly one reveal, every answer counted --------------------
        s = admin.wait_phase("reveal", timeout=15)
        watcher.stop()
        record("concurrency.phase_sequence",
               " -> ".join(p for p, _ in watcher.sequence))
        assert watcher.transitions("question", "reveal") == 1, (
            f"phase sequence was {[p for p, _ in watcher.sequence]}")
        assert s["reveal"]["reason"] == "all_answered"

        deltas = s["reveal"]["deltas"]
        assert len(deltas) == N, f"{len(deltas)} deltas for {N} players"

        release_at = s["reveal_until"] - REVEAL
        visible = []
        for ph in players:
            ph.page.wait_for_selector("#screen-reveal:not([hidden])", timeout=10000)
            at = ph.page.evaluate("() => window.__revealAt")
            assert at, f"{ph.name} never showed the reveal"
            visible.append(at / 1000.0 - release_at)
        record("concurrency.reveal_visible_p50_s", round(statistics.median(visible), 3))
        record("concurrency.reveal_visible_max_s", round(max(visible), 3))
        assert max(visible) < 1.0, (
            f"slowest phone saw the reveal {max(visible):.2f}s after release")

        # ---- every score correct, computed independently ------------------
        bad = []
        for ph in players:
            c = calls[ph.pid]
            if not correctness[ph.pid]:
                lo = hi = 0
            else:
                render_at = deadlines[ph.pid] - TIMER
                e_lo = c["sent_at"] / 1000.0 - render_at
                e_hi = e_lo + c["rtt_ms"] / 1000.0
                hi = points(1.0, speed_fraction(e_lo, TIMER)) + 1
                lo = points(1.0, speed_fraction(e_hi, TIMER)) - 1
            got = deltas[ph.pid]
            if not (lo <= got <= hi):
                bad.append((ph.name, got, lo, hi))
        record("concurrency.scores_out_of_band", len(bad))
        assert not bad, f"scores outside the independently computed band: {bad}"

        with ThreadPoolExecutor(max_workers=N) as pool:
            totals = dict(zip([p.pid for p in players],
                              pool.map(lambda p: p.score(), players)))
        for pid, total in totals.items():
            assert total == deltas[pid], (
                f"running total {total} != this question's {deltas[pid]}")
        record("concurrency.correct_answers_scored",
               sum(1 for pid in deltas if deltas[pid] > 0))
        assert sum(1 for pid in deltas if deltas[pid] > 0) == \
            sum(1 for v in correctness.values() if v)
    finally:
        for ctx in contexts:
            try:
                ctx.close()
            except Exception:
                pass
