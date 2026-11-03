"""Joining is three screens: PIN, then name, then selfie.

One long form asked a guest standing at a bar to deal with a PIN, a name and a
camera at once. Each screen now asks for exactly one thing.
"""

import pytest

from conftest import jpeg_bytes


@pytest.fixture
def page(server, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    p = ctx.new_page()
    p.goto(f"{server.base_url}/", wait_until="domcontentloaded")
    yield p
    ctx.close()


def visible_step(page) -> str:
    for step in ("step-pin", "step-name", "step-selfie"):
        if page.locator(f"#{step}").is_visible():
            return step
    return "none"


class TestOneThingAtATime:
    def test_it_opens_on_the_pin_and_nothing_else(self, page):
        assert visible_step(page) == "step-pin"
        assert page.locator("#name-input").is_hidden()
        assert page.locator("#selfie-input").is_hidden()

    def test_a_good_pin_moves_on_to_the_name(self, page, admin):
        pin = admin.create()
        page.reload(wait_until="domcontentloaded")
        page.fill("#pin-input", pin)
        page.click("#btn-pin-next")
        page.wait_for_selector("#step-name", state="visible", timeout=5000)
        assert visible_step(page) == "step-name"

    def test_a_wrong_pin_says_so_and_stays_put(self, page, admin):
        admin.create()
        page.reload(wait_until="domcontentloaded")
        page.fill("#pin-input", "0000")
        page.click("#btn-pin-next")
        page.wait_for_selector("#join-error", state="visible", timeout=5000)
        assert visible_step(page) == "step-pin"

    def test_a_name_is_required_before_the_selfie(self, page, admin):
        pin = admin.create()
        page.reload(wait_until="domcontentloaded")
        page.fill("#pin-input", pin)
        page.click("#btn-pin-next")
        page.wait_for_selector("#step-name", state="visible", timeout=5000)
        page.click("#btn-name-next")
        assert visible_step(page) == "step-name"

    def test_a_name_moves_on_to_the_selfie(self, page, admin):
        pin = admin.create()
        page.reload(wait_until="domcontentloaded")
        page.fill("#pin-input", pin)
        page.click("#btn-pin-next")
        page.wait_for_selector("#step-name", state="visible", timeout=5000)
        page.fill("#name-input", "Robin")
        page.click("#btn-name-next")
        page.wait_for_selector("#step-selfie", state="visible", timeout=5000)


class TestFinishing:
    def walk_to_selfie(self, page, admin, name="Robin"):
        pin = admin.create()
        page.reload(wait_until="domcontentloaded")
        page.fill("#pin-input", pin)
        page.click("#btn-pin-next")
        page.wait_for_selector("#step-name", state="visible", timeout=5000)
        page.fill("#name-input", name)
        page.click("#btn-name-next")
        page.wait_for_selector("#step-selfie", state="visible", timeout=5000)

    def test_skip_joins_without_a_photo(self, page, admin):
        self.walk_to_selfie(page, admin)
        page.click("#btn-skip-selfie")
        page.wait_for_url("**/play**", timeout=8000)

    def test_taking_a_photo_joins_with_one(self, page, admin):
        self.walk_to_selfie(page, admin, name="Sam")
        page.set_input_files("#selfie-input", {
            "name": "me.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()})
        page.click("#btn-join")
        page.wait_for_url("**/play**", timeout=8000)

    def test_she_can_go_back_and_change_her_name(self, page, admin):
        self.walk_to_selfie(page, admin)
        page.click("#btn-selfie-back")
        page.wait_for_selector("#step-name", state="visible", timeout=5000)
        assert page.input_value("#name-input") == "Robin"


class TestJoinByLink:
    def test_a_link_with_the_pin_skips_straight_to_the_name(self, server, browser, admin):
        pin = admin.create()
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        p = ctx.new_page()
        p.goto(f"{server.base_url}/j/{pin}", wait_until="domcontentloaded")
        p.wait_for_selector("#step-name", state="visible", timeout=5000)
        assert visible_step(p) == "step-name"
        ctx.close()


class TestARejectedPinLooksRejected:
    """A stale link is the likeliest failure tonight — the organizer shares a
    link, then starts a new game, and the old link still has the old PIN in it.
    The refusal has to be visible, or Continue reads as a dead button."""

    def test_the_error_appears_inside_the_step_the_guest_is_looking_at(
            self, page, admin):
        admin.create()
        page.reload(wait_until="domcontentloaded")
        page.fill("#pin-input", "0000")
        page.click("#btn-pin-next")
        page.wait_for_selector("#join-error", state="visible", timeout=5000)
        inside = page.evaluate(
            """() => document.querySelector('#step-pin')
                     .contains(document.querySelector('#join-error'))""")
        assert inside, "the refusal renders outside the visible step"

    def test_the_error_is_on_screen_not_below_the_fold(self, page, admin):
        admin.create()
        page.reload(wait_until="domcontentloaded")
        page.fill("#pin-input", "0000")
        page.click("#btn-pin-next")
        page.wait_for_selector("#join-error", state="visible", timeout=5000)
        page.wait_for_timeout(600)          # let the scroll settle
        in_view = page.evaluate("""() => {
          const r = document.querySelector('#join-error').getBoundingClientRect();
          return r.top >= 0 && r.bottom <= window.innerHeight;
        }""")
        assert in_view, "the guest cannot see why her PIN was refused"


class TestTheSelfiePreview:
    """Taking a photo must visibly do something.

    This regressed once: the preview lived inside initJoin(), and rewriting
    that whole function for the wizard deleted it. Taking a photo then looked
    like nothing had happened at all.
    """

    def walk_to_selfie(self, page, admin):
        pin = admin.create()
        page.reload(wait_until="domcontentloaded")
        page.fill("#pin-input", pin)
        page.click("#btn-pin-next")
        page.wait_for_selector("#step-name", state="visible", timeout=5000)
        page.fill("#name-input", "Pat")
        page.click("#btn-name-next")
        page.wait_for_selector("#step-selfie", state="visible", timeout=5000)

    def test_choosing_a_photo_shows_it(self, page, admin):
        from conftest import jpeg_bytes
        self.walk_to_selfie(page, admin)
        assert page.locator("#selfie-drop img").count() == 0
        page.set_input_files("#selfie-input", {
            "name": "me.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()})
        page.wait_for_selector("#selfie-drop img", timeout=5000)
        assert page.locator("#selfie-drop img").count() == 1

    def test_the_skip_button_goes_away_once_she_has_a_photo(self, page, admin):
        from conftest import jpeg_bytes
        self.walk_to_selfie(page, admin)
        assert page.locator("#btn-skip-selfie").is_visible()
        page.set_input_files("#selfie-input", {
            "name": "me.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()})
        page.wait_for_selector("#btn-skip-selfie", state="hidden", timeout=5000)

    def test_the_photo_still_uploads_after_the_preview(self, page, admin):
        from conftest import jpeg_bytes
        self.walk_to_selfie(page, admin)
        page.set_input_files("#selfie-input", {
            "name": "me.jpg", "mimeType": "image/jpeg", "buffer": jpeg_bytes()})
        page.wait_for_selector("#selfie-drop img", timeout=5000)
        page.click("#btn-join")
        page.wait_for_url("**/play**", timeout=8000)
        assert any(p["selfie"] for p in admin.state()["players"])
