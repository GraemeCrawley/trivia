"""The per-question barrier — HOW-IT-WORKS section 2, tested hardest.

Nothing scores, and no horse moves, until the room has gone.
"""

import time

import httpx
import pytest

from conftest import ADMIN_PASSWORD, Q1_CORRECT, Q1_WRONG
from metrics import record


def open_admin_page(phones, server):
    ph = phones.bare_context()
    ph.page.goto(server.base_url + "/admin")
    ph.page.fill("#admin-password", ADMIN_PASSWORD)
    ph.page.click("#btn-login")
    ph.page.wait_for_selector("#admin-panel:not([hidden])", timeout=10000)
    return ph.page


def scores(state):
    return {p["id"]: p["score"] for p in state["leaderboard"]}


# ---------------------------------------------------------------------------

def test_nothing_moves_until_every_player_is_in(server, admin, phones):
    """Poll the leaderboard and the horse race for the whole question."""
    pin = admin.create(timer=25, per_round=8, reveal=3)
    ps = phones(pin, 6, prefix="H")
    board = open_admin_page(phones, server)

    admin.start()
    for p in ps:
        p.wait_question()

    for p in ps[:-1]:
        p.click_option(Q1_CORRECT)
        p.wait_waiting()

    baseline_board = admin.state()["leaderboard"]
    baseline_race = board.inner_html("#admin-race")
    assert all(e["score"] == 0 for e in baseline_board)

    samples = 0
    end = time.time() + 5.0
    while time.time() < end:
        s = admin.state()
        assert s["phase"] == "question", (
            "the barrier released with one player still out")
        assert s["leaderboard"] == baseline_board, (
            f"leaderboard moved mid-question: {s['leaderboard']}")
        assert s["progress"] == {"answered": 5, "total": 6}
        samples += 1
        time.sleep(0.1)
    assert board.inner_html("#admin-race") == baseline_race, (
        "a horse moved while a player had not answered")
    record("barrier.poll_samples_with_leaderboard_frozen", samples)

    # The last player goes in. Reveal must fire at once, not at the buzzer.
    t0 = time.time()
    ps[-1].click_option(Q1_CORRECT)
    s, seen = admin.wait_phase_change("question", timeout=10)
    record("barrier.reveal_latency_after_last_answer_s", round(seen - t0, 3))
    assert s["phase"] == "reveal"
    assert seen - t0 < 1.5, f"reveal took {seen - t0:.2f}s after the last answer"
    assert all(e["score"] > 0 for e in s["leaderboard"])


def test_all_submit_reveal_does_not_wait_out_the_clock(server, admin, phones):
    pin = admin.create(timer=30, per_round=8, reveal=3)
    ps = phones(pin, 4, prefix="A")
    admin.start()
    t_start = time.time()
    for p in ps:
        p.wait_question()
    for p in ps:
        p.click_option(Q1_CORRECT)
    s, seen = admin.wait_phase_change("question", timeout=15)
    assert s["phase"] == "reveal"
    assert seen - t_start < 10, (
        f"reveal at +{seen - t_start:.1f}s with a 30s clock — it waited")
    assert s["reveal"]["reason"] == "all_answered"
    for p in ps:
        assert p.score() > 0


def test_one_player_never_answers_reveal_at_her_own_deadline(server, admin, phones):
    pin = admin.create(timer=6, per_round=8, reveal=2)
    ps = phones(pin, 3, prefix="D")
    admin.start()
    t_start = time.time()
    for p in ps:
        p.wait_question()
    laggard = ps[-1]
    dl = laggard.deadline()
    ceiling = t_start + 6 + 10

    for p in ps[:-1]:
        p.click_option(Q1_CORRECT)

    s, seen = admin.wait_phase_change("question", timeout=20)
    record("barrier.release_vs_laggard_deadline_s", round(seen - dl, 3))
    assert s["phase"] == "reveal"
    assert s["reveal"]["reason"] == "deadlines_passed", s["reveal"]["reason"]
    assert seen >= dl - 0.05, f"released {dl - seen:.2f}s BEFORE her deadline"
    assert seen - dl < 1.2, f"released {seen - dl:.2f}s after her deadline"
    assert seen < ceiling - 3, "it waited for the ceiling instead of her deadline"
    assert s["reveal"]["deltas"][laggard.pid] == 0


