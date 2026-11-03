"""The organizer's way in from the public join page, driven in a real browser.

Presence in the HTML is not the same as working — twice tonight an element
existed, a test asserted it existed, and the thing it was for was still broken.
So this clicks it.
"""

import pytest

ADMIN_PW = "testpw"


@pytest.fixture
def join_page(server, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(f"{server.base_url}/", wait_until="domcontentloaded")
    yield page
    ctx.close()


def test_the_prompt_is_hidden_until_the_icon_is_tapped(join_page):
    assert join_page.locator("#admin-prompt").is_hidden()
    join_page.click("#admin-entry")
    join_page.wait_for_selector("#admin-prompt:not([hidden])", timeout=5000)


def test_the_icon_does_not_cover_the_pin_field(join_page):
    # A guest's thumb goes to the PIN. The organizer's door must not be in
    # the way of it.
    icon = join_page.locator("#admin-entry").bounding_box()
    pin = join_page.locator("#pin-input").bounding_box()
    overlaps = not (icon["y"] + icon["height"] <= pin["y"]
                    or pin["y"] + pin["height"] <= icon["y"]
                    or icon["x"] + icon["width"] <= pin["x"]
                    or pin["x"] + pin["width"] <= icon["x"])
    assert not overlaps, "the organizer icon overlaps the PIN field"


def test_the_tap_target_is_big_enough_to_hit(join_page):
    box = join_page.locator("#admin-entry").bounding_box()
    assert box["width"] >= 40 and box["height"] >= 40, f"tap target {box}"


def test_a_wrong_password_is_rejected_inline_and_stays_on_the_page(join_page):
    join_page.click("#admin-entry")
    join_page.fill("#admin-prompt-input", "not-the-password")
    join_page.click("#admin-prompt-go")
    join_page.wait_for_selector("#admin-prompt-error:not([hidden])", timeout=5000)
    assert "/admin" not in join_page.url
    assert join_page.locator("#admin-prompt-error").inner_text().strip()


def test_the_right_password_goes_to_the_admin_page(join_page):
    join_page.click("#admin-entry")
    join_page.fill("#admin-prompt-input", ADMIN_PW)
    join_page.click("#admin-prompt-go")
    join_page.wait_for_url("**/admin", timeout=8000)
    # And she arrives signed in, not at the login screen again.
    join_page.wait_for_selector("#admin-panel:not([hidden])", timeout=8000)


def test_escape_dismisses_it(join_page):
    join_page.click("#admin-entry")
    join_page.wait_for_selector("#admin-prompt:not([hidden])", timeout=5000)
    join_page.keyboard.press("Escape")
    join_page.wait_for_selector("#admin-prompt", state="hidden", timeout=5000)


def test_tapping_outside_dismisses_it(join_page):
    join_page.click("#admin-entry")
    join_page.wait_for_selector("#admin-prompt:not([hidden])", timeout=5000)
    join_page.click("#pin-input")
    join_page.wait_for_selector("#admin-prompt", state="hidden", timeout=5000)


def test_a_guest_can_still_join_normally_with_the_door_present(server, browser, admin):
    pin = admin.create()
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(f"{server.base_url}/", wait_until="domcontentloaded")
    page.fill("#pin-input", pin)
    page.click("#btn-pin-next")
    page.wait_for_selector("#step-name", state="visible", timeout=8000)
    page.fill("#name-input", "Guest")
    page.click("#btn-name-next")
    page.wait_for_selector("#step-selfie", state="visible", timeout=8000)
    page.click("#btn-skip-selfie")
    page.wait_for_url("**/play**", timeout=8000)
    ctx.close()
