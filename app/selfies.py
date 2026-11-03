"""Selfie storage — docs/HOW-IT-WORKS.md section 6.

These are photographs of real women taken in a bar, on a host the whole
internet can reach. Everything here is written to fail closed.
"""

import io
import os
import secrets
import time
from pathlib import Path

from PIL import Image, ImageOps

# BARGAMES_SELFIE_DIR is the app's DATA directory; photographs live in a
# subdirectory of it. They are separated because delete_all() wipes the photo
# directory wholesale, and the game snapshot and admin secret share the same
# volume — putting them side by side meant the organizer's "delete every photo"
# button also deleted the crash-recovery file and miscounted what it removed.
DATA_DIR = Path(os.getenv("BARGAMES_SELFIE_DIR", "/selfies"))
SELFIE_DIR = DATA_DIR / "photos"
MAX_EDGE = 256
RETENTION_SECONDS = 24 * 60 * 60


def _dir(create: bool = False) -> Path:
    if create:
        SELFIE_DIR.mkdir(parents=True, exist_ok=True)
    return SELFIE_DIR


def store(raw: bytes) -> str:
    """Normalise an uploaded photo and save it. Returns its opaque id.

    Re-encoding through a fresh Image object is what strips EXIF, and EXIF is
    the whole point: a phone camera writes GPS coordinates into a selfie, so an
    un-stripped upload would tell anyone who got hold of it exactly which bar
    these women were sitting in. `Image.frombytes`-style copying, rather than
    `save` on the loaded image, guarantees no metadata rides along.
    """
    img = Image.open(io.BytesIO(raw))

    # APPLY the EXIF orientation before stripping metadata. A phone stores a
    # portrait shot as landscape pixels plus an Orientation tag meaning "turn
    # this"; dropping the tag for privacy without honouring it first leaves
    # every selfie on its side.
    img = ImageOps.exif_transpose(img)

    img = img.convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE))

    clean = Image.new("RGB", img.size)
    clean.paste(img)

    selfie_id = secrets.token_urlsafe(12)
    clean.save(_dir(create=True) / selfie_id, format="JPEG", quality=82)
    return selfie_id


def read(selfie_id: str) -> bytes | None:
    # Reject anything that could climb out of the directory. The id comes from
    # a URL path segment, so this is the one place traversal could bite.
    if not selfie_id or "/" in selfie_id or "." in selfie_id or "\\" in selfie_id:
        return None
    # An unmounted or misconfigured volume must read as "no photo", never
    # raise: an exception here surfaces as a 500 with a stack trace on a host
    # the whole internet can reach, instead of the intended 403.
    try:
        path = _dir() / selfie_id
        if not path.is_file():
            return None
        return path.read_bytes()
    except OSError:
        return None


def delete_one(selfie_id: str) -> bool:
    """Remove a single photo, e.g. one the player has replaced."""
    if not selfie_id or "/" in selfie_id or "." in selfie_id or "\\" in selfie_id:
        return False
    try:
        (_dir() / selfie_id).unlink()
        return True
    except OSError:
        return False


def delete_all() -> int:
    """The admin phone's panic button. Returns how many were removed."""
    removed = 0
    try:
        entries = list(_dir().iterdir())
    except OSError:
        return 0
    for path in entries:
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def purge_older_than(seconds: int = RETENTION_SECONDS) -> int:
    cutoff = time.time() - seconds
    removed = 0
    try:
        entries = list(_dir().iterdir())
    except OSError:
        return 0
    for path in entries:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed
