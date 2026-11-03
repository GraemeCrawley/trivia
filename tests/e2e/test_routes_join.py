"""Routes, joining, names and selfie-vs-no-selfie. HOW-IT-WORKS section 10, layer 2."""

import httpx
import pytest

from conftest import Admin, jpeg_bytes, join_api


# ------------------------------------------------------------------- routes

def test_public_routes_render(server, admin):
    b = server.base_url
    assert httpx.get(b + "/healthz").status_code == 200
    assert httpx.get(b + "/healthz").json()["ok"] is True

    r = httpx.get(b + "/")
    assert r.status_code == 200 and 'id="pin-input"' in r.text

    r = httpx.get(b + "/play")
    assert r.status_code == 200 and 'id="screen-question"' in r.text

    r = httpx.get(b + "/admin")
    assert r.status_code == 200 and 'id="admin-login"' in r.text

    assert httpx.get(b + "/static/app.js").status_code == 200


def test_unknown_routes_404(server):
    b = server.base_url
    for path in ["/nope", "/api/nope", "/static/not-a-file.js", "/selfie"]:
        assert httpx.get(b + path).status_code == 404, path


def test_join_link_prefills_pin(server, admin):
    pin = admin.create(timer=15, per_round=8, reveal=2)
    r = httpx.get(f"{server.base_url}/j/{pin}")
    assert r.status_code == 200
    assert f'value="{pin}"' in r.text


def test_join_link_with_junk_pin_still_renders(server):
    r = httpx.get(server.base_url + "/j/zzzzzzzz")
    assert r.status_code == 200
    assert 'value="zzzz"' in r.text   # truncated to 4, page still usable


# ------------------------------------------------------- unauthenticated 403s

def test_player_endpoints_reject_unknown_token(server, admin):
    admin.create(timer=15, per_round=8, reveal=2)
    b = server.base_url
    assert httpx.post(b + "/api/rendered", params={"t": "nope"}).status_code == 403
    assert httpx.post(b + "/api/answer", params={"t": "nope"},
                      json={"answer": "x"}).status_code == 403
    assert httpx.get(b + "/selfie/anything", params={}).status_code == 403


def test_admin_endpoints_reject_without_cookie(server, admin):
    b = server.base_url
    for path in ["/api/admin/create", "/api/admin/start", "/api/admin/end_question",
                 "/api/admin/next_round", "/api/admin/delete_selfies"]:
        assert httpx.post(b + path, json={}).status_code == 403, path
    assert httpx.get(b + "/api/admin/state").status_code == 403


def test_no_game_yet_paths(fresh_server):
    """Before the organizer creates anything: state is 'none', players 404."""
    b = fresh_server.base_url
    assert httpx.get(b + "/api/state").json()["phase"] == "none"
    assert httpx.post(b + "/api/rendered", params={"t": "x"}).status_code == 404
    assert httpx.post(b + "/api/answer", params={"t": "x"},
                      json={"answer": 1}).status_code == 404
    # A selfie request before any game exists must fail closed, not 404-leak.
    assert httpx.get(b + "/selfie/abc").status_code == 403
    a = Admin(b)
    assert a.login().status_code == 200
    assert a.c.post("/api/admin/start").status_code == 400   # no game


# ------------------------------------------------------------- joining by UI

def test_join_by_typing_pin(server, admin, phones):
    pin = admin.create(timer=30, per_round=8, reveal=2)
    ph = phones.bare_context()
    ph.page.goto(server.base_url + "/")
    ph.page.fill("#pin-input", pin)
    ph.page.click("#btn-pin-next")
    ph.page.wait_for_selector("#step-name", state="visible", timeout=10000)
    ph.page.fill("#name-input", "Tilly")
    ph.page.click("#btn-name-next")
    ph.page.wait_for_selector("#step-selfie", state="visible", timeout=10000)
    ph.page.click("#btn-skip-selfie")
    ph.page.wait_for_url("**/play?t=*", timeout=10000)
    ph.page.wait_for_selector("#screen-lobby:not([hidden])", timeout=10000)
    assert ph.page.inner_text("#you-name") == "Tilly"
    assert any(p["name"] == "Tilly" for p in admin.state()["players"])


def test_join_by_link(server, admin, phones):
    """Tap the link the organizer shares. HOW-IT-WORKS section 10."""
    pin = admin.create(timer=30, per_round=8, reveal=2)
    ph = phones.bare_context()
    missing, errors = [], []
    ph.page.on("response", lambda r: missing.append(r.url)
               if r.status == 404 else None)
    ph.page.on("pageerror", lambda e: errors.append(str(e)))

    ph.page.goto(f"{server.base_url}/j/{pin}")
    assert ph.page.input_value("#pin-input") == pin
    ph.page.wait_for_timeout(500)
    assert not missing, (
        "the join-by-link page requested assets that 404: " + ", ".join(missing))
    assert not errors, f"join-by-link page raised: {errors}"

    # The PIN is already in, so the link lands straight on the name step.
    ph.page.wait_for_selector("#step-name", state="visible", timeout=10000)
    ph.page.fill("#name-input", "Bea")
    ph.page.click("#btn-name-next")
    ph.page.wait_for_selector("#step-selfie", state="visible", timeout=10000)
    ph.page.click("#btn-skip-selfie")
    ph.page.wait_for_selector("#screen-lobby:not([hidden])", timeout=10000)
    assert ph.page.inner_text("#you-name") == "Bea"


