"""Pure game logic: scoring and the per-question barrier.

Deliberately free of FastAPI, SQLAlchemy and wall-clock reads — every function
takes the numbers it needs. That is what lets the barrier, which is the part
most likely to be wrong and the hardest to reproduce at a bar, be tested
exhaustively in milliseconds. See docs/HOW-IT-WORKS.md sections 2 and 7.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

# Free-text fuzzy-match bands (HOW-IT-WORKS section 7). At or above ACCEPT it simply
# counts. Between REVIEW and ACCEPT it counts *and* is flagged for the
# organizer, who can flip it either way. Below REVIEW it is wrong.
FREE_TEXT_ACCEPT = 0.85
FREE_TEXT_REVIEW = 0.70

BASE_POINTS = 600
SPEED_POINTS = 400

# How long past the nominal question window the room will EVER wait. A phone
# that never renders the question at all — dead battery, owner outside having a
# cigarette — has no deadline of its own, so without this the barrier would
# wait forever and thirty people would stare at a frozen screen. See
# docs/HOW-IT-WORKS.md section 2.
CEILING_GRACE_SECONDS = 10.0


@dataclass
class Question:
    id: str
    type: str
    prompt: str
    correct: Any = None
    options: list | None = None
    span: float | None = None
    image: str | None = None


def speed_fraction(elapsed: float, timer_seconds: float) -> float:
    """How much of this player's own clock was still left when she answered.

    Clamped to [0, 1]: a late answer must score zero, never a negative bonus
    that eats into the base points.
    """
    if timer_seconds <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (elapsed / timer_seconds)))


def points_for(accuracy: float, speed: float) -> int:
    return round(accuracy * (BASE_POINTS + SPEED_POINTS * speed))


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"^(the|a|an)\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def free_text_ratio(given: str, accepted: list[str]) -> float:
    given_n = _normalise(given)
    if not given_n:
        return 0.0
    return max(
        SequenceMatcher(None, given_n, _normalise(a)).ratio() for a in accepted
    )


def _accepted(question: Question) -> list[str]:
    accepted = question.correct
    return [accepted] if isinstance(accepted, str) else list(accepted)


def needs_review(question: Question, given: Any) -> bool:
    """Did this one score on a judgement call the organizer should see?

    Only free text is ever ambiguous; every other type is an equality check or
    a distance, and putting those in her queue would just bury the real cases.
    """
    if question.type != "free_text" or given is None:
        return False
    ratio = free_text_ratio(str(given), _accepted(question))
    return FREE_TEXT_REVIEW <= ratio < FREE_TEXT_ACCEPT


def accuracy_of(question: Question, given: Any) -> float:
    """How right this answer is, in [0, 1]. No answer is always 0."""
    if given is None:
        return 0.0

    if question.type == "number_wheel":
        span = question.span or 1.0
        return max(0.0, 1.0 - abs(float(given) - float(question.correct)) / span)

    if question.type == "free_text":
        # Generous on purpose: anything from REVIEW upwards scores, so a thumb
        # slip does not cost the point. The looser half of that range is also
        # flagged for the organizer, who can revoke it (see needs_review).
        return 1.0 if free_text_ratio(str(given), _accepted(question)) >= FREE_TEXT_REVIEW else 0.0

    # multiple_choice, true_false, and pick_player when it has a designated
    # answer: a plain equality check.
    return 1.0 if given == question.correct else 0.0


def deadline_for(rendered_at: float, opened_at: float, timer_seconds: float,
                 grace: float = CEILING_GRACE_SECONDS) -> float:
    """This phone's own deadline: a full clock from when IT rendered.

    A guest on bad cell data who takes three seconds to load still gets her
    whole fifteen seconds rather than inheriting a clock that started without
    her — that is the per-phone timer the barrier has to preserve. Clamped to
    the ceiling so an absurdly late render cannot extend the question for
    everyone else.
    """
    return min(rendered_at + timer_seconds, opened_at + timer_seconds + grace)


@dataclass
class PlayerAnswerState:
    player_id: str
    deadline: float | None = None      # None -> this phone never rendered it
    submitted_at: float | None = None
    seated: bool = False               # joined mid-question; plays from the next one
    answer: Any = None

    @property
    def answered(self) -> bool:
        return self.submitted_at is not None

    def expired_at(self, now: float) -> bool:
        return self.deadline is not None and now > self.deadline


@dataclass
class QuestionBarrier:
    """Holds every score still until the whole room has gone."""

    opened_at: float
    timer_seconds: float
    ceiling_grace: float = CEILING_GRACE_SECONDS
    admin_ended: bool = False
    states: dict[str, PlayerAnswerState] = field(default_factory=dict)

    @property
    def ceiling(self) -> float:
        return self.opened_at + self.timer_seconds + self.ceiling_grace

    def active(self) -> list[PlayerAnswerState]:
        """Everyone this question is actually waiting on.

        Seated players — those who joined after the question opened — are
        excluded: dropping them into a question already in flight would either
        steal their clock or extend everyone else's.
        """
        return [s for s in self.states.values() if not s.seated]

    def progress(self) -> tuple[int, int]:
        """(answered, total) for the bubble counter: '17 of 24 in'."""
        active = self.active()
        return sum(1 for s in active if s.answered), len(active)

    def released_at(self, now: float) -> str | None:
        """Why the barrier is open, or None if the room is still waiting.

        Order matters only for the label we report, not for correctness.
        """
        if self.admin_ended:
            return "admin_ended"

        active = self.active()

        # An empty room never releases on its own. "Everyone has answered" is
        # vacuously true over an empty list, which would march the game through
        # a question every reveal with nobody playing — so a guest who joins a
        # minute later lands halfway down the deck. Only the organizer can move
        # an empty room.
        if not active:
            return None

        if all(s.answered for s in active):
            return "all_answered"

        if now > self.ceiling:
            return "ceiling"

        if all(s.answered or s.expired_at(now) for s in active):
            return "deadlines_passed"

        return None


def consensus_points(picks: dict[str, Any]) -> dict[str, int]:
    """Score a pick_player question by agreement.

    Players who did not answer score zero and are excluded from the
    denominator, so two players who agreed are still unanimous between
    themselves even when a third never voted.
    """
    answered = {pid: choice for pid, choice in picks.items() if choice is not None}
    total = len(answered)
    if total == 0:
        return {pid: 0 for pid in picks}

    tally: dict[Any, int] = {}
    for choice in answered.values():
        tally[choice] = tally.get(choice, 0) + 1

    return {
        pid: (round(1000 * tally[picks[pid]] / total) if picks[pid] is not None else 0)
        for pid in picks
    }
