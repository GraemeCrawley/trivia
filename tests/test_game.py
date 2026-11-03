"""Game orchestration — docs/HOW-IT-WORKS.md sections 2 and 4.

The state machine that sits on top of engine.py: joining, the lobby, running a
question under the barrier, revealing, advancing, and rounds. Clock is injected
so every timing case is deterministic.
"""

import pytest

from engine import Question
from game import Game, Phase

T0 = 1000.0


def questions(n=4):
    return [
        Question(id=f"q{i}", type="true_false", prompt=f"Q{i}?", correct=True)
        for i in range(n)
    ]


def new_game(qs=None, timer=15.0, per_round=2, reveal=5.0):
    return Game(
        questions=qs if qs is not None else questions(4),
        timer_seconds=timer,
        questions_per_round=per_round,
        reveal_seconds=reveal,
        now=T0,
    )


class TestJoining:
    def test_a_new_game_has_a_four_digit_pin(self):
        pin = new_game().pin
        assert len(pin) == 4 and pin.isdigit()

    def test_joining_returns_a_token_that_identifies_the_player(self):
        g = new_game()
        p = g.join("Alice", now=T0)
        assert g.player_by_token(p.token) is p

    def test_two_players_get_different_tokens(self):
        g = new_game()
        assert g.join("Alice", now=T0).token != g.join("Bob", now=T0).token

    def test_players_start_on_zero(self):
        assert new_game().join("Alice", now=T0).score == 0

    def test_a_game_starts_in_the_lobby(self):
        assert new_game().phase is Phase.LOBBY


class TestStarting:
    def test_starting_opens_the_first_question(self):
        g = new_game()
        g.join("Alice", now=T0)
        g.start(now=T0)
        assert g.phase is Phase.QUESTION
        assert g.current_question.id == "q0"

    def test_the_question_clock_does_not_start_until_a_phone_renders_it(self):
        g = new_game()
        a = g.join("Alice", now=T0)
        g.start(now=T0)
        assert g.state_for(a).deadline is None

    def test_rendering_gives_that_phone_its_own_full_clock(self):
        g = new_game(timer=15.0)
        a = g.join("Alice", now=T0)
        g.start(now=T0)
        g.mark_rendered(a, now=T0 + 3)
        assert g.state_for(a).deadline == T0 + 18


class TestTheBarrierHoldsScores:
    def setup_method(self):
        self.g = new_game()
        self.a = self.g.join("Alice", now=T0)
        self.b = self.g.join("Bob", now=T0)
        self.g.start(now=T0)
        self.g.mark_rendered(self.a, now=T0)
        self.g.mark_rendered(self.b, now=T0)

    def test_answering_does_not_move_your_score_while_others_are_thinking(self):
        self.g.submit(self.a, True, now=T0 + 1)
        assert self.a.score == 0
        assert self.g.phase is Phase.QUESTION

    def test_scores_land_together_once_everyone_has_answered(self):
        self.g.submit(self.a, True, now=T0 + 1)
        self.g.submit(self.b, True, now=T0 + 2)
        self.g.tick(now=T0 + 2)
        assert self.g.phase is Phase.REVEAL
        assert self.a.score > 0 and self.b.score > 0

    def test_answering_faster_scores_more(self):
        self.g.submit(self.a, True, now=T0 + 1)
        self.g.submit(self.b, True, now=T0 + 10)
        self.g.tick(now=T0 + 10)
        assert self.a.score > self.b.score

    def test_a_wrong_answer_scores_nothing(self):
        self.g.submit(self.a, True, now=T0 + 1)
        self.g.submit(self.b, False, now=T0 + 1)
        self.g.tick(now=T0 + 1)
        assert self.b.score == 0

    def test_a_player_who_never_answers_scores_nothing_and_the_room_moves_on(self):
        self.g.submit(self.a, True, now=T0 + 1)
        self.g.tick(now=T0 + 16)
        assert self.g.phase is Phase.REVEAL
        assert self.b.score == 0

    def test_an_answer_after_your_own_deadline_is_rejected(self):
        with pytest.raises(ValueError):
            self.g.submit(self.a, True, now=T0 + 16)

    def test_you_cannot_answer_the_same_question_twice(self):
        self.g.submit(self.a, True, now=T0 + 1)
        with pytest.raises(ValueError):
            self.g.submit(self.a, False, now=T0 + 2)

    def test_the_organizer_can_end_the_question_early(self):
        self.g.submit(self.a, True, now=T0 + 1)
        self.g.end_question(now=T0 + 3)
        assert self.g.phase is Phase.REVEAL
        assert self.a.score > 0
        assert self.b.score == 0

    def test_the_bubble_counter_tracks_who_is_in(self):
        assert self.g.progress() == (0, 2)
        self.g.submit(self.a, True, now=T0 + 1)
        assert self.g.progress() == (1, 2)


