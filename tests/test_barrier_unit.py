"""The per-question barrier — docs/HOW-IT-WORKS.md section 2.

No score moves until every player has gone. The barrier releases when all have
answered, or all remaining deadlines have passed, or the organizer ends the
question, or a hard ceiling is reached so one dead phone cannot hold thirty
people hostage.

Time is passed in explicitly everywhere: these are the cases that are miserable
to reproduce in a bar and trivial to reproduce here.
"""

from engine import (
    QuestionBarrier,
    PlayerAnswerState,
    deadline_for,
)

TIMER = 15.0
GRACE = 10.0
OPENED = 1000.0


def barrier(states, admin_ended=False):
    return QuestionBarrier(
        opened_at=OPENED,
        timer_seconds=TIMER,
        ceiling_grace=GRACE,
        admin_ended=admin_ended,
        states=states,
    )


def player(pid, rendered_at=OPENED, submitted_at=None, seated=False):
    return PlayerAnswerState(
        player_id=pid,
        deadline=(
            None if rendered_at is None
            else deadline_for(rendered_at, OPENED, TIMER, GRACE)
        ),
        submitted_at=submitted_at,
        seated=seated,
    )


class TestPerPhoneDeadlines:
    def test_a_phone_gets_its_full_clock_from_its_own_render(self):
        # Loaded 3s late on bad cell data -> still gets the whole 15 seconds.
        assert deadline_for(OPENED + 3, OPENED, TIMER, GRACE) == OPENED + 18

    def test_deadlines_are_independent_between_phones(self):
        fast = deadline_for(OPENED, OPENED, TIMER, GRACE)
        slow = deadline_for(OPENED + 4, OPENED, TIMER, GRACE)
        assert slow - fast == 4

    def test_a_very_late_render_is_clamped_to_the_ceiling(self):
        # Otherwise a phone that loads at +60s would extend the question to +75
        # and the room would wait a minute and a quarter.
        assert deadline_for(OPENED + 60, OPENED, TIMER, GRACE) == OPENED + TIMER + GRACE


class TestBarrierHolds:
    def test_holds_while_one_player_is_still_thinking(self):
        b = barrier({
            "a": player("a", submitted_at=OPENED + 2),
            "b": player("b"),
        })
        assert b.released_at(now=OPENED + 5) is None

    def test_holds_even_when_everyone_but_one_has_answered(self):
        states = {f"p{i}": player(f"p{i}", submitted_at=OPENED + 1) for i in range(29)}
        states["slow"] = player("slow")
        assert barrier(states).released_at(now=OPENED + 10) is None


class TestBarrierReleases:
    def test_releases_immediately_when_everyone_has_answered(self):
        b = barrier({
            "a": player("a", submitted_at=OPENED + 2),
            "b": player("b", submitted_at=OPENED + 3),
        })
        assert b.released_at(now=OPENED + 3) == "all_answered"

    def test_releases_when_the_last_stragglers_own_deadline_passes(self):
        b = barrier({
            "a": player("a", submitted_at=OPENED + 2),
            "b": player("b"),
        })
        assert b.released_at(now=OPENED + 14) is None
        assert b.released_at(now=OPENED + 16) == "deadlines_passed"

    def test_waits_for_the_latest_deadline_not_the_earliest(self):
        # The whole point of per-phone timers under a barrier.
        b = barrier({
            "early": player("early", rendered_at=OPENED),
            "late": player("late", rendered_at=OPENED + 5),
        })
        assert b.released_at(now=OPENED + 16) is None
        assert b.released_at(now=OPENED + 21) == "deadlines_passed"

    def test_organizer_can_end_the_question_at_any_time(self):
        b = barrier({"a": player("a"), "b": player("b")}, admin_ended=True)
        assert b.released_at(now=OPENED + 1) == "admin_ended"

    def test_a_phone_that_never_loaded_cannot_stall_past_the_ceiling(self):
        b = barrier({
            "a": player("a", submitted_at=OPENED + 2),
            "dead": player("dead", rendered_at=None),
        })
        assert b.released_at(now=OPENED + 20) is None
        assert b.released_at(now=OPENED + 26) == "ceiling"

    def test_an_empty_room_does_NOT_release(self):
        # "Everyone has answered" is vacuously true over an empty list, so this
        # would advance a question every reveal with nobody playing — the
        # organizer taps START while guests are still walking over and the deck
        # burns down before anyone sees a question.
        assert barrier({}).released_at(now=OPENED + 1) is None

    def test_an_empty_room_does_not_release_at_the_ceiling_either(self):
        assert barrier({}).released_at(now=OPENED + 999) is None

    def test_the_organizer_can_still_force_an_empty_room_along(self):
        assert barrier({}, admin_ended=True).released_at(now=OPENED + 1) == "admin_ended"


class TestSeatedPlayers:
    def test_a_mid_question_joiner_does_not_hold_the_barrier(self):
        b = barrier({
            "a": player("a", submitted_at=OPENED + 2),
            "newcomer": player("newcomer", rendered_at=None, seated=True),
        })
        assert b.released_at(now=OPENED + 3) == "all_answered"

    def test_a_seated_player_is_not_counted_as_answered_either(self):
        b = barrier({
            "a": player("a"),
            "newcomer": player("newcomer", rendered_at=None, seated=True),
        })
        assert b.released_at(now=OPENED + 3) is None

    def test_answered_count_ignores_seated_players(self):
        b = barrier({
            "a": player("a", submitted_at=OPENED + 1),
            "b": player("b"),
            "newcomer": player("newcomer", rendered_at=None, seated=True),
        })
        assert b.progress() == (1, 2)
