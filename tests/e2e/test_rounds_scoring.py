"""A whole three-round game, with every score checked independently.

Expected points come from tests/e2e/expected.py, written from HOW-IT-WORKS section 7
and importing nothing from the app. Because a player's speed bonus depends on
when the server timestamped her POST, each score is checked against the band
[points at send, points at send+RTT] rather than a single number — the band is
a couple of points wide on loopback.
"""

import json
import time
from pathlib import Path

import pytest

import expected as X
from metrics import record

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = json.loads((ROOT / "app" / "seed" / "questions.json").read_text())["questions"]
BY_ID = {q["id"]: q for q in QUESTIONS}

TIMER = 12.0
REVEAL = 2.0
PER_ROUND = 8
NPLAYERS = 4


def plan_for(q, phones, idx):
    """Deterministic, deliberately mixed answers. Returns {player_id: value}."""
    t = q["type"]
    if t == "multiple_choice":
        wrong = next(o for o in q["options"] if o != q["correct"])
        return {p.pid: (q["correct"] if i < 3 else wrong)
                for i, p in enumerate(phones)}
    if t == "true_false":
        return {p.pid: (q["correct"] if i < 2 else not q["correct"])
                for i, p in enumerate(phones)}
    if t == "number_wheel":
        c, span = q["correct"], q["span"]
        lo, hi = q["min"], q["max"]
        offsets = [0, span / 2, span * 3, -1]
        return {p.pid: max(lo, min(hi, round(c + offsets[i])))
                for i, p in enumerate(phones)}
    if t == "free_text":
        acc = q["correct"] if isinstance(q["correct"], list) else [q["correct"]]
        vals = [acc[0], acc[-1], "definitely not that", acc[0]]
        return {p.pid: vals[i] for i, p in enumerate(phones)}
    if t == "pick_player":
        target, backup = phones[0], phones[1]
        return {p.pid: (backup.pid if p.pid == target.pid else target.pid)
                for p in phones}
    raise AssertionError(f"unknown question type {t}")


def submit(ph, q, value):
    page, t = ph.page, q["type"]
    n = page.evaluate("() => window.__answerCalls.length")
    if t == "multiple_choice":
        page.click(f'#q-options button[data-value="{value}"]')
    elif t == "true_false":
        page.click(f'#q-truefalse [data-value="{str(value).lower()}"]')
    elif t == "number_wheel":
        page.evaluate("""v => {
            const i = document.getElementById('wheel-input');
            i.value = String(v);
            i.dispatchEvent(new Event('input'));
        }""", value)
        page.click("#wheel-submit")
    elif t == "free_text":
        page.fill("#q-text-input", str(value))
        page.click("#q-text-submit")
    elif t == "pick_player":
        page.click(f'#q-players .bubble[data-value="{value}"]')
    page.wait_for_function("n => window.__answerCalls.length > n", arg=n,
                           timeout=10000)
    return page.evaluate("() => window.__answerCalls.at(-1)")


def band(q, value, deadline, call):
    """[lo, hi] points this answer may legitimately be worth."""
    acc = X.accuracy(q["type"], q.get("correct"), q.get("span"), value)
    if acc == 0:
        return 0, 0
    render_at = deadline - TIMER
    e_lo = call["sent_at"] / 1000.0 - render_at
    e_hi = e_lo + call["rtt_ms"] / 1000.0
    hi = X.points(acc, X.speed_fraction(e_lo, TIMER)) + 1
    lo = X.points(acc, X.speed_fraction(e_hi, TIMER)) - 1
    return lo, hi


def lanes(page, container_id):
    return [tuple(x) for x in page.eval_on_selector_all(
        f"#{container_id} .lane",
        """els => els.map(e => [e.querySelector('.hname').textContent,
                                Number(e.querySelector('.hscore').textContent)])""")]


