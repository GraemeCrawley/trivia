"""Free-text review queue — docs/HOW-IT-WORKS.md section 7.

Near-miss spellings score (a tipsy thumb should not lose the point), and land in
a queue on the organizer's phone where she can revoke one. A flip re-scores
immediately so the leaderboard stays honest.
"""

import pytest

from engine import Question, accuracy_of, needs_review
from game import Game, Phase

T0 = 1000.0


def ft(*accepted):
    return Question(id="ft1", type="free_text", prompt="?", correct=list(accepted))


class TestTheGenerousBandScores:
    def test_a_close_typo_scores_rather_than_being_thrown_out(self):
        # "beatlez" ~0.86 of "beatles" — a thumb slip, not a wrong answer.
        assert accuracy_of(ft("The Beatles"), "the beatlez") == 1.0

    def test_a_looser_near_miss_still_scores_and_is_flagged(self):
        q = ft("Tempranillo")
        assert accuracy_of(q, "temprano") == 1.0
        assert needs_review(q, "temprano") is True

    def test_a_confident_match_is_not_flagged(self):
        q = ft("The Beatles")
        assert accuracy_of(q, "the beatles") == 1.0
        assert needs_review(q, "the beatles") is False

    def test_a_genuinely_wrong_answer_scores_nothing_and_is_not_flagged(self):
        q = ft("The Beatles")
        assert accuracy_of(q, "the rolling stones") == 0.0
        assert needs_review(q, "the rolling stones") is False

    def test_only_free_text_is_ever_flagged(self):
        mc = Question(id="m", type="multiple_choice", prompt="?",
                      options=["a", "b"], correct="a")
        assert needs_review(mc, "b") is False


def game_with_free_text():
    g = Game(questions=[ft("Tempranillo")], timer_seconds=15.0,
             questions_per_round=1, now=T0)
    a = g.join("Alice", now=T0)
    g.start(now=T0)
    g.mark_rendered(a, now=T0)
    return g, a


class TestTheQueue:
    def test_a_near_miss_reaches_the_organizer(self):
        g, a = game_with_free_text()
        g.submit(a, "temprano", now=T0 + 1)
        g.tick(now=T0 + 1)
        assert [f["player_id"] for f in g.flagged] == [a.id]

    def test_the_queue_carries_what_she_needs_to_judge(self):
        g, a = game_with_free_text()
        g.submit(a, "temprano", now=T0 + 1)
        g.tick(now=T0 + 1)
        entry = g.flagged[0]
        assert entry["given"] == "temprano"
        assert entry["name"] == "Alice"
        assert entry["points"] > 0
        assert entry["accepted"] is True

    def test_a_confident_answer_never_reaches_her(self):
        g = Game(questions=[ft("Tempranillo")], timer_seconds=15.0,
                 questions_per_round=1, now=T0)
        a = g.join("Alice", now=T0)
        g.start(now=T0)
        g.mark_rendered(a, now=T0)
        g.submit(a, "tempranillo", now=T0 + 1)
        g.tick(now=T0 + 1)
        assert g.flagged == []


class TestOverriding:
    def setup_method(self):
        self.g, self.a = game_with_free_text()
        self.g.submit(self.a, "temprano", now=T0 + 1)
        self.g.tick(now=T0 + 1)
        self.awarded = self.a.score

    def test_revoking_takes_the_points_back(self):
        self.g.review(self.a.id, "ft1", accept=False)
        assert self.a.score == 0

    def test_accepting_an_already_accepted_answer_changes_nothing(self):
        self.g.review(self.a.id, "ft1", accept=True)
        assert self.a.score == self.awarded

    def test_she_can_change_her_mind_back(self):
        self.g.review(self.a.id, "ft1", accept=False)
        self.g.review(self.a.id, "ft1", accept=True)
        assert self.a.score == self.awarded

    def test_revoking_twice_does_not_double_penalise(self):
        self.g.review(self.a.id, "ft1", accept=False)
        self.g.review(self.a.id, "ft1", accept=False)
        assert self.a.score == 0

    def test_the_queue_remembers_her_decision(self):
        self.g.review(self.a.id, "ft1", accept=False)
        assert self.g.flagged[0]["accepted"] is False

    def test_reviewing_something_that_was_never_flagged_is_ignored(self):
        self.g.review("nobody", "ft1", accept=False)
        assert self.a.score == self.awarded


class TestTheQueueNeverReachesPlayers:
    """It holds other players' answers — on a player's phone it is an answer key."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        import os, sys
        monkeypatch.setenv("BARGAMES_ADMIN_PASSWORD", "pw")
        monkeypatch.setenv("BARGAMES_COOKIE_PATH", "/")
        monkeypatch.setenv("BARGAMES_COOKIE_SECURE", "0")
        monkeypatch.setenv("BARGAMES_SELFIE_DIR", str(tmp_path / "s"))
        monkeypatch.setenv("BARGAMES_STATE_FILE", str(tmp_path / "state.json"))
        for m in ["main", "persist", "game", "engine", "selfies"]:
            sys.modules.pop(m, None)
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
        import main
        from fastapi.testclient import TestClient
        with TestClient(main.app) as c:
            yield c

    def test_a_player_snapshot_carries_no_review_queue(self, client):
        client.post("/api/admin/login", data={"password": "pw"})
        pin = client.post("/api/admin/create", json={}).json()["pin"]
        tok = client.post("/api/join", data={"pin": pin, "name": "A"}).json()["token"]
        assert "flagged" not in client.get("/api/state", params={"t": tok}).json()

    def test_a_player_cannot_call_the_review_endpoint(self, client):
        client.post("/api/admin/login", data={"password": "pw"})
        pin = client.post("/api/admin/create", json={}).json()["pin"]
        client.post("/api/join", data={"pin": pin, "name": "A"})
        client.cookies.delete("bg_admin")
        r = client.post("/api/admin/review",
                        json={"player_id": "x", "question_id": "y", "accept": False})
        assert r.status_code == 403

    def test_a_player_cannot_read_the_admin_snapshot(self, client):
        client.post("/api/admin/login", data={"password": "pw"})
        client.post("/api/admin/create", json={})
        client.cookies.delete("bg_admin")
        assert client.get("/api/admin/state").status_code == 403
