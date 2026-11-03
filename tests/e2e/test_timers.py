"""Per-phone timers under lockstep — HOW-IT-WORKS section 2.

Each phone's clock starts when THAT phone rendered the question, and the
barrier waits for the last of them. A late answer is refused by the server
even when the client insists otherwise.
"""

import time

import pytest

from conftest import Q1_CORRECT
from metrics import record

TIMER = 8.0
STAGGER = 2.5


def test_each_phone_gets_a_full_clock_from_its_own_render(server, admin, phones):
    pin = admin.create(timer=TIMER, per_round=8, reveal=2)
    a, b, c = phones(pin, 3, prefix="T", open_pages=False)

    admin.start()
    t_open = time.time()

    renders = {}
    for ph in (a, b, c):
        ph.open()
        ph.wait_question()
        renders[ph.name] = time.time()
        if ph is not c:
            time.sleep(STAGGER)

    deadlines = {ph.name: ph.deadline() for ph in (a, b, c)}
    record("timers.render_offsets_s",
           ", ".join(f"{k}=+{v - t_open:.2f}" for k, v in renders.items()))
    record("timers.deadline_offsets_s",
           ", ".join(f"{k}=+{v - t_open:.2f}" for k, v in deadlines.items()))

    for ph in (a, b, c):
        own = deadlines[ph.name] - renders[ph.name]
        assert abs(own - TIMER) < 0.6, (
            f"{ph.name} got {own:.2f}s, not her own full {TIMER}s clock")

    # ...and they are genuinely staggered, not one shared clock.
    assert deadlines[c.name] - deadlines[a.name] > 2 * STAGGER - 0.8
    # ...and none of them exceeds the ceiling.
    ceiling = t_open + TIMER + 10
    for name, dl in deadlines.items():
        assert dl <= ceiling + 0.1, f"{name} got a deadline past the ceiling"


def test_the_barrier_waits_for_the_last_phone_to_render(server, admin, phones):
    pin = admin.create(timer=TIMER, per_round=8, reveal=2)
    a, b, c = phones(pin, 3, prefix="U", open_pages=False)

    admin.start()
    t_open = time.time()
    a.open(); a.wait_question(); a.click_option(Q1_CORRECT)
    time.sleep(STAGGER)
    b.open(); b.wait_question(); b.click_option(Q1_CORRECT)
    time.sleep(STAGGER)
    c.open(); c.wait_question()          # she renders late and never answers
    dl_c = c.deadline()
    dl_a = a.deadline()

    s, seen = admin.wait_phase_change("question", timeout=25)
    record("timers.release_vs_last_phone_deadline_s", round(seen - dl_c, 3))
    assert s["phase"] == "reveal"
    assert seen > dl_a + 2 * STAGGER - 1.0, (
        "the room released on the FIRST phone's clock, not the last one's")
    assert dl_c - 0.05 <= seen <= dl_c + 1.2, (
        f"released {seen - dl_c:+.2f}s from the last phone's own deadline")
    assert seen < t_open + TIMER + 10 - 1, "it fell through to the ceiling"


def test_a_forged_answer_one_tick_late_is_refused(server, admin, phones):
    """Straight at the API, no UI involved. HOW-IT-WORKS section 10."""
    pin = admin.create(timer=3, per_round=8, reveal=2)
    forger, = phones(pin, 1, prefix="X", open_pages=False)
    # A second phone that never renders holds the barrier open to the ceiling,
    # so the question is still live when the forged answer lands.
    phones(pin, 1, prefix="Y", open_pages=False)

    admin.start()
    assert forger.api_rendered().status_code == 200
    dl = forger.deadline()

    time.sleep(max(0.0, dl - time.time()) + 0.35)
    r = forger.api_answer(Q1_CORRECT)
    record("timers.late_post_offset_s", round(time.time() - dl, 3))
    assert admin.state()["phase"] == "question", (
        "the question closed before the forged answer — inconclusive run")
    assert r.status_code == 409, f"late answer accepted: {r.status_code} {r.text}"
    assert "late" in r.json()["detail"].lower()

    admin.end_question()
    s = admin.wait_phase("reveal", timeout=10)
    assert s["reveal"]["deltas"][forger.pid] == 0


def test_an_answer_just_inside_the_deadline_is_accepted(server, admin, phones):
    pin = admin.create(timer=4, per_round=8, reveal=2)
    ph, = phones(pin, 1, prefix="Z", open_pages=False)
    phones(pin, 1, prefix="Q", open_pages=False)     # holds the barrier open

    admin.start()
    ph.api_rendered()
    dl = ph.deadline()
    time.sleep(max(0.0, dl - time.time() - 0.4))
    r = ph.api_answer(Q1_CORRECT)
    assert r.status_code == 200, f"an in-time answer was refused: {r.text}"
    admin.end_question()
    s = admin.wait_phase("reveal", timeout=10)
    pts = s["reveal"]["deltas"][ph.pid]
    record("timers.last_second_points", pts)
    assert 600 <= pts <= 680, f"a last-second correct answer scored {pts}"