class TestAdvancing:
    def setup_method(self):
        self.g = new_game(per_round=2)
        self.a = self.g.join("Alice", now=T0)
        self.g.start(now=T0)
        self.g.mark_rendered(self.a, now=T0)

    def answer_current(self, t):
        self.g.submit(self.a, True, now=t)
        self.g.tick(now=t)

    def test_the_reveal_holds_before_the_next_question(self):
        self.answer_current(T0 + 1)
        self.g.tick(now=T0 + 2)
        assert self.g.phase is Phase.REVEAL

    def test_the_next_question_opens_after_the_reveal(self):
        self.answer_current(T0 + 1)
        self.g.tick(now=T0 + 10)
        assert self.g.phase is Phase.QUESTION
        assert self.g.current_question.id == "q1"

    def test_the_round_leaderboard_appears_after_the_last_question_of_a_round(self):
        self.answer_current(T0 + 1)
        self.g.tick(now=T0 + 10)
        self.g.mark_rendered(self.a, now=T0 + 10)
        self.answer_current(T0 + 11)
        self.g.tick(now=T0 + 20)
        assert self.g.phase is Phase.ROUND_LEADERBOARD

    def test_the_organizer_advances_to_the_next_round(self):
        self.answer_current(T0 + 1)
        self.g.tick(now=T0 + 10)
        self.g.mark_rendered(self.a, now=T0 + 10)
        self.answer_current(T0 + 11)
        self.g.tick(now=T0 + 20)
        self.g.next_round(now=T0 + 25)
        assert self.g.phase is Phase.QUESTION
        assert self.g.round_index == 1

    def test_the_game_ends_itself_after_the_last_question(self):
        # No organizer tap. "Next round" as the button that finishes a game was
        # the wrong label at the only moment it mattered.
        g = new_game(qs=questions(2), per_round=2)
        a = g.join("Alice", now=T0)
        g.start(now=T0)
        g.mark_rendered(a, now=T0)
        g.submit(a, True, now=T0 + 1)
        g.tick(now=T0 + 1)
        g.tick(now=T0 + 10)
        g.mark_rendered(a, now=T0 + 10)
        g.submit(a, True, now=T0 + 11)
        g.tick(now=T0 + 11)
        g.tick(now=T0 + 20)
        assert g.phase is Phase.FINAL


class TestLateJoiners:
    def test_someone_joining_mid_question_is_seated_for_the_next_one(self):
        g = new_game()
        a = g.join("Alice", now=T0)
        g.start(now=T0)
        g.mark_rendered(a, now=T0)
        late = g.join("Latecomer", now=T0 + 5)
        assert g.state_for(late).seated is True

    def test_a_seated_player_does_not_hold_up_the_reveal(self):
        g = new_game()
        a = g.join("Alice", now=T0)
        g.start(now=T0)
        g.mark_rendered(a, now=T0)
        g.join("Latecomer", now=T0 + 5)
        g.submit(a, True, now=T0 + 6)
        g.tick(now=T0 + 6)
        assert g.phase is Phase.REVEAL

    def test_a_seated_player_plays_the_next_question_normally(self):
        g = new_game()
        a = g.join("Alice", now=T0)
        g.start(now=T0)
        g.mark_rendered(a, now=T0)
        late = g.join("Latecomer", now=T0 + 5)
        g.submit(a, True, now=T0 + 6)
        g.tick(now=T0 + 6)
        g.tick(now=T0 + 15)
        assert g.phase is Phase.QUESTION
        assert g.state_for(late).seated is False


class TestLeaderboard:
    def test_the_leaderboard_is_ordered_by_score(self):
        g = new_game()
        a = g.join("Alice", now=T0)
        b = g.join("Bob", now=T0)
        g.start(now=T0)
        g.mark_rendered(a, now=T0)
        g.mark_rendered(b, now=T0)
        g.submit(a, True, now=T0 + 1)
        g.submit(b, True, now=T0 + 9)
        g.tick(now=T0 + 9)
        assert [p.name for p in g.leaderboard()] == ["Alice", "Bob"]


class TestEmptyLobby:
    def test_starting_with_nobody_in_the_room_is_refused(self):
        # Rather than opening a question no one can answer. The organizer sees
        # a clear error instead of a game that silently eats its own deck.
        g = new_game()
        with pytest.raises(ValueError):
            g.start(now=T0)

    def test_the_game_stays_in_the_lobby_after_a_refused_start(self):
        g = new_game()
        try:
            g.start(now=T0)
        except ValueError:
            pass
        assert g.phase is Phase.LOBBY

    def test_starting_works_as_soon_as_one_person_has_joined(self):
        g = new_game()
        g.join("Alice", now=T0)
        g.start(now=T0)
        assert g.phase is Phase.QUESTION
