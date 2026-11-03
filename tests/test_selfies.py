"""Selfie handling — docs/HOW-IT-WORKS.md section 6. Fail-closed, asserted not assumed."""

import io
from pathlib import Path

import pytest
from PIL import Image

import selfies


@pytest.fixture(autouse=True)
def tmp_selfie_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(selfies, "SELFIE_DIR", tmp_path / "selfies")


def photo_with_gps() -> bytes:
    """A JPEG carrying EXIF GPS, like a real phone camera produces."""
    img = Image.new("RGB", (800, 800), (120, 90, 200))
    exif = img.getexif()
    exif[0x8825] = {1: "N", 2: (43.0, 39.0, 0.0)}  # GPSInfo
    exif[0x010F] = "TestPhone"                      # Make
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class TestStoring:
    def test_a_stored_photo_can_be_read_back(self):
        sid = selfies.store(photo_with_gps())
        assert selfies.read(sid) is not None

    def test_exif_does_not_survive_upload(self):
        # The location leak this whole module exists to prevent.
        sid = selfies.store(photo_with_gps())
        stored = Image.open(io.BytesIO(selfies.read(sid)))
        assert not dict(stored.getexif())

    def test_large_photos_are_downscaled(self):
        sid = selfies.store(photo_with_gps())
        stored = Image.open(io.BytesIO(selfies.read(sid)))
        assert max(stored.size) <= selfies.MAX_EDGE

    def test_ids_are_unguessable_and_unique(self):
        a, b = selfies.store(photo_with_gps()), selfies.store(photo_with_gps())
        assert a != b and len(a) >= 12

    def test_stored_filenames_carry_no_extension(self):
        # CDNs such as Cloudflare cache by file extension; nothing here should look like
        # an image file to a caching layer.
        sid = selfies.store(photo_with_gps())
        assert "." not in sid


class TestReadingFailsClosed:
    def test_an_unknown_id_reads_as_nothing(self):
        assert selfies.read("nope") is None

    def test_an_empty_id_reads_as_nothing(self):
        assert selfies.read("") is None

    @pytest.mark.parametrize("evil", ["../etc/passwd", "a/b", "..", "x.jpg", "a\\b"])
    def test_path_traversal_is_refused(self, evil):
        assert selfies.read(evil) is None


class TestDeletion:
    def test_delete_all_removes_every_photo(self):
        selfies.store(photo_with_gps())
        sid = selfies.store(photo_with_gps())
        assert selfies.delete_all() == 2
        assert selfies.read(sid) is None

    def test_purge_leaves_fresh_photos_alone(self):
        sid = selfies.store(photo_with_gps())
        assert selfies.purge_older_than(3600) == 0
        assert selfies.read(sid) is not None

    def test_purge_removes_photos_past_retention(self):
        sid = selfies.store(photo_with_gps())
        assert selfies.purge_older_than(-1) == 1
        assert selfies.read(sid) is None


class TestStorageErrorsStillFailClosed:
    def test_reading_when_the_directory_is_unusable_returns_nothing(self, monkeypatch):
        # A misconfigured or unmounted volume must read as "no photo", not blow
        # up: an exception here would surface as a 500 with a stack trace on a
        # host the whole internet can reach, instead of the intended 403.
        monkeypatch.setattr(selfies, "SELFIE_DIR", Path("/proc/nope/selfies"))
        assert selfies.read("anything") is None

    def test_purging_an_unusable_directory_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(selfies, "SELFIE_DIR", Path("/proc/nope/selfies"))
        assert selfies.purge_older_than(0) == 0


class TestOrientation:
    """Phones store photos sideways with an EXIF tag saying which way is up.

    Stripping EXIF for privacy throws that tag away, so the rotation has to be
    APPLIED to the pixels first — otherwise every portrait selfie taken on a
    phone comes out on its side.
    """

    def sideways_photo(self) -> bytes:
        """Landscape pixels + Orientation=6, exactly what a phone writes for a
        portrait shot."""
        img = Image.new("RGB", (400, 200), (90, 140, 200))
        img.paste(Image.new("RGB", (40, 40), (255, 0, 0)), (0, 0))  # corner mark
        exif = img.getexif()
        exif[0x0112] = 6                      # rotate 90° CW to display
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        return buf.getvalue()

    def test_a_sideways_photo_is_stood_upright(self):
        sid = selfies.store(self.sideways_photo())
        stored = Image.open(io.BytesIO(selfies.read(sid)))
        w, h = stored.size
        assert h > w, f"stored {w}x{h}: the photo is still on its side"

    def test_the_orientation_tag_does_not_survive(self):
        # Applied, then dropped — leaving it would double-rotate in some viewers.
        sid = selfies.store(self.sideways_photo())
        stored = Image.open(io.BytesIO(selfies.read(sid)))
        assert 0x0112 not in dict(stored.getexif())

    def test_an_already_upright_photo_is_left_alone(self):
        img = Image.new("RGB", (200, 400), (10, 10, 10))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        sid = selfies.store(buf.getvalue())
        stored = Image.open(io.BytesIO(selfies.read(sid)))
        assert stored.size[1] > stored.size[0]
