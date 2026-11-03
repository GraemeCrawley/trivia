"""A version stamp on every screen.

So a phone showing something odd can be identified as running an old build
without guesswork — the recurring confusion tonight has been "is this the new
code or a cached copy?".
"""

import os
import re
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


@pytest.mark.parametrize("path", ["/", "/j/1234", "/play", "/admin"])
def test_every_screen_carries_the_version(client, path):
    html = client.get(path).text
    assert 'class="version"' in html, f"{path} has no version stamp"


def test_the_stamp_is_a_build_id_not_a_placeholder(client):
    html = client.get("/").text
    stamp = re.search(r'class="version"[^>]*>([^<]+)<', html).group(1).strip()
    assert stamp and stamp.lower() not in {"version", "v", "unknown"}, stamp


def test_it_changes_when_the_app_changes(client, tmp_path, monkeypatch):
    import main
    first = main.BUILD_ID
    # Touch a source file the way a deploy would.
    target = os.path.join(os.path.dirname(__file__), "..", "app", "game.py")
    stat = os.stat(target)
    os.utime(target, (stat.st_atime, stat.st_mtime + 5))
    try:
        assert main.compute_build_id() != first
    finally:
        os.utime(target, (stat.st_atime, stat.st_mtime))


def test_it_is_stable_between_calls_when_nothing_changes(client):
    import main
    assert main.compute_build_id() == main.compute_build_id()
