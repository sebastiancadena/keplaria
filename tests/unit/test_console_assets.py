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
    files += list((CONSOLE / "static").glob("*.svg"))
    assert files, "no stylesheet or template found to check"
    for path in files:
        # The SVG XML namespace is itself an "http://" literal that names no
        # live host -- neutralise it before scanning so a vendored SVG isn't
        # flagged for a URI nobody's browser ever resolves.
        text = path.read_text().replace("http://www.w3.org/", "")
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


# The lockup viewBox carries its own clear space (brand guidelines §4: 0.5x the
# icon height per side, expressed by EXTENDING the viewBox rather than by adding
# geometry). So for keplaria-lockup-horizontal-dark.svg the artwork is 74.74% of
# the element's width and HALF its height, and the §5 minimum of 96px wide needs
# an element ~65px tall. Sized by eye it comes out roughly half that -- which is
# what shipped, on three surfaces, before anyone measured.
_VIEWBOX_W, _VIEWBOX_H = 1013.52, 512.0
_ARTWORK_W = 757.52
_MIN_ARTWORK_WIDTH = 96.0


def _artwork_width(element_height: float) -> float:
    return element_height * (_VIEWBOX_W / _VIEWBOX_H) * (_ARTWORK_W / _VIEWBOX_W)


def _artwork_width_from_element_width(element_width: float) -> float:
    return element_width * (_ARTWORK_W / _VIEWBOX_W)


def test_every_surface_that_embeds_the_lockup_clears_the_brand_minimum():
    """One row per surface. Add a row when you add a surface.

    A per-file test would have caught the console and left the site and the
    two diagrams under-sized, which is exactly how this went wrong: the fix
    was applied where the complaint landed rather than everywhere the asset
    is used.
    """
    import re
    from pathlib import Path

    surfaces = {
        # (path, regex capturing the size, whether the capture is a HEIGHT)
        "console header": (
            "console/templates/base.html",
            r'keplaria-lockup-horizontal-dark\.svg"[^>]*height="(\d+)"', True),
        # The stylesheet wins over the HTML height attribute at render time,
        # so the attribute row above proves nothing on its own: the console
        # shipped a 72px attribute and a 28px CSS rule, and rendered ~41px
        # of artwork while this test stayed green. Whatever the CSS says the
        # element is, THAT is the surface.
        "console header (css)": (
            "console/static/app.css",
            r'\.masthead__brand img \{ height: (\d+)px', True),
        "keplaria.com header": (
            "site/build_site.py", r"header svg\{height:(\d+)px", True),
        "architecture diagram": (
            "docs/architecture/build.py",
            r'#keplaria-lockup"[^\']*width="(\d+)"', False),
        "judge diagram": (
            "docs/architecture/build_judge_diagram.py",
            r'#keplaria-lockup"[^\']*width="(\d+)"', False),
    }

    undersized = []
    for name, (path, pattern, is_height) in surfaces.items():
        text = Path(path).read_text()
        match = re.search(pattern, text)
        assert match, f"{name}: no lockup size found in {path}"
        value = float(match.group(1))
        artwork = (_artwork_width(value) if is_height
                   else _artwork_width_from_element_width(value))
        if artwork < _MIN_ARTWORK_WIDTH:
            undersized.append(f"{name} renders a {artwork:.0f}px lockup")
    assert not undersized, (
        "brand minimum is 96px wide: " + "; ".join(undersized)
    )


def test_the_console_orientation_figure_is_the_generated_one():
    """The console ships a COPY of docs/architecture/orientation.svg (the
    Dockerfile copies console/, not docs/). A copy drifts; this pins it."""
    generated = CONSOLE.parent / "docs" / "architecture" / "orientation.svg"
    shipped = CONSOLE / "static" / "orientation.svg"
    assert shipped.read_bytes() == generated.read_bytes()


def test_the_orientation_figure_carries_no_text_below_the_floor():
    """Nothing carrying meaning below 16px on the 1200-wide canvas: the
    README renders it at 880, the console wider."""
    import re
    svg = (CONSOLE.parent / "docs" / "architecture" / "orientation.svg").read_text()
    sizes = [int(s) for s in re.findall(r'font-size="(\d+)"', svg)]
    assert sizes, "no text found"
    assert min(sizes) >= 16
