"""Organizer controls and the end of the game, in a real browser."""

import time

import pytest


@pytest.fixture(autouse=True)
def leave_the_shared_server_clean(admin):
    """These tests pause and reset a SESSION-SCOPED server.

    Without this, a paused game leaks into whatever runs next and fails it
    somewhere else entirely — which is exactly what happened: three unrelated
    tests, including the full three-round game, failed in the full run and
    passed in isolation.
    """
    yield
    try:
        admin.c.post("/api/admin/pause", json={"paused": False})
        admin.create()
    except Exception:
        pass


def open_more(page):
    """Every control lives in the sticky tray behind one chevron.

    There used to be a second "More controls" fold inside it; two nested
    disclosures for one set of buttons was confusing and it is gone.
    """
    page.wait_for_selector("#btn-tray", timeout=10000)
    if page.locator("#control-buttons").is_hidden():
        page.click("#btn-tray")
        page.wait_for_selector("#control-buttons:not([hidden])", timeout=5000)


def login_admin(server, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(f"{server.base_url}/admin", wait_until="domcontentloaded")
    page.fill("#admin-password", "testpw")
    page.click("#btn-login")
    page.wait_for_selector("#admin-panel:not([hidden])", timeout=10000)
    return ctx, page


class TestProgressIsShown:
    def test_both_views_say_which_round_and_question(self, server, browser, admin, phones):
        pin = admin.create(timer=30, per_round=8, reveal=2)
        ps = phones(pin, 1, prefix="PG")
        ctx, page = login_admin(server, browser)
        admin.start()
        ps[0].wait_question(timeout=15000)

        player_text = ps[0].page.inner_text("#progress-label")
        page.wait_for_selector("#admin-progress-label:not([hidden])", timeout=10000)
        admin_text = page.inner_text("#admin-progress-label")
        for text in (player_text, admin_text):
            assert "Round 1 of 3" in text, text
            assert "Question 1 of 8" in text, text
        ctx.close()


class TestRevealCountdown:
    def test_the_player_is_told_when_the_next_question_lands(self, server, admin, phones):
        pin = admin.create(timer=30, per_round=8, reveal=5)
        ps = phones(pin, 1, prefix="RC")
        admin.start()
        ps[0].wait_question(timeout=15000)
        ps[0].api_answer(True)
        # The phone learns about the reveal over SSE, not from its own POST.
        ps[0].page.wait_for_selector("#screen-reveal:not([hidden])", timeout=15000)
        ps[0].page.wait_for_selector("#reveal-countdown:not([hidden])", timeout=5000)
        text = ps[0].page.inner_text(".reveal-countdown-text")
        assert "Next question in" in text, text


class TestPause:
    def test_pausing_stops_the_clock_and_resuming_gives_the_time_back(
            self, server, browser, admin, phones):
        pin = admin.create(timer=30, per_round=8, reveal=2)
        ps = phones(pin, 1, prefix="PA")
        ctx, page = login_admin(server, browser)
        admin.start()
        ps[0].wait_question(timeout=15000)

        page.wait_for_selector("#btn-pause:not([hidden])", timeout=10000)
        page.click("#btn-pause")
        page.wait_for_function(
            "() => document.querySelector('#btn-pause').textContent.trim() === 'Resume'",
            timeout=10000)

        before = admin.state()
        time.sleep(3)
        after = admin.state()
        assert after["paused"] is True
        # The room has not moved on during the pause.
        assert after["phase"] == before["phase"]

        page.click("#btn-pause")
        page.wait_for_function(
            "() => document.querySelector('#btn-pause').textContent.trim() === 'Pause'",
            timeout=10000)
        assert admin.state()["paused"] is False
        # And she can still answer afterwards — her clock was given back.
        ps[0].api_answer(True)
        ctx.close()

    def test_an_answer_is_refused_while_paused(self, server, admin, phones):
        pin = admin.create(timer=30, per_round=8, reveal=2)
        ps = phones(pin, 1, prefix="PB")
        admin.start()
        ps[0].wait_question(timeout=15000)
        admin.c.post("/api/admin/pause", json={"paused": True}).raise_for_status()
        r = admin.c.post(f"/api/answer?t={ps[0].token}", json={"answer": True})
        assert r.status_code == 409
        admin.c.post("/api/admin/pause", json={"paused": False}).raise_for_status()


class TestNextQuestion:
    def test_it_moves_the_room_on_immediately(self, server, admin, phones):
        pin = admin.create(timer=60, per_round=8, reveal=1)
        ps = phones(pin, 1, prefix="NQ")
        admin.start()
        ps[0].wait_question(timeout=15000)
        first = admin.state()["question"]["id"]
        admin.c.post("/api/admin/next_question").raise_for_status()
        deadline = time.time() + 15
        while time.time() < deadline:
            s = admin.state()
            if s.get("question") and s["question"]["id"] != first:
                return
            time.sleep(0.25)
        pytest.fail("the room did not move on")


class TestReset:
    def test_reset_asks_first(self, server, browser, admin, phones):
        admin.create(timer=30, per_round=8, reveal=2)
        ctx, page = login_admin(server, browser)
        open_more(page)
        page.wait_for_selector("#btn-reset:not([hidden])", timeout=10000)
        assert page.locator("#reset-confirm").is_hidden()
        page.click("#btn-reset")
        page.wait_for_selector("#reset-confirm:not([hidden])", timeout=5000)
        page.click("#btn-reset-no")
        page.wait_for_selector("#reset-confirm", state="hidden", timeout=5000)
        ctx.close()

    def test_confirming_zeroes_the_scores_and_keeps_everyone_in(
            self, server, browser, admin, phones):
        pin = admin.create(timer=30, per_round=8, reveal=1)
        ps = phones(pin, 2, prefix="RS")
        ctx, page = login_admin(server, browser)
        admin.start()
        for p in ps:
            p.wait_question(timeout=15000)
            p.api_answer(True)
        time.sleep(2)

        open_more(page)
        page.click("#btn-reset")
        page.wait_for_selector("#reset-confirm:not([hidden])", timeout=5000)
        page.click("#btn-reset-yes")

        deadline = time.time() + 15
        while time.time() < deadline:
            s = admin.state()
            if s["phase"] == "lobby":
                assert len(s["players"]) == 2, "reset lost the players"
                assert all(p["score"] == 0 for p in s["players"])
                ctx.close()
                return
            time.sleep(0.25)
        pytest.fail("reset did not return the game to the lobby")


class TestTheGameEndsItself:
    def test_the_last_question_finishes_without_an_organizer_tap(
            self, server, admin, phones):
        """No "Next round" press to end a game that is already over.

        One round holding every question, so the only way out of it is the end
        of the game. Driven with "next question" to keep the test quick — the
        thing under test is what happens AFTER the final question resolves.
        """
        pin = admin.create(timer=10, per_round=24, reveal=1)
        ps = phones(pin, 1, prefix="EG")
        admin.start()
        ps[0].wait_question(timeout=15000)

        for _ in range(60):
            s = admin.state()
            if s["phase"] == "final":
                break
            if s["phase"] == "round_leaderboard":
                pytest.fail("stopped on a round leaderboard instead of ending")
            admin.c.post("/api/admin/next_question")
            time.sleep(0.15)
        else:
            pytest.fail(f"never ended; stuck at {admin.state()['phase']}")

        assert admin.state()["phase"] == "final"
        ps[0].page.wait_for_selector("#screen-final:not([hidden])", timeout=10000)
        assert ps[0].page.inner_text("#final-headline").strip()

    def test_the_winner_is_told_she_won(self, server, admin, phones):
        pin = admin.create(timer=10, per_round=24, reveal=1)
        ps = phones(pin, 2, prefix="WN")
        admin.start()
        for p in ps:
            p.wait_question(timeout=15000)
        # One player answers everything, the other nothing.
        for _ in range(60):
            st = admin.state()
            if st["phase"] == "final":
                break
            if st["phase"] == "question":
                ps[0].api_answer(True)
            admin.c.post("/api/admin/next_question")
            time.sleep(0.15)

        board = admin.state()["leaderboard"]
        assert board[0]["score"] > 0, "nobody scored, so nobody can be told they won"
        winner = next(p for p in ps if p.pid == board[0]["id"])
        loser = next(p for p in ps if p.pid != board[0]["id"])

        winner.page.wait_for_selector("#screen-final:not([hidden])", timeout=10000)
        assert "won" in winner.page.inner_text("#final-headline").lower()
        loser.page.wait_for_selector("#screen-final:not([hidden])", timeout=10000)
        assert "won" not in loser.page.inner_text("#final-headline").lower()
