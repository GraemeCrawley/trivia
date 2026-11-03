"""Survival — HOW-IT-WORKS section 10.

Refresh, a closed tab, a phone that went dark for half a minute, and the
container bouncing under everyone.
"""

import time

import httpx
import pytest

from conftest import ADMIN_PASSWORD, Admin, Q1_CORRECT
from metrics import record

HIDE = """() => {
  Object.defineProperty(document, 'visibilityState',
    {get: () => 'hidden', configurable: true});
  Object.defineProperty(document, 'hidden', {get: () => true, configurable: true});
  document.dispatchEvent(new Event('visibilitychange'));
}"""

SHOW = """() => {
  Object.defineProperty(document, 'visibilityState',
    {get: () => 'visible', configurable: true});
  Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
  document.dispatchEvent(new Event('visibilitychange'));
}"""


def timer_text(page) -> int:
    return int(page.inner_text("#timer-text"))


def wait_timer_live(phone, timer_seconds, timeout=10.0):
    """The countdown only starts once the server has answered /api/rendered."""
    phone.deadline()
    end = time.time() + timeout
    while time.time() < end:
        t = timer_text(phone.page)
        if abs(t - timer_seconds) <= 2:
            return t
        time.sleep(0.1)
    raise AssertionError(
        f"countdown never reached ~{timer_seconds}s (stuck at {t})")


def click_true(phone):
    phone.page.click('#q-truefalse [data-value="true"]')


def test_refresh_mid_question_keeps_identity_score_and_clock(server, admin, phones):
    pin = admin.create(timer=40, per_round=8, reveal=2)
    ps = phones(pin, 2, prefix="RF")
    admin.start()
    for p in ps:
        p.wait_question()

    me = ps[0]
    before_deadline = me.deadline()
    time.sleep(3)
    before_text = timer_text(me.page)

    me.page.reload()
    me.wait_question()
    after_deadline = me.deadline()
    after_text = timer_text(me.page)

    assert after_deadline == before_deadline, (
        "the refresh restarted her clock — she got a second full timer")
    assert me.page.inner_text("#you-name") == me.name
    assert abs(after_text - before_text) <= 1, (
        f"countdown jumped from {before_text} to {after_text} across a refresh")
    assert admin.state()["progress"] == {"answered": 0, "total": 2}

    me.click_option(Q1_CORRECT)
    me.wait_waiting()


def test_close_the_tab_and_reopen_the_link(server, admin, phones):
    pin = admin.create(timer=40, per_round=8, reveal=2)
    ps = phones(pin, 2, prefix="CT")
    admin.start()
    for p in ps:
        p.wait_question()
    me = ps[0]
    me.click_option(Q1_CORRECT)
    me.wait_waiting()
    dl = me.deadline()

    me.page.close()
    time.sleep(0.5)
    me.page = me.context.new_page()
    me.open()

    me.page.wait_for_selector("#screen-waiting:not([hidden])", timeout=10000)
    assert me.page.inner_text("#you-name") == me.name
    assert me.deadline() == dl
    assert me.state()["you_answered"] is True


def test_thirty_seconds_dark_then_back(server, admin, phones):
    """A phone that locked. Headless Chromium will not actually background a
    page, so this reproduces the same failure mode: the visibilitychange
    handler fires, the connection is cut for 30s, then it comes back."""
    pin = admin.create(timer=90, per_round=8, reveal=2)
    ps = phones(pin, 2, prefix="BG")
    admin.start()
    for p in ps:
        p.wait_question()
    me = ps[0]
    dl = me.deadline()
    before = wait_timer_live(me, 90)

    me.page.evaluate(HIDE)
    me.context.set_offline(True)
    time.sleep(30)
    me.context.set_offline(False)
    me.page.evaluate(SHOW)

    deadline = time.time() + 5
    shown = None
    while time.time() < deadline:
        shown = timer_text(me.page)
        if abs(shown - (dl - time.time())) <= 1.5:
            break
        time.sleep(0.2)

    record("survival.countdown_after_30s_dark",
           f"before={before}s shown={shown}s true={dl - time.time():.0f}s")
    assert shown is not None
    assert abs(shown - (dl - time.time())) <= 2.0, (
        f"countdown read {shown}s when {dl - time.time():.0f}s remained — frozen")
    assert before - shown >= 25, (
        f"countdown barely moved across 30s dark ({before} -> {shown})")
    assert me.visible_screen() == "screen-question"

    # And it is live again, not a dead page.
    me.click_option(Q1_CORRECT)
    me.wait_waiting()
    assert me.state()["you_answered"] is True


