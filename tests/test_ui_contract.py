"""The UI contract is load-bearing — docs/UI-CONTRACT.md.

The JS drives these ids, and the Playwright suite selects on them. A restyle
that renames one breaks the game silently: the page still renders, it just
stops working. So the contract is a test, not a promise.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

PLAY_IDS = [
    "screen-lobby", "screen-question", "screen-waiting", "screen-reveal",
    "screen-leaderboard", "screen-final", "timer-ring", "timer-text",
    "q-prompt", "q-image", "q-options", "q-truefalse", "q-wheel", "q-text",
    "q-players", "bubbles", "bubble-count", "reveal-correct", "reveal-points",
    "race", "leaderboard",
]
ADMIN_IDS = [
    "admin-login", "admin-panel", "cfg-timer", "cfg-per-round", "cfg-reveal",
    "btn-create", "pin-display", "join-link", "roster", "btn-start",
    "btn-end-question", "btn-next-round", "btn-delete-selfies",
    "admin-bubbles", "admin-race", "flagged-queue", "flagged-list",
    "panel-error",
]
JOIN_IDS = ["pin-input", "name-input", "selfie-input", "btn-join", "join-error"]


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


@pytest.mark.parametrize("path,ids", [
    ("/play", PLAY_IDS), ("/admin", ADMIN_IDS),
    ("/", JOIN_IDS), ("/j/4821", JOIN_IDS),
])
def test_every_contract_id_is_present(client, path, ids):
    html = client.get(path).text
    missing = [i for i in ids if f'id="{i}"' not in html]
    assert not missing, f"{path} is missing contract ids: {missing}"


class TestAssetsResolveAtEveryRouteDepth:
    def test_the_join_by_link_page_can_find_its_own_assets(self, client):
        # /j/<pin> is one level deep, so a bare relative "static/style.css"
        # resolves to /j/static/style.css and 404s. This is the page most
        # guests open, and it failed as unstyled default HTML.
        html = client.get("/j/4821").text
        assert "<base" in html, "deep route needs a <base> for relative assets"

    def test_seed_question_images_exist(self, client):
        # Every image a seed question names must be served; without the file
        # it is a question with no picture.
        import json
        from pathlib import Path
        seed = Path(__file__).parent.parent / "app" / "seed" / "questions.json"
        images = [q["image"] for q in json.loads(seed.read_text())["questions"] if q.get("image")]
        assert images
        for name in images:
            r = client.get(f"/static/questions/{name}")
            assert r.status_code == 200, name


class TestTheWireNeverLeaksTheAnswer:
    def test_the_correct_answer_is_absent_while_the_question_is_open(self, client):
        client.post("/api/admin/login", data={"password": "pw"})
        pin = client.post("/api/admin/create", json={}).json()["pin"]
        tok = client.post("/api/join", data={"pin": pin, "name": "A"}).json()["token"]
        client.post("/api/admin/start")
        state = client.get("/api/state", params={"t": tok}).json()
        assert state["phase"] == "question"
        assert "correct" not in state["question"]
        assert "reveal" not in state


class TestPanelErrorsAreVisible:
    def test_the_panel_error_lives_inside_the_panel_not_the_login(self, client):
        # #admin-error is inside #admin-login, which is hidden once signed in.
        # A control failure written there is invisible — the exact silent-button
        # problem the message exists to prevent.
        html = client.get("/admin").text
        panel = html.split('id="admin-panel"', 1)[1]
        assert 'id="panel-error"' in panel


class TestPanelRefusalsGoSomewhereVisible:
    """It is not enough for #panel-error to exist — something must write to it.

    A previous fix added the element and passed its test while the click
    handler still wrote into #admin-error, the node inside the hidden login
    section. The message was set correctly on an invisible element, so the
    organizer tapped START and nothing happened at all.
    """

    def js(self):
        import pathlib
        return (pathlib.Path(__file__).parent.parent
                / "app" / "static" / "admin.js").read_text()

    def handler(self, name: str) -> str:
        src = self.js()
        start = src.index(f'"{name}"')
        rest = src[start:]
        end = rest.find('$("btn-', 1)
        return rest if end == -1 else rest[:end]

    def test_the_client_writes_control_refusals_to_the_panel_error(self):
        assert "panel-error" in self.js()

    def test_the_start_button_does_not_write_into_the_hidden_login_error(self):
        h = self.handler("btn-start")
        assert "admin-error" not in h, (
            "btn-start writes its refusal into #admin-error, which is inside "
            "#admin-login and hidden once signed in — the message goes nowhere"
        )

    def test_the_login_error_is_still_the_one_used_on_the_login_screen(self):
        # #admin-error is correct THERE — the login section is visible then.
        assert "admin-error" in self.js()
