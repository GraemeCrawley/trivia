"""Pause, skip and reset — the organizer's mid-game controls.

Pause is the one with teeth: a phone's deadline is an absolute timestamp, so
freezing the game means every one of those has to move, and the barrier must not
release while the room is stopped. Nobody should lose a second of their own
clock to a pause they did not ask for.
"""

import pytest

from engine import Question
from game import Game, Phase

T0 = 1000.0


def running_game(n=4, per_round=2, timer=15.0):
    qs = [Question(id=f"q{i}", type="true_false", prompt="?", correct=True)
          for i in range(n)]
    g = Game(questions=qs, timer_seconds=timer, questions_per_round=per_round,
             reveal_seconds=5.0, now=T0)
    a = g.join("Alice", now=T0)
    b = g.join("Bob", now=T0)
    g.start(now=T0)
    g.mark_rendered(a, now=T0)
    g.mark_rendered(b, now=T0)
    return g, a, b


class TestPausing:
    def test_a_paused_game_says_it_is_paused(self):
        g, _, _ = running_game()
        g.pause(now=T0 + 3)
        assert g.paused is True

    def test_the_barrier_does_not_release_while_paused(self):
        g, a, b = running_game()
        g.submit(a, True, now=T0 + 1)
        g.pause(now=T0 + 2)
        # Bob's deadline would have passed at T0+15 — it must not, while stopped.
        g.tick(now=T0 + 60)
        assert g.phase is Phase.QUESTION

    def test_resuming_gives_everyone_back_the_time_they_had(self):
        g, a, b = running_game(timer=15.0)
        before = g.state_for(b).deadline
        g.pause(now=T0 + 5)          # 10s left on Bob's clock
        g.resume(now=T0 + 65)        # stopped for a minute
        assert g.state_for(b).deadline == pytest.approx(before + 60)

    def test_a_player_can_still_answer_after_a_long_pause(self):
        g, a, b = running_game(timer=15.0)
        g.pause(now=T0 + 5)
        g.resume(now=T0 + 300)
        g.submit(b, True, now=T0 + 305)      # would be long past the old deadline
        assert g.state_for(b).answered

    def test_answering_is_refused_while_the_game_is_stopped(self):
        g, a, _ = running_game()
        g.pause(now=T0 + 2)
        with pytest.raises(ValueError):
            g.submit(a, True, now=T0 + 3)

    def test_resuming_a_running_game_changes_nothing(self):
        g, _, b = running_game()
        before = g.state_for(b).deadline
        g.resume(now=T0 + 5)
        assert g.paused is False and g.state_for(b).deadline == before

    def test_the_ceiling_moves_too_so_a_pause_cannot_skip_a_question(self):
        # Otherwise a long pause would blow straight through the hard cap and
        # reveal the moment the organizer resumed.
        g, a, _ = running_game()
        g.submit(a, True, now=T0 + 1)
        g.pause(now=T0 + 2)
        g.resume(now=T0 + 600)
        g.tick(now=T0 + 601)
        assert g.phase is Phase.QUESTION


class TestSkipping:
    def test_next_question_goes_to_the_NEXT_question_in_one_tap(self):
        # It used to stop at the reveal, which made it identical to "End this
        # question" and meant two taps to actually move on.
        g, a, _ = running_game()
        g.submit(a, True, now=T0 + 1)
        g.next_question(now=T0 + 2)
        assert a.score > 0, "answers already in must still score"
        assert g.phase is Phase.QUESTION
        assert g.current_question.id == "q1"

    def test_end_question_still_stops_at_the_reveal(self):
        # The two controls have to differ: End shows the answer, Next skips it.
        g, a, _ = running_game()
        g.submit(a, True, now=T0 + 1)
        g.end_question(now=T0 + 2)
        assert g.phase is Phase.REVEAL

    def test_next_question_on_the_last_of_a_round_lands_on_the_leaderboard(self):
        g, a, b = running_game(n=4, per_round=2)
        g.next_question(now=T0 + 1)          # through q0
        g.mark_rendered(a, now=T0 + 2)
        g.next_question(now=T0 + 3)          # through q1, end of round
        assert g.phase is Phase.ROUND_LEADERBOARD

    def test_next_question_during_a_reveal_cuts_the_wait_short(self):
        g, a, b = running_game()
        g.submit(a, True, now=T0 + 1)
        g.submit(b, True, now=T0 + 1)
        g.tick(now=T0 + 1)
        assert g.phase is Phase.REVEAL
        g.next_question(now=T0 + 2)
        assert g.phase is Phase.QUESTION
        assert g.current_question.id == "q1"

    def test_skipping_is_refused_in_the_lobby(self):
        g = Game(questions=[], timer_seconds=15.0)
        with pytest.raises(ValueError):
            g.next_question(now=T0)


class TestResetting:
    def test_reset_puts_everyone_back_in_the_lobby_on_zero(self):
        g, a, b = running_game()
        g.submit(a, True, now=T0 + 1)
        g.submit(b, True, now=T0 + 1)
        g.tick(now=T0 + 1)
        assert a.score > 0
        g.reset(now=T0 + 10)
        assert g.phase is Phase.LOBBY
        assert a.score == 0 and b.score == 0

    def test_reset_keeps_the_players_so_nobody_has_to_rejoin(self):
        g, a, b = running_game()
        g.reset(now=T0 + 10)
        assert len(g.players) == 2
        assert g.player_by_token(a.token) is not None

    def test_reset_keeps_the_pin(self):
        g, _, _ = running_game()
        pin = g.pin
        g.reset(now=T0 + 10)
        assert g.pin == pin

    def test_reset_goes_back_to_the_first_question(self):
        g, a, b = running_game()
        g.submit(a, True, now=T0 + 1); g.submit(b, True, now=T0 + 1)
        g.tick(now=T0 + 1); g.tick(now=T0 + 10)
        g.reset(now=T0 + 20)
        assert g.question_index == 0 and g.round_index == 0

    def test_reset_clears_the_review_queue(self):
        g, _, _ = running_game()
        g.flagged.append({"player_id": "x", "question_id": "q", "name": "n",
                          "given": "g", "points": 1, "accepted": True, "prompt": "p"})
        g.reset(now=T0 + 10)
        assert g.flagged == []

    def test_the_game_can_be_started_again_after_a_reset(self):
        g, _, _ = running_game()
        g.reset(now=T0 + 10)
        g.start(now=T0 + 11)
        assert g.phase is Phase.QUESTION
        assert g.current_question.id == "q0"

    def test_a_reset_game_is_not_paused(self):
        g, _, _ = running_game()
        g.pause(now=T0 + 2)
        g.reset(now=T0 + 10)
        assert g.paused is False


class TestProgress:
    def test_it_reports_the_question_within_the_round(self):
        g, _, _ = running_game(n=4, per_round=2)
        assert g.progress_label() == (1, 2, 1, 2)   # round 1 of 2, question 1 of 2

    def test_the_question_number_resets_each_round(self):
        g, a, b = running_game(n=4, per_round=2)
        for t in (1, 11):
            g.submit(a, True, now=T0 + t); g.submit(b, True, now=T0 + t)
            g.tick(now=T0 + t); g.tick(now=T0 + t + 8)
        g.next_round(now=T0 + 40)
        assert g.progress_label()[:3] == (2, 2, 1)


class TestTheGameEndsItself:
    def two_question_game(self):
        g, a, b = running_game(n=2, per_round=2)
        return g, a, b

    def answer_both(self, g, a, b, t):
        g.submit(a, True, now=t)
        g.submit(b, True, now=t)
        g.tick(now=t)

    def test_the_last_question_ends_the_game_with_no_further_taps(self):
        # It used to stop on a round leaderboard and wait for "Next round",
        # which on the final round is a button that means "finish" — so the
        # organizer had to press something to end a game that was already over.
        g, a, b = self.two_question_game()
        self.answer_both(g, a, b, T0 + 1)
        g.tick(now=T0 + 10)
        self.answer_both(g, a, b, T0 + 11)
        g.tick(now=T0 + 20)
        assert g.phase is Phase.FINAL

    def test_a_mid_game_round_still_waits_for_the_organizer(self):
        # She may want to let the room look at the leaderboard, refill drinks,
        # heckle. Only the LAST round should end on its own.
        g, a, b = running_game(n=4, per_round=2)
        self.answer_both(g, a, b, T0 + 1)
        g.tick(now=T0 + 10)
        self.answer_both(g, a, b, T0 + 11)
        g.tick(now=T0 + 20)
        assert g.phase is Phase.ROUND_LEADERBOARD

    def test_the_winner_is_the_top_scorer(self):
        g, a, b = self.two_question_game()
        g.submit(a, True, now=T0 + 1)
        g.submit(b, True, now=T0 + 9)
        g.tick(now=T0 + 9)
        assert g.winner_ids() == [a.id]

    def test_a_tie_has_more_than_one_winner(self):
        g, a, b = self.two_question_game()
        g.submit(a, True, now=T0 + 1)
        g.submit(b, True, now=T0 + 1)
        g.tick(now=T0 + 1)
        assert sorted(g.winner_ids()) == sorted([a.id, b.id])

    def test_nobody_wins_a_game_where_nobody_scored(self):
        g, a, b = self.two_question_game()
        g.submit(a, False, now=T0 + 1)
        g.submit(b, False, now=T0 + 1)
        g.tick(now=T0 + 1)
        assert g.winner_ids() == []
