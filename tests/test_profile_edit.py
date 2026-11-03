"""A player can fix her name or add a photo from the lobby.

She typed her name in a hurry, or skipped the selfie and changed her mind once
she saw everyone else's faces. Before this she had no way back — the only
option was to join again as a new player on zero.
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
    monkeypatch.setenv("BARGAMES_SELFIE_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("BARGAMES_STATE_FILE", str(tmp_path / "d" / "s.json"))
    monkeypatch.setenv("BARGAMES_SECRET_FILE", str(tmp_path / "d" / "sec"))
    for m in ["main", "persist", "game", "engine", "selfies"]:
        sys.modules.pop(m, None)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def a_photo(colour=(200, 60, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), colour).save(buf, format="JPEG")
    return buf.getvalue()


def join(client, name="Alice", with_photo=False):
    client.post("/api/admin/login", data={"password": "pw"})
    pin = client.post("/api/admin/create", json={}).json()["pin"]
    files = {"selfie": ("me.jpg", a_photo(), "image/jpeg")} if with_photo else None
    r = client.post("/api/join", data={"pin": pin, "name": name}, files=files)
    return r.json()["token"], r.json()["id"]


class TestChangingYourName:
    def test_she_can_correct_her_name(self, client):
        token, _ = join(client, "Aliec")
        r = client.post("/api/profile", params={"t": token}, data={"name": "Alice"})
        assert r.status_code == 200
        assert client.get("/api/state", params={"t": token}).json()["you"]["name"] == "Alice"

    def test_an_empty_name_is_refused_rather_than_wiping_hers(self, client):
        token, _ = join(client, "Alice")
        client.post("/api/profile", params={"t": token}, data={"name": "   "})
        assert client.get("/api/state", params={"t": token}).json()["you"]["name"] == "Alice"

    def test_a_stranger_cannot_rename_anyone(self, client):
        join(client, "Alice")
        r = client.post("/api/profile", params={"t": "not-a-token"}, data={"name": "Mallory"})
        assert r.status_code == 403

    def test_her_score_is_untouched(self, client):
        token, pid = join(client, "Alice")
        import main
        main.game.players[pid].score = 750
        client.post("/api/profile", params={"t": token}, data={"name": "Alicia"})
        assert client.get("/api/state", params={"t": token}).json()["you"]["score"] == 750


class TestAddingOrChangingYourPhoto:
    def test_she_can_add_a_photo_she_skipped(self, client):
        token, pid = join(client, "Alice", with_photo=False)
        assert client.get(f"/selfie/{pid}").status_code == 403
        r = client.post("/api/profile", params={"t": token},
                        data={"name": "Alice"},
                        files={"selfie": ("me.jpg", a_photo(), "image/jpeg")})
        assert r.status_code == 200
        assert client.get(f"/selfie/{pid}").status_code == 200

    def test_she_can_replace_a_photo_she_dislikes(self, client):
        token, pid = join(client, "Alice", with_photo=True)
        before = client.get(f"/selfie/{pid}").content
        client.post("/api/profile", params={"t": token}, data={"name": "Alice"},
                    files={"selfie": ("new.jpg", a_photo((20, 200, 90)), "image/jpeg")})
        assert client.get(f"/selfie/{pid}").content != before

    def test_the_old_photo_is_deleted_not_left_on_the_volume(self, client):
        # These are real people's faces; a replaced one should not linger.
        import selfies
        token, pid = join(client, "Alice", with_photo=True)
        import main
        old_id = main.game.players[pid].selfie_id
        client.post("/api/profile", params={"t": token}, data={"name": "Alice"},
                    files={"selfie": ("new.jpg", a_photo((20, 200, 90)), "image/jpeg")})
        assert selfies.read(old_id) is None

    def test_sending_no_photo_keeps_the_one_she_has(self, client):
        token, pid = join(client, "Alice", with_photo=True)
        client.post("/api/profile", params={"t": token}, data={"name": "Alice B"})
        assert client.get(f"/selfie/{pid}").status_code == 200


class TestWhenSheCanDoIt:
    def test_she_can_edit_in_the_lobby(self, client):
        token, _ = join(client, "Alice")
        assert client.post("/api/profile", params={"t": token},
                           data={"name": "Alice2"}).status_code == 200

    def test_she_cannot_edit_mid_question(self, client):
        # Changing your face while the room is looking at the bubbles is a
        # distraction, and renaming mid-scoring makes the leaderboard lie.
        token, _ = join(client, "Alice")
        client.post("/api/admin/start")
        r = client.post("/api/profile", params={"t": token}, data={"name": "Alice2"})
        assert r.status_code == 409


class TestOtherPhonesSeeTheChange:
    """A new photo has to appear without a reload.

    The bubble field reconciles nodes in place, so an existing bubble kept its
    old avatar forever — and because the photo URL is keyed only on the player
    id, even a rebuilt one could serve the previous image. The snapshot carries
    a revision so both problems have a signal to react to.
    """

    def test_a_player_starts_at_photo_revision_zero(self, client):
        token, pid = join(client, "Alice", with_photo=False)
        me = next(p for p in client.get("/api/state", params={"t": token}).json()["players"]
                  if p["id"] == pid)
        assert me["photo_rev"] == 0

    def test_adding_a_photo_bumps_the_revision(self, client):
        token, pid = join(client, "Alice", with_photo=False)
        client.post("/api/profile", params={"t": token}, data={"name": "Alice"},
                    files={"selfie": ("me.jpg", a_photo(), "image/jpeg")})
        me = next(p for p in client.get("/api/state", params={"t": token}).json()["players"]
                  if p["id"] == pid)
        assert me["photo_rev"] == 1 and me["selfie"] is True

    def test_replacing_a_photo_bumps_it_again(self, client):
        token, pid = join(client, "Alice", with_photo=True)
        for _ in range(2):
            client.post("/api/profile", params={"t": token}, data={"name": "Alice"},
                        files={"selfie": ("x.jpg", a_photo((5, 200, 120)), "image/jpeg")})
        me = next(p for p in client.get("/api/state", params={"t": token}).json()["players"]
                  if p["id"] == pid)
        # Joining with a photo is already revision 1, plus two replacements.
        assert me["photo_rev"] == 3

    def test_changing_only_the_name_does_not_bump_it(self, client):
        token, pid = join(client, "Alice", with_photo=True)
        client.post("/api/profile", params={"t": token}, data={"name": "Alicia"})
        me = next(p for p in client.get("/api/state", params={"t": token}).json()["players"]
                  if p["id"] == pid)
        assert me["photo_rev"] == 1   # the one from joining with a photo
