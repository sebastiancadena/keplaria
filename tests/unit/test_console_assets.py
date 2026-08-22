"""Brand values are constants, and an external font request is a defect.

The palette assertions exist so a hand-edited stylesheet cannot quietly drift
from the design system. The no-external-reference assertion exists because a
third-party request from a public page is both a privacy leak and a dependency
that can fail at the worst possible moment.
"""

from __future__ import annotations

from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[2] / "console"
CSS = CONSOLE / "static" / "app.css"

PALETTE = {
    "--void": "#0B1020",
    "--ink": "#111827",
    "--amber": "#F59E0B",
    "--amber-bright": "#FBBF24",
    "--star": "#F8FAFC",
    "--muted": "#64748B",
    "--clear": "#34D399",
    "--blocked": "#F87171",
}


def test_every_palette_token_is_declared_with_its_exact_value():
    css = CSS.read_text()
    for token, value in PALETTE.items():
        assert f"{token}: {value}" in css, f"{token} missing or drifted"


def test_no_stylesheet_or_template_references_an_external_host():
    """Self-hosted only. No CDN, no font service, no remote image."""
    files = list((CONSOLE / "static").glob("*.css"))
    files += list((CONSOLE / "templates").glob("*.html"))
    assert files, "no stylesheet or template found to check"
    for path in files:
        text = path.read_text()
        for needle in ("http://", "https://", "//fonts.", "cdn."):
            assert needle not in text, f"{path.name} references {needle}"


def test_the_three_faces_are_vendored_with_their_licences():
    fonts = CONSOLE / "static" / "fonts"
    for face in ("SpaceGrotesk.ttf", "Inter.ttf", "JetBrainsMono.ttf"):
        assert (fonts / face).exists(), f"{face} not vendored"
    licences = list(fonts.glob("*OFL*.txt"))
    assert len(licences) == 3, "each vendored face needs its OFL notice"


def test_third_party_records_the_fonts():
    third_party = (CONSOLE.parent / "THIRD_PARTY.md").read_text()
    for face in ("Space Grotesk", "Inter", "JetBrains Mono"):
        assert face in third_party, f"{face} missing from the provenance ledger"


def test_the_header_lockup_clears_the_brand_minimum_size():
    """The lockup's viewBox carries its own clear space, and that is a trap.

    Per the brand guidelines, clear space is expressed by EXTENDING the
    viewBox rather than by adding empty geometry -- 0.5x the icon height on
    every side. So the artwork occupies only 74.74% of the element's width
    and HALF its height, and an element sized to the 96px lockup minimum
    renders a 72px mark that misses it.

    The console shipped at height="28" for weeks, which is a 41px-wide mark
    against a 96px minimum. It looked like a speck and nobody measured it.

        artwork_width = element_height * (1013.52 / 512) * (757.52 / 1013.52)

    Minimum element height for a 96px artwork is therefore ~65px.
    """
    import re
    from pathlib import Path

    html = Path("console/templates/base.html").read_text()
    match = re.search(r'keplaria-lockup-horizontal-dark\.svg"[^>]*height="(\d+)"',
                      html)
    assert match, "the header lockup must declare an explicit height"
    element_height = int(match.group(1))
    artwork_width = element_height * (1013.52 / 512) * (757.52 / 1013.52)
    assert artwork_width >= 96, (
        f'height="{element_height}" renders a {artwork_width:.0f}px lockup; '
        "the brand minimum is 96px wide"
    )