@pytest.mark.slow
def test_server_restart_mid_question(server, phones):
    """State is a JSON snapshot on the selfie volume (app/persist.py).

    Anything that does not come back is reported, not fixed.
    """
    admin = Admin(server.base_url)
    assert admin.login().status_code == 200
    pin = admin.create(timer=40, per_round=8, reveal=2)
    ps = phones(pin, 3, prefix="RS")
    admin.start()

    # Question 1 in full, so everyone has a score worth losing.
    for p in ps:
        p.wait_question()
    for p in ps:
        p.click_option(Q1_CORRECT)
    admin.wait_phase("reveal", timeout=15)
    before_scores = {p.pid: p.score() for p in ps}
    assert all(v > 0 for v in before_scores.values())

    # Question 2, one player in, then the container bounces.
    admin.wait_phase("question", timeout=20)
    for p in ps:
        p.wait_question()
    click_true(ps[0])                 # question 2 of the deck is true/false
    ps[0].wait_waiting()
    before_progress = admin.state()["progress"]
    before_deadlines = {p.pid: p.deadline() for p in ps}

    t0 = time.time()
    server.restart()
    record("survival.restart_downtime_s", round(time.time() - t0, 2))

    health = httpx.get(server.base_url + "/healthz").json()
    findings = []
    if health["players"] != 3:
        findings.append(f"players: {health['players']} of 3 came back")
    if health["phase"] != "question":
        findings.append(f"phase: came back as {health['phase']!r}, not 'question'")

    after_scores = {p.pid: p.score() for p in ps}
    if after_scores != before_scores:
        findings.append(f"scores: {before_scores} -> {after_scores}")

    fresh_admin = Admin(server.base_url)
    admin_survived = fresh_admin.c.get("/api/admin/state").status_code == 200
    stale = httpx.get(server.base_url + "/api/admin/state",
                      cookies=dict(admin.c.cookies)).status_code
    record("survival.admin_cookie_survives_restart", stale == 200)

    assert fresh_admin.login().status_code == 200
    after = fresh_admin.state()
    if after["progress"] != before_progress:
        findings.append(f"barrier: {before_progress} -> {after['progress']}")
    for p in ps:
        if abs(p.deadline() - before_deadlines[p.pid]) > 0.01:
            findings.append(f"{p.name}'s deadline moved")

    record("survival.restart_findings", "; ".join(findings) or "none")

    # The phones must find their own way back without a manual reload.
    reconnected = []
    end = time.time() + 15
    for p in ps:
        while time.time() < end:
            try:
                if p.page.evaluate("() => window.__answerCalls !== undefined") \
                        and p.visible_screen() in ("screen-question", "screen-waiting"):
                    reconnected.append(p.name)
                    break
            except Exception:
                pass
            time.sleep(0.3)
    assert len(reconnected) == 3, f"only {reconnected} came back on their own"

    # Finish the question to prove the room is not wedged.
    for p in ps[1:]:
        click_true(p)
    s = fresh_admin.wait_phase("reveal", timeout=20)
    assert s["phase"] == "reveal"

    assert not findings, "state did not survive the restart: " + "; ".join(findings)


def test_sigterm_shuts_the_app_down_promptly(fresh_server):
    """A redeploy sends SIGTERM. With SSE streams open the app must still go.

    docker's default stop timeout is 10s; anything that ignores SIGTERM is a
    ten-second hang in front of the room on every deploy.
    """
    import threading

    a = Admin(fresh_server.base_url)
    a.login()
    a.create(timer=30, per_round=8, reveal=2)

    def hold_stream():
        try:
            with httpx.stream("GET", fresh_server.base_url + "/api/stream",
                              timeout=60) as r:
                for _ in r.iter_lines():
                    pass
        except Exception:
            pass

    for _ in range(3):
        threading.Thread(target=hold_stream, daemon=True).start()
    time.sleep(1.0)

    fresh_server.stop(grace=12.0)
    record("survival.sigterm_shutdown_s", round(fresh_server.last_stop_seconds, 2))
    record("survival.sigterm_needed_kill", fresh_server.last_stop_forced)
    assert not fresh_server.last_stop_forced, (
        f"SIGTERM was ignored for {fresh_server.last_stop_seconds:.1f}s with "
        "three SSE streams open; it took SIGKILL")


def test_sse_heartbeat_keeps_bytes_moving_through_a_silent_window(server, admin):
    """HOW-IT-WORKS section 5's mitigation for the idle-timeout risk.

    Local only — this says nothing about any reverse proxy in front of the app, which
    is the risk the spec actually flags. It only proves the app emits the
    keep-alive it promises.
    """
    admin.create(timer=30, per_round=8, reveal=2)   # a quiet lobby, no traffic
    beats, frames = 0, 0
    t0 = time.time()
    with httpx.stream("GET", server.base_url + "/api/stream", timeout=40) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith(": beat"):
                beats += 1
            elif line.startswith("data: "):
                frames += 1
            if time.time() - t0 > 25:
                break
    record("survival.sse_beats_in_25s", beats)
    assert beats >= 2, (
        f"only {beats} heartbeats in 25s of silence; HOW-IT-WORKS section 5 asks for "
        "one every 10 seconds so no idle timeout in the chain kills the game")


def test_a_resume_link_alone_restores_the_photo_session(server, admin, browser):
    """HOW-IT-WORKS section 6: the token in the URL is the resume identity.

    A phone that opens `play?t=...` with no cookie — a reopened link, a cleared
    browser, the link forwarded to her other device — must come away with a
    `bg_session` cookie, or every face in the room 403s and she sees a room
    full of initials.
    """
    from conftest import ANSWER_TIMING_SCRIPT, jpeg_bytes, join_api

    pin = admin.create(timer=40, per_round=8, reveal=2)
    join_api(server, pin, "Ada", selfie_bytes=jpeg_bytes())
    bea = join_api(server, pin, "Bea")

    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.add_init_script(ANSWER_TIMING_SCRIPT)          # no cookie preloaded
    try:
        page = ctx.new_page()
        hits = []
        page.on("response",
                lambda r: hits.append((r.url, r.status)) if "/selfie/" in r.url
                else None)
        page.goto(f"{server.base_url}/play?t={bea.token}")
        page.wait_for_selector("#lobby-roster .bubble", timeout=10000)
        page.wait_for_timeout(1500)

        assert page.inner_text("#you-name") == "Bea"
        names = {c["name"] for c in ctx.cookies()}
        assert "bg_session" in names, (
            "opening a resume link handed out no session cookie, so no selfie "
            f"in the room will ever load (cookies: {names})")
        assert hits, "the phone never requested Ada's selfie"
        for url, status in hits:
            assert status == 200, f"{url} -> {status} on a resumed link"
    finally:
        ctx.close()