def test_a_phone_that_never_renders_releases_at_the_ceiling(server, admin, phones):
    """Dead battery / gone outside: the room must not be held hostage."""
    pin = admin.create(timer=3, per_round=8, reveal=2)
    present = phones(pin, 2, prefix="C")
    ghost, = phones(pin, 1, prefix="G", open_pages=False)   # never loads the page

    t_before = time.time()
    admin.start()
    t_after = time.time()
    for p in present:
        p.wait_question()
        p.click_option(Q1_CORRECT)

    assert ghost.state().get("you_deadline") is None

    # It must NOT release at the present players' deadlines (~+3s).
    time.sleep(8)
    assert admin.state()["phase"] == "question", (
        "released early — the ghost was not waited for at all")

    s, seen = admin.wait_phase_change("question", timeout=20)
    ceiling_lo, ceiling_hi = t_before + 13, t_after + 13
    record("barrier.ceiling_release_offset_s", round(seen - ceiling_lo, 3))
    assert s["reveal"]["reason"] == "ceiling", s["reveal"]["reason"]
    assert ceiling_lo - 0.1 <= seen <= ceiling_hi + 0.8, (
        f"ceiling release at {seen - t_before:.2f}s after start "
        f"(expected ~13s)")
    assert s["reveal"]["deltas"][ghost.pid] == 0
    for p in present:
        assert s["reveal"]["deltas"][p.pid] > 0


def test_admin_end_question_scores_the_subset_and_zeroes_the_rest(server, admin, phones):
    pin = admin.create(timer=40, per_round=8, reveal=2)
    ps = phones(pin, 5, prefix="E")
    admin.start()
    for p in ps:
        p.wait_question()
    answered, silent = ps[:2], ps[2:]
    for p in answered:
        p.click_option(Q1_CORRECT)
        p.wait_waiting()

    time.sleep(0.4)
    assert admin.state()["phase"] == "question"
    t0 = time.time()
    admin.end_question()
    s, seen = admin.wait_phase_change("question", timeout=10)
    record("barrier.admin_end_latency_s", round(seen - t0, 3))
    assert seen - t0 < 1.0, f"End question took {seen - t0:.2f}s"
    assert s["reveal"]["reason"] == "admin_ended"
    for p in answered:
        assert s["reveal"]["deltas"][p.pid] > 0, f"{p.name} was in and scored 0"
    for p in silent:
        assert s["reveal"]["deltas"][p.pid] == 0, f"{p.name} was out and scored"


def test_a_player_joining_mid_question_is_seated_then_plays(server, admin, phones):
    pin = admin.create(timer=30, per_round=8, reveal=2)
    early = phones(pin, 2, prefix="S")
    admin.start()
    for p in early:
        p.wait_question()

    late, = phones(pin, 1, prefix="L", start_index=90)
    late.page.wait_for_selector("#screen-waiting:not([hidden])", timeout=10000)
    assert "next question" in late.page.inner_text("#bubble-count").lower()
    assert late.state()["you_seated"] is True

    s = admin.state()
    assert s["progress"]["total"] == 2, (
        f"a mid-question joiner was counted in the barrier: {s['progress']}")
    assert [p for p in s["players"] if p["id"] == late.pid][0]["seated"] is True

    # Her own POST must be refused for this question, not silently scored.
    r = late.api_answer(Q1_CORRECT)
    assert r.status_code == 409, r.status_code

    t0 = time.time()
    for p in early:
        p.click_option(Q1_CORRECT)
    s, seen = admin.wait_phase_change("question", timeout=10)
    assert seen - t0 < 2.0, "the seated player held the barrier open"
    assert s["reveal"]["deltas"].get(late.pid, 0) == 0
    assert late.score() == 0

    # Next question: she is a full participant.
    admin.wait_phase("question", timeout=15)
    late.wait_question()
    assert admin.state()["progress"]["total"] == 3
    assert late.state()["you_seated"] is False
