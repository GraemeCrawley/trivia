"""The organizer's phone — HOW-IT-WORKS section 9 and 10."""

import time

import httpx
import pytest

from conftest import ADMIN_PASSWORD, Admin, Q1_CORRECT, Q1_ID
from metrics import record


def login_page(phones, server, password=ADMIN_PASSWORD):
    ph = phones.bare_context()
    ph.page.goto(server.base_url + "/admin")
    ph.page.wait_for_selector("#admin-login", timeout=10000)
    ph.page.fill("#admin-password", password)
    ph.page.click("#btn-login")
    return ph.page


def test_wrong_password_is_rejected(server, phones):
    page = login_page(phones, server, "not-the-password")
    page.wait_for_selector("#admin-error:not([hidden])", timeout=5000)
    assert page.locator("#admin-panel").is_hidden()
    assert page.locator("#admin-login").is_visible()
    # And the API says so too, with no cookie handed out.
    r = httpx.post(server.base_url + "/api/admin/login", data={"password": "x"})
    assert r.status_code == 403
    assert "bg_admin" not in r.cookies


def test_login_opens_the_panel(server, phones):
    page = login_page(phones, server)
    page.wait_for_selector("#admin-panel:not([hidden])", timeout=10000)
    assert page.locator("#admin-login").is_hidden()


def test_config_changes_take_effect(fresh_server, phones):
    """A fresh process, because the config block is hidden once a game exists."""
    server = fresh_server
    page = login_page(phones, server)
    page.wait_for_selector("#admin-panel:not([hidden])", timeout=10000)
    page.fill("#cfg-timer", "7")
    page.fill("#cfg-per-round", "4")
    page.fill("#cfg-reveal", "3")
    page.click("#btn-create")

    a = Admin(server.base_url)
    a.login()
    end = time.time() + 5
    while time.time() < end:
        s = a.state()
        if s["timer_seconds"] == 7:
            break
        time.sleep(0.1)
    assert s["timer_seconds"] == 7, f"timer stayed {s['timer_seconds']}"
    assert s["round"]["total"] == 6, (
        f"24 questions at 4 a round should be 6 rounds, got {s['round']['total']}")

    page.wait_for_function(
        "() => /^[0-9]{4}$/.test("
        "document.getElementById('pin-display').textContent.trim())",
        timeout=8000)
    assert page.inner_text("#pin-display") == s["pin"]
    assert page.get_attribute("#join-link", "href").endswith(f"/j/{s['pin']}")

    # A player joined with the new config gets the new clock.
    ph = httpx.post(server.base_url + "/api/join",
                    data={"pin": s["pin"], "name": "Cfg"})
    assert ph.status_code == 200
    a.start()
    tok = ph.json()["token"]
    httpx.post(server.base_url + "/api/rendered", params={"t": tok})
    st = httpx.get(server.base_url + "/api/state", params={"t": tok}).json()
    assert 6.0 < st["you_deadline"] - time.time() <= 7.5


def test_start_end_question_and_next_round_buttons(server, admin, phones):
    pin = admin.create(timer=40, per_round=2, reveal=2)
    ps = phones(pin, 2, prefix="AD")
    page = login_page(phones, server)
    page.wait_for_selector("#admin-panel:not([hidden])", timeout=10000)

    page.wait_for_selector("#btn-start:not([hidden])", timeout=5000)
    assert page.locator("#btn-end-question").is_hidden()
    page.click("#btn-start")

    page.wait_for_selector("#btn-end-question:not([hidden])", timeout=5000)
    assert admin.wait_phase("question", timeout=5)["phase"] == "question"
    for p in ps:
        p.wait_question()
    ps[0].click_option(Q1_CORRECT)

    page.click("#btn-end-question")          # question 1 of 2 in this round
    rev = admin.wait_phase("reveal", timeout=5)
    assert rev["reveal"]["reason"] == "admin_ended"
    assert rev["reveal"]["deltas"][ps[0].pid] > 0
    assert rev["reveal"]["deltas"][ps[1].pid] == 0

    admin.wait_phase("question", timeout=10)      # question 2 of the round
    page.click("#btn-end-question")
    admin.wait_phase("round_leaderboard", timeout=10)
    page.wait_for_selector("#btn-next-round:not([hidden])", timeout=5000)
    assert page.locator("#btn-end-question").is_hidden()

    page.click("#btn-next-round")
    s = admin.wait_phase("question", timeout=10)
    assert s["round"]["index"] == 1, "NEXT ROUND did not advance the round"
    ps[0].page.wait_for_selector("#screen-question:not([hidden])", timeout=10000)


