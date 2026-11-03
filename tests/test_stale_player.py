"""A phone left over from a previous game must be told, not left hanging.

The organizer taps "Start a new game". Every phone still open from the last one
holds a token the server no longer knows. The snapshot simply omitted `you`, and
the client kept rendering whatever it had — so a player sat looking at "You're
in. Waiting for the organizer to start" with her old score in the corner, in a
game she was not in and could not be started into.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAMES_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("BARGAMES_COOKIE_PATH", "/")
    monkeypatch.setenv("BARGAMES_COOKIE_SECURE", "0")
    monkeypatch.setenv("BARGAMES_SELFIE_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("BARGAMES_STATE_FILE", str(tmp_path / "d" / "s.json"))
    monkeypatch.setenv("BARGAMES_SECRET_FILE", str(tmp_path / "d" / "sec"))
    for m in ["main", "persist", "game", "engine", "selfies"]:
        sys.modules.pop(m, None)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def join_a_game(client):
    client.post("/api/admin/login", data={"password": "pw"})
    pin = client.post("/api/admin/create", json={}).json()["pin"]
    return client.post("/api/join", data={"pin": pin, "name": "Alice"}).json()["token"]


class TestTheServerSaysItDoesNotKnowYou:
    def test_a_current_player_is_known(self, client):
        token = join_a_game(client)
        s = client.get("/api/state", params={"t": token}).json()
        assert s["you_known"] is True
        assert s["you"]["name"] == "Alice"

    def test_a_token_from_the_previous_game_is_not_known(self, client):
        token = join_a_game(client)
        client.post("/api/admin/create", json={})      # organizer starts a new game
        s = client.get("/api/state", params={"t": token}).json()
        assert s["you_known"] is False, (
            "a phone from the old game is not told it has been left behind"
        )

    def test_the_stale_phone_is_not_handed_someone_elses_identity(self, client):
        token = join_a_game(client)
        client.post("/api/admin/create", json={})
        s = client.get("/api/state", params={"t": token}).json()
        assert "you" not in s or s.get("you") is None

    def test_no_identity_at_all_is_not_known(self, client):
        # Cookies cleared too: the session cookie IS an identity, so leaving it
        # in place would still, correctly, resolve to a known player.
        join_a_game(client)
        client.cookies.clear()
        assert client.get("/api/state").json()["you_known"] is False

    def test_a_reset_keeps_everyone_known_because_they_stay_in_the_room(self, client):
        # Reset is deliberately different from a new game: nobody has to rejoin.
        token = join_a_game(client)
        client.post("/api/admin/reset")
        s = client.get("/api/state", params={"t": token}).json()
        assert s["you_known"] is True
        assert s["you"]["score"] == 0


class TestTheClientHasSomewhereToPutIt:
    def test_the_play_page_has_a_screen_for_being_left_behind(self, client):
        html = client.get("/play").text
        assert 'id="screen-gone"' in html
