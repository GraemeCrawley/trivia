"""Crash recovery — docs/HOW-IT-WORKS.md section 5.

The worst failure at the bar is the container restarting mid-question. The game
must come back with every score and every player intact, not start over with
thirty people watching.
"""

import json

import pytest

from engine import Question
from game import Game, Phase
import persist

T0 = 1000.0


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(persist, "STATE_FILE", tmp_path / "state.json")


def running_game():
    qs = [Question(id=f"q{i}", type="true_false", prompt="?", correct=True)
          for i in range(4)]
    g = Game(questions=qs, timer_seconds=15.0, questions_per_round=2, now=T0)
    a = g.join("Alice", now=T0)
    b = g.join("Bob", now=T0)
    g.start(now=T0)
    g.mark_rendered(a, now=T0)
    g.mark_rendered(b, now=T0)
    return g, a, b


class TestRoundTrip:
    def test_a_saved_game_comes_back(self):
        g, _, _ = running_game()
        persist.save(g)
        assert persist.load(g.questions) is not None

    def test_scores_survive(self):
        g, a, b = running_game()
        g.submit(a, True, now=T0 + 1)
        g.submit(b, True, now=T0 + 9)
        g.tick(now=T0 + 9)
        persist.save(g)
        back = persist.load(g.questions)
        assert {p.name: p.score for p in back.players.values()} == \
               {p.name: p.score for p in g.players.values()}

    def test_players_keep_their_tokens_so_phones_reconnect_as_themselves(self):
        g, a, _ = running_game()
        persist.save(g)
        back = persist.load(g.questions)
        assert back.player_by_token(a.token).name == "Alice"

    def test_the_pin_survives_so_late_joiners_can_still_get_in(self):
        g, _, _ = running_game()
        persist.save(g)
        assert persist.load(g.questions).pin == g.pin

    def test_position_in_the_game_survives(self):
        g, a, b = running_game()
        g.submit(a, True, now=T0 + 1)
        g.submit(b, True, now=T0 + 1)
        g.tick(now=T0 + 1)
        g.tick(now=T0 + 10)           # past the reveal, onto question 2
        persist.save(g)
        back = persist.load(g.questions)
        assert back.question_index == g.question_index
        assert back.round_index == g.round_index
        assert back.phase is g.phase

    def test_an_in_flight_barrier_survives_with_who_had_answered(self):
        g, a, _ = running_game()
        g.submit(a, True, now=T0 + 1)
        persist.save(g)
        back = persist.load(g.questions)
        assert back.progress() == (1, 2)

    def test_selfies_survive(self):
        g, a, _ = running_game()
        a.selfie_id = "abc123"
        persist.save(g)
        back = persist.load(g.questions)
        assert back.player_by_token(a.token).selfie_id == "abc123"


class TestFailsSafely:
    def test_no_saved_game_loads_as_nothing(self):
        assert persist.load([]) is None

    def test_a_corrupt_state_file_loads_as_nothing_rather_than_crashing(self):
        # A half-written file from a crash mid-save must not stop the app from
        # booting — losing the game is bad, refusing to start is worse.
        persist.STATE_FILE.write_text("{not json")
        assert persist.load([]) is None

    def test_saving_never_raises_even_if_the_path_is_unusable(self, monkeypatch):
        monkeypatch.setattr(persist, "STATE_FILE", persist.Path("/proc/nope/state.json"))
        g, _, _ = running_game()
        persist.save(g)   # must not raise