def test_wrong_pin_rejected_clearly(server, admin, phones):
    pin = admin.create(timer=30, per_round=8, reveal=2)
    wrong = "0000" if pin != "0000" else "1111"
    ph = phones.bare_context()
    ph.page.goto(server.base_url + "/")
    # A wrong PIN is now caught on the FIRST screen, before she is asked for
    # a name or a photo.
    ph.page.fill("#pin-input", wrong)
    ph.page.click("#btn-pin-next")
    ph.page.wait_for_selector("#join-error:not([hidden])", timeout=10000)
    assert "PIN" in ph.page.inner_text("#join-error")
    assert ph.page.url.endswith("/")          # not navigated into the game
    assert all(p["name"] != "Nope" for p in admin.state()["players"])


def test_name_is_required(server, admin, phones):
    pin = admin.create(timer=30, per_round=8, reveal=2)
    ph = phones.bare_context()
    ph.page.goto(server.base_url + "/")      # not /j/<pin>: see test_join_by_link
    ph.page.fill("#pin-input", pin)
    ph.page.click("#btn-pin-next")
    ph.page.wait_for_selector("#step-name", state="visible", timeout=10000)
    ph.page.click("#btn-name-next")
    ph.page.wait_for_selector("#join-error:not([hidden])", timeout=5000)
    assert "name" in ph.page.inner_text("#join-error").lower()


def test_join_api_rejects_wrong_pin_with_detail(server, admin):
    admin.create(timer=30, per_round=8, reveal=2)
    r = httpx.post(server.base_url + "/api/join",
                   data={"pin": "9999" if admin.state()["pin"] != "9999" else "8888",
                         "name": "X"})
    assert r.status_code == 400
    assert "PIN" in r.json()["detail"]


# --------------------------------------------------- selfie vs. initial disc

def test_selfie_skipped_shows_initial_disc(server, admin, phones):
    pin = admin.create(timer=30, per_round=8, reveal=2)
    ph, = phones(pin, 1, prefix="Zara")
    ph.page.wait_for_selector("#screen-lobby:not([hidden])", timeout=10000)
    ph.page.wait_for_selector("#lobby-roster .bubble", timeout=10000)
    av = ph.page.locator("#lobby-roster .bubble .avatar").first
    assert av.inner_text().strip() == "Z"
    assert av.locator("img").count() == 0
    bg = av.evaluate("e => getComputedStyle(e).backgroundColor")
    assert bg not in ("rgba(0, 0, 0, 0)", "transparent"), bg


def test_selfie_upload_is_stored_and_flagged(server, admin, phones):
    pin = admin.create(timer=30, per_round=8, reveal=2)
    before = {p.name for p in server.selfie_dir.iterdir() if p.is_file()}
    ph = join_api(server, pin, "Photo", selfie_bytes=jpeg_bytes())
    after = {p.name for p in server.selfie_dir.iterdir() if p.is_file()}
    new = after - before - {"game-state.json"}
    assert len(new) == 1, f"expected exactly one new stored selfie, got {new}"
    me = [p for p in admin.state()["players"] if p["id"] == ph.pid][0]
    assert me["selfie"] is True


def test_selfie_renders_in_the_bubble(server, admin, phones):
    """HOW-IT-WORKS section 9: the bubble carries her selfie when she took one.

    This is the end of the chain the upload test starts — stored, flagged in
    the snapshot, and actually fetched by the phone. Asserted on the network,
    because app.js removes an <img> that fails to load, which makes a broken
    selfie look exactly like a player who skipped the photo.
    """
    pin = admin.create(timer=30, per_round=8, reveal=2)
    ph, = phones(pin, 1, prefix="Snap", selfie=jpeg_bytes())
    hits = []
    ph.page.on("response",
               lambda r: hits.append((r.url, r.status)) if "/selfie/" in r.url
               else None)
    ph.page.reload()
    ph.page.wait_for_selector("#lobby-roster .bubble", timeout=10000)
    ph.page.wait_for_timeout(1500)

    assert hits, "the phone never asked for the selfie at all"
    for url, status in hits:
        tail = url.rsplit("/", 1)[-1].split("?")[0]
        assert "." not in tail, f"selfie URL has a file extension: {url}"
        assert status == 200, (
            f"the phone requested {url} with a valid session cookie and the "
            f"server answered {status} — no selfie ever appears in a bubble, "
            "a horse's head or the pick-a-player grid")
    assert ph.page.locator("#lobby-roster .bubble .avatar img").count() == 1, (
        "the selfie <img> was removed after failing to load")


def test_seeded_question_images_are_actually_served(server):
    """Three seeded multiple-choice questions carry an image (HOW-IT-WORKS section 8)."""
    import json
    from pathlib import Path
    seed = json.loads(
        (Path(__file__).resolve().parents[2] / "app" / "seed" /
         "questions.json").read_text())["questions"]
    imaged = [q for q in seed if q.get("image")]
    assert imaged, "no image questions in the seed at all"
    missing = [q["image"] for q in imaged
               if httpx.get(f"{server.base_url}/static/questions/{q['image']}"
                            ).status_code != 200]
    assert not missing, (
        "these questions point at images that are not on disk, so the phone "
        f"shows a broken-image icon: {missing}")
