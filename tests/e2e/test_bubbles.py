"""The bubble waiting room — HOW-IT-WORKS section 9, UI-CONTRACT 'the two screens'."""

import re
import time

import pytest

from conftest import Q1_CORRECT
from metrics import record

BUBBLE_IDS = "els => els.map(e => e.dataset.playerId)"

STYLE_PROBE = """
e => {
  const s = getComputedStyle(e);
  const av = e.querySelector('.avatar');
  const a = av ? getComputedStyle(av) : {};
  return {
    opacity: s.opacity,
    check: getComputedStyle(e, '::after').content,
    avatar_transform: a.transform,
    avatar_filter: a.filter,
    avatar_border_style: a.borderTopStyle,
    avatar_border_color: a.borderTopColor,
    avatar_box_shadow: a.boxShadow,
    classes: e.className,
  };
}
"""

COLOUR_ONLY = {"avatar_border_color", "color", "background_color"}


def bubbles_on(page):
    return page.eval_on_selector_all("#bubbles .bubble", BUBBLE_IDS)


def test_waiting_room_lists_every_participant_exactly_once(server, admin, phones):
    pin = admin.create(timer=45, per_round=8, reveal=2)
    ps = phones(pin, 5, prefix="B")
    admin.start()
    for p in ps:
        p.wait_question()

    ps[0].click_option(Q1_CORRECT)
    ps[0].wait_waiting()
    ps[0].page.wait_for_selector("#bubbles .bubble", timeout=5000)

    ids = bubbles_on(ps[0].page)
    assert len(ids) == len(set(ids)), f"a participant appears twice: {ids}"
    assert sorted(ids) == sorted(p.pid for p in ps), (
        "the waiting room does not list every participant")
    assert re.match(r"^\s*1 of 5 in\s*$", ps[0].page.inner_text("#bubble-count"))


def test_a_bubble_flips_on_other_phones_within_a_second(server, admin, phones):
    pin = admin.create(timer=45, per_round=8, reveal=2)
    ps = phones(pin, 4, prefix="F")
    admin.start()
    for p in ps:
        p.wait_question()

    watcher, actor = ps[0], ps[1]
    watcher.click_option(Q1_CORRECT)
    watcher.wait_waiting()
    watcher.page.wait_for_selector(
        f'#bubbles .bubble[data-player-id="{actor.pid}"].thinking', timeout=5000)

    t0 = time.time()
    actor.click_option(Q1_CORRECT)
    watcher.page.wait_for_selector(
        f'#bubbles .bubble[data-player-id="{actor.pid}"].in', timeout=3000)
    flip = time.time() - t0
    record("bubbles.flip_latency_on_other_phone_s", round(flip, 3))
    assert flip < 1.0, f"bubble took {flip:.2f}s to flip on another phone"
    assert "2 of 4 in" in watcher.page.inner_text("#bubble-count")


def test_bubble_counter_tracks_the_answered_count(server, admin, phones):
    pin = admin.create(timer=45, per_round=8, reveal=2)
    ps = phones(pin, 4, prefix="K")
    admin.start()
    for p in ps:
        p.wait_question()
    watcher = ps[0]
    watcher.click_option(Q1_CORRECT)
    watcher.wait_waiting()

    for n, p in enumerate(ps[1:], start=2):
        if n == len(ps):
            break                      # the last one releases the barrier
        p.click_option(Q1_CORRECT)
        watcher.page.wait_for_selector(
            f'#bubbles .bubble[data-player-id="{p.pid}"].in', timeout=3000)
        counted = len(watcher.page.query_selector_all("#bubbles .bubble.in"))
        text = watcher.page.inner_text("#bubble-count")
        assert f"{n} of 4 in" in text, f"counter said {text!r}, expected {n} of 4"
        assert counted == n, f"{counted} lit bubbles but the counter said {n}"


def test_answered_and_thinking_differ_in_more_than_colour(server, admin, phones):
    pin = admin.create(timer=45, per_round=8, reveal=2)
    ps = phones(pin, 4, prefix="V")
    admin.start()
    for p in ps:
        p.wait_question()
    watcher = ps[0]
    watcher.click_option(Q1_CORRECT)
    watcher.wait_waiting()
    watcher.page.wait_for_selector("#bubbles .bubble.in", timeout=5000)
    watcher.page.wait_for_selector("#bubbles .bubble.thinking", timeout=5000)

    lit = watcher.page.locator("#bubbles .bubble.in").first.evaluate(STYLE_PROBE)
    dim = watcher.page.locator("#bubbles .bubble.thinking").first.evaluate(STYLE_PROBE)

    differing = [k for k in lit
                 if k not in ("classes",) and lit[k] != dim[k]]
    non_colour = [k for k in differing if k not in COLOUR_ONLY]
    record("bubbles.non_colour_state_cues", ", ".join(sorted(non_colour)) or "NONE")
    assert len(non_colour) >= 2, (
        "answered and thinking are distinguished by colour alone — "
        f"differing properties: {differing}\nlit={lit}\ndim={dim}")
    assert lit["opacity"] != dim["opacity"] or \
        lit["avatar_transform"] != dim["avatar_transform"], (
        "neither opacity nor size differs between the two states")


def test_bubbles_do_not_all_drift_in_unison(server, admin, phones):
    """UI-CONTRACT: 'staggered delays so they never move in unison'."""
    pin = admin.create(timer=45, per_round=8, reveal=2)
    ps = phones(pin, 3, prefix="W")
    page = ps[0].page
    page.wait_for_selector("#lobby-roster .bubble", timeout=10000)

    has_factory = page.evaluate(
        "() => typeof bargames !== 'undefined' "
        "&& typeof bargames.bubble === 'function'")
    if not has_factory:
        pytest.skip("bargames.bubble is no longer exported")

    # Player ids come from secrets.token_urlsafe, whose alphabet includes
    # '-' and '_'. Feed the renderer ids of exactly that shape.
    delays = page.evaluate("""() => {
      const ids = ["abcdef_x", "abcde-yz", "abcdefgh", "ab_cdefg"];
      return ids.map(id => {
        const el = bargames.bubble(
          {id, name: "N", initial: "N", selfie: false, answered: false});
        return [id, el.style.animationDelay];
      });
    }""")
    broken = [d for d in delays if not d[1] or "NaN" in d[1]]
    record("bubbles.ids_with_no_drift_offset", str(broken))
    assert not broken, (
        "these player ids get no drift offset at all, so those bubbles bob in "
        f"lockstep with every other un-offset bubble: {broken}")
