"""How long until the room moves on.

On the waiting screen her own clock is spent and irrelevant — what she wants
to know is how much longer everyone else has. That is the latest outstanding
deadline, capped by the ceiling.
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


def start(client, names, timer=15):
    client.post("/api/admin/login", data={"password": "pw"})
    pin = client.post("/api/admin/create", json={"timer_seconds": timer}).json()["pin"]
    out = [client.post("/api/join", data={"pin": pin, "name": n}).json() for n in names]
    client.post("/api/admin/start")
    return out


class TestTheRoomClock:
    def test_it_is_the_latest_outstanding_deadline(self, client):
        import main
        a, b = start(client, ["Alice", "Bob"])
        client.post("/api/rendered", params={"t": a["token"]})
        client.post("/api/rendered", params={"t": b["token"]})
        # Push Bob's deadline out; the room waits for the later of the two.
        main.game.barrier.states[b["id"]].deadline += 5
        client.post("/api/answer", params={"t": a["token"]}, json={"answer": True})
        s = client.get("/api/state", params={"t": a["token"]}).json()
        assert s["waiting_until"] == pytest.approx(
            main.game.barrier.states[b["id"]].deadline)

    def test_a_phone_that_never_rendered_counts_as_the_ceiling(self, client):
        import main
        a, b = start(client, ["Alice", "Bob"])
        client.post("/api/rendered", params={"t": a["token"]})
        client.post("/api/answer", params={"t": a["token"]}, json={"answer": True})
        s = client.get("/api/state", params={"t": a["token"]}).json()
        assert s["waiting_until"] == pytest.approx(main.game.barrier.ceiling)

    def test_it_never_exceeds_the_ceiling(self, client):
        import main
        a, b = start(client, ["Alice", "Bob"])
        client.post("/api/rendered", params={"t": a["token"]})
        client.post("/api/rendered", params={"t": b["token"]})
        main.game.barrier.states[b["id"]].deadline += 9999
        client.post("/api/answer", params={"t": a["token"]}, json={"answer": True})
        s = client.get("/api/state", params={"t": a["token"]}).json()
        assert s["waiting_until"] <= main.game.barrier.ceiling + 0.001

    def test_there_is_nothing_to_wait_for_once_everyone_is_in(self, client):
        a, b = start(client, ["Alice", "Bob"])
        for p in (a, b):
            client.post("/api/rendered", params={"t": p["token"]})
            client.post("/api/answer", params={"t": p["token"]}, json={"answer": True})
        s = client.get("/api/state", params={"t": a["token"]}).json()
        assert s.get("waiting_until") is None

    def test_a_seated_player_does_not_hold_the_clock_open(self, client):
        import main, time
        a, = start(client, ["Alice"])
        client.post("/api/rendered", params={"t": a["token"]})
        pin = main.game.pin
        client.post("/api/join", data={"pin": pin, "name": "Latecomer"})
        client.post("/api/answer", params={"t": a["token"]}, json={"answer": True})
        s = client.get("/api/state", params={"t": a["token"]}).json()
        assert s.get("waiting_until") is None


class TestTheScreenShowsIt:
    def test_the_waiting_screen_has_somewhere_to_put_it(self, client):
        assert 'id="waiting-time"' in client.get("/play").text