@pytest.mark.slow
def test_full_three_round_game(server, admin, phones):
    pin = admin.create(timer=TIMER, per_round=PER_ROUND, reveal=REVEAL)
    ps = phones(pin, NPLAYERS, prefix="R")
    totals = {p.pid: 0 for p in ps}
    names = {p.pid: p.name for p in ps}
    checked = 0

    admin.start()
    for qi, qrec in enumerate(QUESTIONS):
        s = admin.wait_phase("question", timeout=25)
        assert s["question"]["id"] == qrec["id"], (
            f"expected {qrec['id']}, room is on {s['question']['id']}")

        for p in ps:
            p.wait_question(timeout=20000)
            assert p.page.inner_text("#q-prompt") == qrec["prompt"]

        deadlines = {p.pid: p.deadline() for p in ps}
        values = plan_for(qrec, ps, qi)
        calls = {p.pid: submit(p, qrec, values[p.pid]) for p in ps}
        for pid, c in calls.items():
            assert c["status"] == 200, f"{names[pid]} answer refused: {c}"

        rev, _ = admin.wait_phase_change("question", timeout=25)
        assert rev["phase"] == "reveal", rev["phase"]
        deltas = rev["deltas"] if "deltas" in rev else rev["reveal"]["deltas"]

        if qrec["type"] == "pick_player":
            want = X.consensus({p.pid: values[p.pid] for p in ps})
            assert deltas == want, (
                f"{qrec['id']} consensus: got {deltas}, expected {want}")
            checked += len(ps)
        else:
            for p in ps:
                lo, hi = band(qrec, values[p.pid], deadlines[p.pid], calls[p.pid])
                got = deltas[p.pid]
                assert lo <= got <= hi, (
                    f"{qrec['id']} ({qrec['type']}) {p.name} answered "
                    f"{values[p.pid]!r} and scored {got}, expected {lo}..{hi}")
                checked += 1

        for pid, d in deltas.items():
            totals[pid] += d

        last_question_of_the_game = (qi + 1) == len(QUESTIONS)
        if (qi + 1) % PER_ROUND == 0 and not last_question_of_the_game:
            # The FINAL round does not stop on a leaderboard: the game ends
            # itself rather than making the organizer press "Next round" to
            # finish a game that is already over. Every earlier round does
            # wait for her.
            s = admin.wait_phase("round_leaderboard", timeout=25)
            want = sorted(totals.items(), key=lambda kv: (-kv[1], names[kv[0]]))
            got = [(e["name"], e["score"]) for e in s["leaderboard"]]
            assert got == [(names[pid], sc) for pid, sc in want], (
                f"round {qi // PER_ROUND + 1} leaderboard: {got} != {want}")
            ps[0].page.wait_for_selector("#screen-leaderboard:not([hidden])",
                                         timeout=10000)
            ui = lanes(ps[0].page, "leaderboard")
            assert ui == got, f"round leaderboard on the phone: {ui} != {got}"
            admin.next_round()

    s = admin.wait_phase("final", timeout=25)
    want = sorted(totals.items(), key=lambda kv: (-kv[1], names[kv[0]]))
    got = [(e["name"], e["score"]) for e in s["leaderboard"]]
    assert got == [(names[pid], sc) for pid, sc in want]
    ps[0].page.wait_for_selector("#screen-final:not([hidden])", timeout=10000)
    assert lanes(ps[0].page, "final-leaderboard") == got

    record("rounds.questions_played", len(QUESTIONS))
    record("rounds.individual_scores_checked", checked)
    record("rounds.final_leaderboard", "; ".join(f"{n}={s}" for n, s in got))
    assert s["round"]["total"] == 3


def test_free_text_review_band_scores_and_is_flagged(server, admin, phones):
    """HOW-IT-WORKS section 7: 0.70-0.85 scores AND is flagged for the organizer."""
    pin = admin.create(timer=20, per_round=8, reveal=1)
    ps = phones(pin, 2, prefix="FT")
    admin.start()
    admin.skip_to("r3q6")                      # the one-word free-text question

    for p in ps:
        p.wait_question(timeout=15000)
    ps[0].page.fill("#q-text-input", "otawwa")     # ratio 0.833: in the review band
    ps[0].page.click("#q-text-submit")
    ps[1].page.fill("#q-text-input", "ottawa")      # ratio 1.0
    ps[1].page.click("#q-text-submit")

    rev, _ = admin.wait_phase_change("question", timeout=20)
    deltas = rev["reveal"]["deltas"]
    record("scoring.review_band_points", deltas[ps[0].pid])
    assert deltas[ps[0].pid] > 0, (
        "an answer in the 0.70-0.85 review band scored zero; HOW-IT-WORKS section 7 "
        "says it scores and is flagged for the organizer to flip")
    assert deltas[ps[1].pid] > 0

    flagged = admin.state().get("flagged", [])
    mine = [f for f in flagged if f["player_id"] == ps[0].pid]
    assert mine, (
        "the review-band answer scored but was not flagged for the organizer "
        f"(queue: {flagged})")
    assert mine[0]["given"] == "otawwa"
    assert mine[0]["accepted"] is True
    assert not [f for f in flagged if f["player_id"] == ps[1].pid], (
        "an exact answer was put in the adjudication queue")
    assert "flagged" not in ps[0].state(), (
        "the review queue leaks other players' answers to a player's phone")
