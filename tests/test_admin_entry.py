"""Admin entry from the public join page.

The join page is on a host the whole internet can reach, so putting a password
prompt on it makes the organizer login discoverable to everyone. The endpoint
already existed and was already unthrottled; adding a visible door to it is the
moment to fix that, not later.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAMES_ADMIN_PASSWORD", "right-password")
    monkeypatch.setenv("BARGAMES_COOKIE_PATH", "/")
    monkeypatch.setenv("BARGAMES_COOKIE_SECURE", "0")
    monkeypatch.setenv("BARGAMES_SELFIE_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("BARGAMES_STATE_FILE", str(tmp_path / "d" / "state.json"))
    monkeypatch.setenv("BARGAMES_SECRET_FILE", str(tmp_path / "d" / "secret"))
    for m in ["main", "persist", "game", "engine", "selfies"]:
        sys.modules.pop(m, None)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


class TestTheDoorIsOnThePage:
    def test_the_join_page_offers_a_way_in_for_the_organizer(self, client):
        html = client.get("/").text
        for el in ("admin-entry", "admin-prompt", "admin-prompt-input",
                   "admin-prompt-go", "admin-prompt-error"):
            assert f'id="{el}"' in html, f"join page is missing #{el}"

    def test_the_prompt_starts_hidden(self, client):
        html = client.get("/").text
        prompt = html[html.index('id="admin-prompt"'):]
        assert "hidden" in prompt[:200], "the admin prompt must start hidden"

    def test_the_join_by_link_page_has_it_too(self, client):
        assert 'id="admin-entry"' in client.get("/j/1234").text


class TestLogin:
    def test_the_right_password_signs_her_in(self, client):
        r = client.post("/api/admin/login", data={"password": "right-password"})
        assert r.status_code == 200 and "bg_admin" in r.cookies

    def test_a_wrong_password_is_rejected(self, client):
        r = client.post("/api/admin/login", data={"password": "nope"})
        assert r.status_code == 403
        assert "bg_admin" not in r.cookies

    def test_the_rejection_does_not_say_whether_a_game_exists(self, client):
        # No oracle for a passer-by probing the endpoint.
        body = client.post("/api/admin/login", data={"password": "nope"}).json()
        assert "game" not in str(body).lower()


class TestBruteForceThrottling:
    def test_repeated_wrong_guesses_get_locked_out(self, client):
        for _ in range(5):
            assert client.post("/api/admin/login", data={"password": "x"}).status_code == 403
        r = client.post("/api/admin/login", data={"password": "x"})
        assert r.status_code == 429, "no throttle on a public, internet-facing login"

    def test_the_lockout_blocks_even_the_correct_password(self, client):
        # Otherwise it is not a lockout, just a slower oracle.
        for _ in range(6):
            client.post("/api/admin/login", data={"password": "x"})
        r = client.post("/api/admin/login", data={"password": "right-password"})
        assert r.status_code == 429

    def test_signing_in_correctly_clears_the_count(self, client):
        for _ in range(3):
            client.post("/api/admin/login", data={"password": "x"})
        assert client.post("/api/admin/login", data={"password": "right-password"}).status_code == 200
        for _ in range(4):
            assert client.post("/api/admin/login", data={"password": "x"}).status_code == 403

    def test_the_organizer_is_not_locked_out_by_one_fat_fingered_attempt(self, client):
        client.post("/api/admin/login", data={"password": "rihgt-password"})
        assert client.post("/api/admin/login", data={"password": "right-password"}).status_code == 200
