"""Scoring, re-implemented from docs/HOW-IT-WORKS.md section 7 alone.

Deliberately NOT importing app/engine.py: a test that asserts the app against
its own arithmetic asserts nothing.

    points = round( accuracy * (600 + 400 * speed) )
    speed  = fraction of THAT PLAYER'S own clock still remaining
    pick_player = round(1000 * share_of_answering_players_who_picked_the_same)
"""

import difflib
import re
import unicodedata


def speed_fraction(elapsed: float, timer_seconds: float) -> float:
    if timer_seconds <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - elapsed / timer_seconds))


def points(accuracy: float, speed: float) -> int:
    return round(accuracy * (600 + 400 * speed))


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"^(the|a|an)\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def accuracy(qtype: str, correct, span, given) -> float:
    if given is None:
        return 0.0
    if qtype == "number_wheel":
        return max(0.0, 1.0 - abs(float(given) - float(correct)) / float(span or 1.0))
    if qtype == "free_text":
        accepted = [correct] if isinstance(correct, str) else list(correct)
        g = _norm(str(given))
        if not g:
            return 0.0
        best = max(difflib.SequenceMatcher(None, g, _norm(a)).ratio()
                   for a in accepted)
        return 1.0 if best >= 0.85 else 0.0
    return 1.0 if given == correct else 0.0


def consensus(picks: dict) -> dict:
    """round(1000 * share of the ANSWERING players who picked the same)."""
    answered = {p: v for p, v in picks.items() if v is not None}
    total = len(answered)
    if total == 0:
        return {p: 0 for p in picks}
    tally: dict = {}
    for v in answered.values():
        tally[v] = tally.get(v, 0) + 1
    return {p: (round(1000 * tally[v] / total) if v is not None else 0)
            for p, v in picks.items()}
