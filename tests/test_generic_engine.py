"""bargames is a GENERIC trivia engine.

No event-specific or occasion-specific wording may appear in the app's code, logic, seed content, routes, ids or filenames. The
occasion lives only in swappable data — the theme, the badge and the game title.

This is a guard test, not a unit test. It exists so the rule survives the next
person (or agent) who adds a "fun" question at 2am.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

# Occasion-specific vocabulary. Deliberately broad: the point is to fail loudly
# and make someone think, not to be clever about near-misses.
FORBIDDEN = re.compile(
    r"bachelor|bachelorette|bride|groom|wedding|hen\s*(party|do)|stag\s*(party|do)"
    r"|maid\s+of\s+honou?r|nuptial|engagement\s+party",
    re.IGNORECASE,
)

SCANNED = ["app", "Dockerfile", "requirements.txt"]


def files_to_scan():
    for entry in SCANNED:
        path = ROOT / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            for f in path.rglob("*"):
                if f.is_file() and f.suffix in {
                    ".py", ".json", ".html", ".js", ".css", ".yml", ".yaml", ".txt", ""
                } and ".venv" not in f.parts:
                    yield f


class TestNoOccasionSpecificContent:
    def test_the_engine_ships_no_occasion_specific_wording(self):
        offenders = []
        for f in files_to_scan():
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary asset
            for n, line in enumerate(text.splitlines(), 1):
                if FORBIDDEN.search(line):
                    offenders.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()[:90]}")
        assert not offenders, (
            "Occasion-specific wording found in the engine. It belongs in the "
            "theme, badge or title (all data), never in code or seed:\n  "
            + "\n  ".join(offenders)
        )

    def test_no_occasion_specific_filenames(self):
        offenders = [
            str(f.relative_to(ROOT)) for f in files_to_scan() if FORBIDDEN.search(f.name)
        ]
        assert not offenders, f"Occasion-specific filenames: {offenders}"


class TestBrandingIsData:
    def test_branding_lives_in_a_swappable_data_file(self):
        import json
        branding = json.loads((ROOT / "app" / "seed" / "branding.json").read_text())
        assert {"title", "badge", "theme"} <= set(branding)

    def test_the_default_branding_is_generic(self):
        import json
        branding = json.loads((ROOT / "app" / "seed" / "branding.json").read_text())
        assert not FORBIDDEN.search(json.dumps(branding))
