"""pick_player consensus scoring — docs/HOW-IT-WORKS.md sections 3 and 7.

"Who is most likely to lose her phone tonight" has no correct answer, so these
score by agreement: 1000 x the share of answering players who picked the same
person you did. Picking with the crowd wins. Resolved at the reveal, which the
barrier guarantees is a moment when everyone has already gone.
"""

from engine import consensus_points


class TestConsensus:
    def test_unanimous_pick_scores_full_marks_for_everyone(self):
        picks = {"a": "z", "b": "z", "c": "z"}
        assert consensus_points(picks) == {"a": 1000, "b": 1000, "c": 1000}

    def test_majority_scores_higher_than_the_outlier(self):
        picks = {"a": "z", "b": "z", "c": "y"}
        result = consensus_points(picks)
        assert result["a"] == result["b"] == round(1000 * 2 / 3)
        assert result["c"] == round(1000 * 1 / 3)

    def test_everyone_picking_differently_scores_everyone_the_same_low_score(self):
        picks = {"a": "x", "b": "y", "c": "z"}
        assert set(consensus_points(picks).values()) == {round(1000 / 3)}

    def test_a_lone_player_agrees_with_herself(self):
        assert consensus_points({"a": "z"}) == {"a": 1000}

    def test_nobody_answered_scores_nobody(self):
        assert consensus_points({}) == {}

    def test_players_who_did_not_answer_score_zero_and_do_not_dilute_the_share(self):
        # None means she never picked. She scores 0, and the two who agreed
        # still count as unanimous between themselves.
        picks = {"a": "z", "b": "z", "c": None}
        result = consensus_points(picks)
        assert result == {"a": 1000, "b": 1000, "c": 0}
