"""Selfie serving is fail-closed. HOW-IT-WORKS section 6, asserted rather than assumed.

The route takes the PLAYER id (docs/UI-CONTRACT.md); the storage id is never
published. EXIF stripping is already covered by tests/test_selfies.py and is
deliberately not duplicated here.
"""

import time

import httpx
import pytest

from conftest import ADMIN_PASSWORD, Admin, jpeg_bytes, join_api


def stored_ids(server):
    return sorted(p.name for p in server.selfie_dir.iterdir()
                  if p.is_file() and p.name != "game-state.json")


def a_player_with_a_photo(server, admin):
    """Returns (photographed player, another player in the same game)."""
    pin = admin.create(timer=30, per_round=8, reveal=2)
    ada = join_api(server, pin, "Ada", selfie_bytes=jpeg_bytes())
    bea = join_api(server, pin, "Bea")
    return ada, bea


def test_selfie_requires_a_session_cookie(server, admin):
    ada, _ = a_player_with_a_photo(server, admin)
    r = httpx.get(f"{server.base_url}/selfie/{ada.pid}")
    assert r.status_code == 403
    assert len(r.content) < 64, "a 403 leaked a body"


def test_selfie_served_to_a_player_in_this_game(server, admin):
    ada, bea = a_player_with_a_photo(server, admin)
    for who in (ada, bea):
        r = httpx.get(f"{server.base_url}/selfie/{ada.pid}",
                      cookies={"bg_session": who.token})
        assert r.status_code == 200, f"{who.name} could not see Ada's bubble"
        assert r.headers["content-type"] == "image/jpeg"

    cc = r.headers["cache-control"].lower()
    assert "no-store" in cc and "private" in cc, cc
    assert r.headers.get("vary", "").lower() == "cookie"
    assert "noindex" in r.headers.get("x-robots-tag", "").lower()


def test_selfie_url_carries_no_file_extension(server, admin):
    ada, bea = a_player_with_a_photo(server, admin)
    assert "." not in ada.pid, f"player id {ada.pid} would give the URL an extension"
    for bad in [f"{ada.pid}.jpg", "../game-state.json", "..%2f..%2fetc%2fpasswd"]:
        r = httpx.get(f"{server.base_url}/selfie/{bad}",
                      cookies={"bg_session": bea.token})
        assert r.status_code in (403, 404), (bad, r.status_code)


def test_the_storage_id_is_not_a_usable_url(server, admin):
    """Filenames on the volume must not be requestable, even by a player."""
    before = set(stored_ids(server))
    ada, bea = a_player_with_a_photo(server, admin)
    sid = (set(stored_ids(server)) - before).pop()
    assert sid != ada.pid
    r = httpx.get(f"{server.base_url}/selfie/{sid}",
                  cookies={"bg_session": bea.token})
    assert r.status_code == 403, "a photo was fetched by guessing its filename"


def test_a_player_without_a_photo_is_not_a_hole(server, admin):
    ada, bea = a_player_with_a_photo(server, admin)
    r = httpx.get(f"{server.base_url}/selfie/{bea.pid}",
                  cookies={"bg_session": ada.token})
    assert r.status_code == 403


def test_cookie_from_a_different_game_is_refused(server, admin):
    """Creating a new game invalidates every old session. Fail closed."""
    ada, bea = a_player_with_a_photo(server, admin)
    assert httpx.get(f"{server.base_url}/selfie/{ada.pid}",
                     cookies={"bg_session": bea.token}).status_code == 200

    admin.create(timer=30, per_round=8, reveal=2)   # a different game entirely
    r = httpx.get(f"{server.base_url}/selfie/{ada.pid}",
                  cookies={"bg_session": bea.token})
    assert r.status_code == 403, "a session from the previous game still worked"


def test_delete_everything_now_removes_files_and_403s(server, admin):
    ada, bea = a_player_with_a_photo(server, admin)
    assert httpx.get(f"{server.base_url}/selfie/{ada.pid}",
                     cookies={"bg_session": bea.token}).status_code == 200

    admin.delete_selfies()
    assert not stored_ids(server), "files still on the volume"
    r = httpx.get(f"{server.base_url}/selfie/{ada.pid}",
                  cookies={"bg_session": bea.token})
    assert r.status_code == 403
    me = [p for p in admin.state()["players"] if p["id"] == ada.pid][0]
    assert me["selfie"] is False


def test_crash_recovery_snapshot_is_not_reachable_over_the_selfie_route(server, admin):
    _, bea = a_player_with_a_photo(server, admin)
    r = httpx.get(f"{server.base_url}/selfie/game-state.json",
                  cookies={"bg_session": bea.token})
    assert r.status_code == 403


@pytest.fixture
def prod_layout_server():
    """Exactly how production is wired.

    deploy/compose.yml mounts the named volume at /selfies and sets
    BARGAMES_SELFIE_DIR=/selfies but no BARGAMES_STATE_FILE, and
    app/persist.py defaults STATE_FILE to /selfies/game-state.json — so the
    crash-recovery snapshot lives inside the photo directory.
    """
    from conftest import AppServer
    s = AppServer()
    s.state_file = s.data_dir / "game-state.json"
    s.start()
    yield s
    s.stop()


def test_delete_all_photos_does_not_destroy_the_crash_snapshot(prod_layout_server):
    s = prod_layout_server
    a = Admin(s.base_url)
    assert a.login().status_code == 200
    pin = a.create(timer=30, per_round=8, reveal=2)
    join_api(s, pin, "Ada", selfie_bytes=jpeg_bytes())

    end = time.time() + 5
    while time.time() < end and not s.state_file.exists():
        time.sleep(0.1)
    assert s.state_file.exists(), "no crash-recovery snapshot was ever written"

    a.delete_selfies()
    assert s.state_file.exists(), (
        "'Delete every photo now' also deleted the crash-recovery snapshot "
        f"({s.state_file.name}); selfies.delete_all() unlinks every file in "
        "the directory and persist.STATE_FILE lives in that same directory, "
        "so the game no longer survives a container restart afterwards")
