"""The organizer can remove someone.

A duplicate join, a phone someone lent to a friend, a name nobody wants on the
screen. Removing has to take the photo with it and must not leave the room
waiting on a person who is no longer there.
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


def a_photo():
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), (40, 120, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def game_with(client, names, photos=False):
    client.post("/api/admin/login", data={"password": "pw"})
    pin = client.post("/api/admin/create", json={}).json()["pin"]
    out = []
    for n in names:
        files = {"selfie": ("m.jpg", a_photo(), "image/jpeg")} if photos else None
        r = client.post("/api/join", data={"pin": pin, "name": n}, files=files)
        out.append(r.json())
    return out


class TestRemoving:
    def test_the_player_is_gone_from_the_room(self, client):
        a, b = game_with(client, ["Alice", "Bob"])
        r = client.post("/api/admin/remove_player", json={"player_id": b["id"]})
        assert r.status_code == 200
        names = [p["name"] for p in client.get("/api/admin/state").json()["players"]]
        assert names == ["Alice"]

    def test_her_photo_goes_with_her(self, client):
        import selfies
        import main
        a, b = game_with(client, ["Alice", "Bob"], photos=True)
        stored = main.game.players[b["id"]].selfie_id
        assert selfies.read(stored) is not None
        client.post("/api/admin/remove_player", json={"player_id": b["id"]})
        assert selfies.read(stored) is None

    def test_her_phone_is_told_it_is_no_longer_in(self, client):
        a, b = game_with(client, ["Alice", "Bob"])
        client.post("/api/admin/remove_player", json={"player_id": b["id"]})
        assert client.get("/api/state", params={"t": b["token"]}).json()["you_known"] is False

    def test_removing_someone_unknown_is_harmless(self, client):
        game_with(client, ["Alice"])
        r = client.post("/api/admin/remove_player", json={"player_id": "nobody"})
        assert r.status_code in (200, 404)
        assert len(client.get("/api/admin/state").json()["players"]) == 1

    def test_only_the_organizer_can_remove(self, client):
        a, b = game_with(client, ["Alice", "Bob"])
        client.cookies.delete("bg_admin")
        r = client.post("/api/admin/remove_player", json={"player_id": b["id"]})
        assert r.status_code == 403


class TestRemovingMidQuestion:
    def test_the_room_stops_waiting_for_someone_who_has_been_removed(self, client):
        # Otherwise removing the person holding everyone up achieves nothing.
        import main, time
        a, b = game_with(client, ["Alice", "Bob"])
        client.post("/api/admin/start")
        client.post("/api/rendered", params={"t": a["token"]})
        client.post("/api/rendered", params={"t": b["token"]})
        client.post("/api/answer", params={"t": a["token"]}, json={"answer": True})
        assert main.game.phase.value == "question"      # still waiting on Bob
        client.post("/api/admin/remove_player", json={"player_id": b["id"]})
        main.game.tick(time.time())
        assert main.game.phase.value == "reveal"

    def test_the_answered_count_drops_too(self, client):
        a, b, c3 = game_with(client, ["Alice", "Bob", "Cara"])
        client.post("/api/admin/start")
        for p in (a, b, c3):
            client.post("/api/rendered", params={"t": p["token"]})
        client.post("/api/answer", params={"t": a["token"]}, json={"answer": True})
        assert client.get("/api/admin/state").json()["progress"] == {"answered": 1, "total": 3}
        client.post("/api/admin/remove_player", json={"player_id": c3["id"]})
        assert client.get("/api/admin/state").json()["progress"]["total"] == 2