def test_delete_all_photos_button(server, admin, phones):
    from conftest import jpeg_bytes
    pin = admin.create(timer=40, per_round=8, reveal=2)
    phones(pin, 1, prefix="Pic", selfie=jpeg_bytes())
    page = login_page(phones, server)
    page.wait_for_selector("#admin-panel:not([hidden])", timeout=10000)
    page.on("dialog", lambda d: d.accept())
    page.click("#btn-delete-selfies")
    end = time.time() + 5
    while time.time() < end:
        if all(not p["selfie"] for p in admin.state()["players"]):
            break
        time.sleep(0.1)
    assert all(not p["selfie"] for p in admin.state()["players"])
    left = [f for f in server.selfie_dir.iterdir()
            if f.is_file() and f.name != "game-state.json"]
    assert not left, f"photos still on the volume: {left}"


def test_starting_with_an_empty_lobby_does_not_burn_the_deck(server, phones):
    """The organizer taps START while guests are still walking over.

    `all()` over an empty list is True, so a room with nobody in it used to
    release the barrier at once and spend a question every reveal_seconds —
    four seconds after START it was already on r1q4. Either outcome is
    correct now: refuse START, or open question one and sit on it. What is
    never acceptable is the deck draining, or a button that does nothing and
    says nothing.
    """
    a = Admin(server.base_url)
    assert a.login().status_code == 200
    a.create(timer=15, per_round=8, reveal=1)

    page = login_page(phones, server)
    page.wait_for_selector("#admin-panel:not([hidden])", timeout=10000)

    r = a.start(expect_ok=False)
    refused = r.status_code >= 400
    record("admin.empty_lobby_start", f"{r.status_code}")

    if not refused:
        time.sleep(4.0)
        s = a.state()
        qid = (s.get("question") or {}).get("id")
        record("admin.empty_lobby_question_after_4s", qid)
        assert qid == Q1_ID, (
            f"with an empty lobby the room ran on to {qid} in four seconds; a "
            "guest joining now lands mid-deck")
        return

    # Refused — then she has to be told, on the screen she is looking at.
    page.click("#btn-start")
    page.wait_for_function(
        """() => {
             const panel = document.getElementById('admin-panel');
             if (!panel) return false;
             return [...panel.querySelectorAll('*')].some(
               e => !e.hidden && e.offsetParent !== null
                    && e.children.length === 0
                    && /nobody|no one|joined|empty/i.test(e.textContent || ''));
           }""",
        timeout=5000)
    after = a.state()
    assert after["phase"] == "lobby", "a refused START still moved the room"
    assert after.get("question") is None, "a question was opened anyway"
    assert all(p["score"] == 0 for p in after["leaderboard"])

    # And it starts normally the moment someone is actually in the room, on
    # the FIRST question — proof the deck was not touched by the refusal.
    from conftest import join_api
    join_api(server, a.state()["pin"], "First")
    assert a.c.post("/api/admin/start").status_code == 200
    assert a.wait_phase("question", timeout=5)["question"]["id"] == Q1_ID


def test_adjudicate_a_flagged_free_text_answer(server, admin, phones):
    """HOW-IT-WORKS section 10: flip a flagged answer and watch the leaderboard re-score."""
    pin = admin.create(timer=20, per_round=8, reveal=1)
    ps = phones(pin, 2, prefix="FL")
    page = login_page(phones, server)
    page.wait_for_selector("#admin-panel:not([hidden])", timeout=10000)

    admin.start()
    admin.skip_to("r3q6")
    for p in ps:
        p.wait_question(timeout=15000)
    ps[0].page.fill("#q-text-input", "otawwa")     # ratio 0.833 against "ottawa": scores and is flagged
    ps[0].page.click("#q-text-submit")
    ps[1].page.fill("#q-text-input", "ottawa")
    ps[1].page.click("#q-text-submit")
    rev, _ = admin.wait_phase_change("question", timeout=20)
    awarded = rev["reveal"]["deltas"][ps[0].pid]
    assert awarded > 0

    page.wait_for_selector("#flagged-queue:not([hidden])", timeout=10000)
    rows = page.locator("#flagged-list .flagrow")
    assert rows.count() == 1, f"{rows.count()} rows in the adjudication queue"
    row = rows.first
    assert row.get_attribute("data-player-id") == ps[0].pid
    assert "otawwa" in row.inner_text()

    before = ps[0].score()
    row.locator("button").click()                     # revoke it
    end = time.time() + 5
    while time.time() < end and ps[0].score() == before:
        time.sleep(0.1)
    after = ps[0].score()
    record("admin.review_revoked_points", before - after)
    assert after == before - awarded, (
        f"revoking {awarded} points left the score at {after}, was {before}")

    board = {e["name"]: e["score"] for e in admin.state()["leaderboard"]}
    assert board[ps[0].name] == after, "the leaderboard did not re-score"

    page.locator("#flagged-list .flagrow button").click()   # give it back
    end = time.time() + 5
    while time.time() < end and ps[0].score() != before:
        time.sleep(0.1)
    assert ps[0].score() == before, "giving the points back did not restore them"
