"""The organizer's guide, served at /guide.

It is on the public host, so it must contain nothing that is not already
public — it points at the admin page but must never carry the password.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BARGAMES_ADMIN_PASSWORD", "super-secret-pw")
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


class TestItIsServed:
    def test_the_guide_loads(self, client):
        assert client.get("/guide").status_code == 200

    def test_it_is_rendered_html_not_raw_markdown(self, client):
        body = client.get("/guide").text
        assert "<h2" in body, "headings were not rendered"
        assert "## " not in body, "raw markdown leaked into the page"

    def test_the_tables_are_rendered(self, client):
        # The what-if table is the most useful part of it.
        assert "<table" in client.get("/guide").text

    def test_it_reads_on_a_phone(self, client):
        body = client.get("/guide").text
        assert "viewport" in body and "width=device-width" in body

    def test_it_carries_the_build_stamp_like_every_other_screen(self, client):
        assert 'class="version"' in client.get("/guide").text

    def test_it_uses_the_same_stylesheet(self, client):
        assert "static/style.css" in client.get("/guide").text


class TestItLeaksNothing:
    def test_the_organizer_password_is_not_on_the_page(self, client):
        assert "super-secret-pw" not in client.get("/guide").text

    def test_no_admin_session_is_handed_out_by_visiting_it(self, client):
        assert "bg_admin" not in client.get("/guide").cookies

    def test_it_needs_no_login(self, client):
        client.cookies.clear()
        assert client.get("/guide").status_code == 200
