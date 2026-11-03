"""Scoring rules — docs/HOW-IT-WORKS.md section 7.

    points = round( accuracy x (600 + 400 x speed) )

so a correct instant answer is 1000, a correct last-gasp answer is 600, and a
wrong or missing answer is 0.
"""

import pytest

from engine import Question, accuracy_of, points_for, speed_fraction

TIMER = 15.0


def mc(correct="b"):
    return Question(id="q1", type="multiple_choice", prompt="?",
                    options=["a", "b", "c", "d"], correct=correct)


def wheel(correct=50, span=20):
    return Question(id="q2", type="number_wheel", prompt="?",
                    correct=correct, span=span)


class TestSpeedFraction:
    def test_instant_answer_has_full_speed(self):
        assert speed_fraction(elapsed=0.0, timer_seconds=TIMER) == 1.0

    def test_answer_on_the_buzzer_has_no_speed_bonus(self):
        assert speed_fraction(elapsed=TIMER, timer_seconds=TIMER) == 0.0

    def test_halfway_answer_has_half_speed(self):
        assert speed_fraction(elapsed=7.5, timer_seconds=TIMER) == 0.5

    def test_late_answer_clamps_to_zero_rather_than_going_negative(self):
        # A negative speed would subtract from the base 600 and hand out
        # points for being late. It must clamp, not wrap.
        assert speed_fraction(elapsed=TIMER + 5, timer_seconds=TIMER) == 0.0


class TestExactTypes:
    def test_correct_instant_multiple_choice_scores_1000(self):
        assert points_for(accuracy_of(mc(), "b"), speed=1.0) == 1000

    def test_correct_last_moment_multiple_choice_scores_600(self):
        assert points_for(accuracy_of(mc(), "b"), speed=0.0) == 600

    def test_wrong_multiple_choice_scores_zero_however_fast(self):
        assert points_for(accuracy_of(mc(), "a"), speed=1.0) == 0

    def test_no_answer_scores_zero(self):
        assert points_for(accuracy_of(mc(), None), speed=1.0) == 0

    def test_true_false_is_scored_exactly(self):
        q = Question(id="q3", type="true_false", prompt="?", correct=True)
        assert points_for(accuracy_of(q, True), speed=1.0) == 1000
        assert points_for(accuracy_of(q, False), speed=1.0) == 0


class TestNumberWheel:
    def test_exact_guess_scores_full_marks(self):
        assert points_for(accuracy_of(wheel(), 50), speed=1.0) == 1000

    def test_guess_one_full_span_away_scores_zero(self):
        assert points_for(accuracy_of(wheel(correct=50, span=20), 70), speed=1.0) == 0

    def test_guess_beyond_the_span_clamps_to_zero_not_negative(self):
        assert points_for(accuracy_of(wheel(correct=50, span=20), 500), speed=1.0) == 0

    def test_guess_half_a_span_away_scores_half(self):
        # accuracy 0.5, instant -> 0.5 * 1000
        assert points_for(accuracy_of(wheel(correct=50, span=20), 60), speed=1.0) == 500

    def test_closeness_is_symmetric_above_and_below(self):
        q = wheel(correct=50, span=20)
        assert accuracy_of(q, 45) == accuracy_of(q, 55)


class TestFreeText:
    def make(self):
        return Question(id="q4", type="free_text", prompt="?",
                        correct=["The Beatles"])

    def test_exact_match_is_correct(self):
        assert accuracy_of(self.make(), "The Beatles") == 1.0

    def test_case_and_punctuation_are_ignored(self):
        assert accuracy_of(self.make(), "the beatles!") == 1.0

    def test_leading_article_is_ignored(self):
        assert accuracy_of(self.make(), "beatles") == 1.0

    def test_a_near_miss_typo_still_counts(self):
        assert accuracy_of(self.make(), "the beatlez") == 1.0

    def test_a_different_answer_is_wrong(self):
        assert accuracy_of(self.make(), "the rolling stones") == 0.0
