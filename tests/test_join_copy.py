"""Player-facing copy.

The racing metaphor must not appear in anything a
first-time player reads. It is confusing before you have seen the leaderboard
and it reads as unprofessional. Internal CSS class and JS variable names are
not player-facing and are left alone.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
METAPHOR = re.compile(r"\bhorses?\b|\bsaddle\w*\b|\bgallop\w*\b|\bjockey\w*\b", re.I)


def visible_text_of(html: str) -> str:
    """Rough but honest: strip tags and attributes, keep what a person reads."""
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style\b.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"\{#.*?#\}", " ", html, flags=re.S)       # jinja comments
    html = re.sub(r"<[^>]+>", " ", html)
    return html


class TestNoRacingMetaphorInPlayerCopy:
    @pytest.mark.parametrize("name", ["join.html", "play.html"])
    def test_templates_players_see(self, name):
        text = visible_text_of((ROOT / "app" / "templates" / name).read_text())
        assert not METAPHOR.search(text), (
            f"{name} shows the racing metaphor to players: "
            f"{METAPHOR.search(text).group(0)!r}"
        )

    def test_strings_the_client_puts_on_screen(self):
        """Prose only.

        CSS class names and selectors (".horse", "saddle") are internal — a
        player never sees them, and renaming them would churn the stylesheet
        for no benefit to anyone reading the screen. Prose is what reaches a
        player, and prose has spaces in it.
        """
        js = (ROOT / "app" / "static" / "app.js").read_text()
        literals = [m[1] for m in re.findall(r'(["\'])((?:\\.|(?!\1).)*)\1', js)]
        prose = [s for s in literals if " " in s.strip()]
        offenders = [s for s in prose if METAPHOR.search(s)]
        assert not offenders, f"player-facing strings use the metaphor: {offenders}"

    def test_the_organizer_runbook_avoids_it_too(self):
        text = (ROOT / "docs" / "RUNBOOK.md").read_text()
        assert not METAPHOR.search(text)
