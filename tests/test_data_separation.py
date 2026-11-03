"""Photos, game state and the admin secret must not share a directory.

Found by the Playwright suite. `delete_all()` unlinks every file in the selfie
directory, and with production defaults the game snapshot lived in that same
directory — so the organizer's "delete every photo" button also deleted the
crash-recovery file, and told her it had removed more photos than existed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAMES_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("BARGAMES_COOKIE_PATH", "/")
    monkeypatch.setenv("BARGAMES_COOKIE_SECURE", "0")
    monkeypatch.setenv("BARGAMES_SELFIE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BARGAMES_STATE_FILE", str(tmp_path / "data" / "game-state.json"))
    monkeypatch.setenv("BARGAMES_SECRET_FILE", str(tmp_path / "data" / "admin-secret"))
    for m in ["main", "persist", "game", "engine", "selfies"]:
        sys.modules.pop(m, None)
    return tmp_path


@pytest.fixture
def client(app_env):
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def a_photo():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), (10, 120, 90)).save(buf, format="JPEG")
    return buf.getvalue()


def game_with_a_photo(client):
    client.post("/api/admin/login", data={"password": "pw"})
    pin = client.post("/api/admin/create", json={}).json()["pin"]
    r = client.post("/api/join", data={"pin": pin, "name": "Alice"},
                    files={"selfie": ("me.jpg", a_photo(), "image/jpeg")})
    return r.json()["id"]


class TestDeletingPhotosLeavesTheGameAlone:
    def test_deleting_photos_never_touches_the_snapshot_at_all(self, client, app_env):
        # Asserted against delete_all() directly, not the endpoint: the handler
        # re-saves the game immediately afterwards, so testing the end state
        # would pass even while the file was being deleted and rewritten every
        # time. Broken-by-one-line still counts as broken.
        import persist, selfies
        game_with_a_photo(client)
        assert persist.STATE_FILE.is_file()
        selfies.delete_all()
        assert persist.STATE_FILE.is_file(), (
            "delete_all() deleted the crash-recovery snapshot"
        )

    def test_deleting_photos_never_touches_the_admin_secret(self, client, app_env):
        import selfies
        from pathlib import Path
        secret = Path(os.environ["BARGAMES_SECRET_FILE"])
        game_with_a_photo(client)
        assert secret.is_file()
        selfies.delete_all()
        assert secret.is_file(), "delete_all() deleted the admin secret"

    def test_the_panic_button_reports_only_photos(self, client):
        game_with_a_photo(client)
        removed = client.post("/api/admin/delete_selfies").json()["removed"]
        assert removed == 1, f"reported {removed} photos deleted, there was 1"

    def test_the_photos_really_are_gone(self, client):
        pid = game_with_a_photo(client)
        assert client.get(f"/selfie/{pid}").status_code == 200
        client.post("/api/admin/delete_selfies")
        assert client.get(f"/selfie/{pid}").status_code == 403

    def test_the_game_still_recovers_after_photos_are_deleted(self, client, app_env):
        import persist
        game_with_a_photo(client)
        client.post("/api/admin/delete_selfies")
        import main
        restored = persist.load(main.load_questions())
        assert restored is not None and len(restored.players) == 1


class TestTheOrganizerStaysSignedInAcrossARestart:
    def test_her_session_survives_the_container_bouncing(self, app_env):
        # ADMIN_SECRET was regenerated on every import, so a restart logged her
        # out and she had to re-enter the password in front of the room.
        from fastapi.testclient import TestClient
        import main
        with TestClient(main.app) as c:
            c.post("/api/admin/login", data={"password": "pw"})
            c.post("/api/admin/create", json={})
            cookie = c.cookies.get("bg_admin")
            assert c.get("/api/admin/state").status_code == 200

        for m in ["main", "persist", "game", "engine", "selfies"]:
            sys.modules.pop(m, None)
        import main as main2
        with TestClient(main2.app) as c2:
            c2.cookies.set("bg_admin", cookie)
            assert c2.get("/api/admin/state").status_code == 200, (
                "the organizer was logged out by a restart"
            )

    def test_the_secret_is_not_guessable_from_the_password(self, app_env):
        import main
        assert main.ADMIN_SECRET != "pw"
        assert len(main.ADMIN_SECRET) >= 20
