"""The /selfie route as the CLIENT actually uses it — docs/UI-CONTRACT.md.

Every client renders `selfie/<player id>`, because a player's opaque
selfie_id is deliberately never published in the snapshot. These tests drive
the route the same way, which is what the earlier unit tests of selfies.py did
not do: they exercised storage, not the URL the browser asks for.
"""

import io
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAMES_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("BARGAMES_COOKIE_PATH", "/")
    monkeypatch.setenv("BARGAMES_COOKIE_SECURE", "0")
    monkeypatch.setenv("BARGAMES_SELFIE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("BARGAMES_STATE_FILE", str(tmp_path / "state.json"))
    for m in ["main", "persist", "game", "engine", "selfies"]:
        sys.modules.pop(m, None)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def a_photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (200, 60, 90)).save(buf, format="JPEG")
    return buf.getvalue()


def start_game_with_photo(client):
    client.post("/api/admin/login", data={"password": "pw"})
    pin = client.post("/api/admin/create", json={}).json()["pin"]
    r = client.post(
        "/api/join",
        data={"pin": pin, "name": "Alice"},
        files={"selfie": ("me.jpg", a_photo(), "image/jpeg")},
    )
    token = r.json()["token"]
    player_id = r.json()["id"]
    return pin, token, player_id


class TestPhotosActuallyLoad:
    def test_a_players_photo_loads_at_her_player_id(self, client):
        # The bug this file exists for: the route took a storage id while every
        # client asks by player id, so no photograph ever loaded all night and
        # it failed silently to an initial disc.
        _, _, player_id = start_game_with_photo(client)
        r = client.get(f"/selfie/{player_id}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"

    def test_the_organizer_can_see_players_photos(self, client):
        # She watches the bubble room to see who is holding up the question;
        # she holds an admin session, not a player token.
        _, token, player_id = start_game_with_photo(client)
        client.cookies.delete("bg_session")
        assert client.get(f"/selfie/{player_id}").status_code == 200

    def test_a_player_with_no_photo_is_not_an_error_page(self, client):
        client.post("/api/admin/login", data={"password": "pw"})
        pin = client.post("/api/admin/create", json={}).json()["pin"]
        pid = client.post("/api/join", data={"pin": pin, "name": "Bob"}).json()["id"]
        assert client.get(f"/selfie/{pid}").status_code == 403


class TestStillFailsClosed:
    def test_a_stranger_gets_nothing(self, client):
        _, _, player_id = start_game_with_photo(client)
        client.cookies.clear()
        assert client.get(f"/selfie/{player_id}").status_code == 403

    def test_an_unknown_player_id_gets_nothing(self, client):
        start_game_with_photo(client)
        assert client.get("/selfie/nosuchplayer").status_code == 403

    def test_photos_are_never_cached(self, client):
        _, _, player_id = start_game_with_photo(client)
        cc = client.get(f"/selfie/{player_id}").headers["cache-control"]
        assert "no-store" in cc and "private" in cc


class TestResumeLink:
    def test_opening_a_resume_link_in_a_fresh_browser_gets_a_session(self, client):
        # HOW-IT-WORKS section 6 treats a forwarded/reopened link as in scope. Without
        # a cookie every image on the page 403s even though the player is real.
        _, token, _ = start_game_with_photo(client)
        client.cookies.clear()
        r = client.get("/play", params={"t": token})
        assert r.status_code == 200
        assert "bg_session" in r.cookies

    def test_a_bogus_resume_token_sets_no_session(self, client):
        start_game_with_photo(client)
        client.cookies.clear()
        r = client.get("/play", params={"t": "not-a-real-token"})
        assert "bg_session" not in r.cookies
