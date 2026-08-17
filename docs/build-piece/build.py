"""Build the public build piece: inline the brand fonts, harden the encoding.

    uv run python docs/build-piece/build.py

Writes article.html next to the source. That output is intentionally NOT
committed: it is regenerable and ~1.55 MB, almost all of it font bytes that
already live in the keplaria-assets repo.

Two steps here are load-bearing rather than tidy-ups:

1. FONTS ARE INLINED, NEVER LINKED. The artifact CSP blocks font CDNs, and a
   blocked webfont does not error — it falls back silently. The page would
   render in the wrong faces with nothing to indicate it.

2. NON-ASCII BECOMES NUMERIC ENTITIES. Em dashes, curly apostrophes and the
   arrow rendered as mojibake when the page was served without an explicit
   charset. Entities are charset-independent, so the text is correct wherever
   it lands instead of correct only where it was tested.
"""

from __future__ import annotations

import base64
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "article.src.html"
OUT = HERE / "article.html"
VENDOR = pathlib.Path.home() / "dev/git/keplaria-assets/vendor/fonts"

# (css family name, vendored file stem). The " KP" suffix keeps these from
# colliding with any same-named face the reader happens to have installed.
FACES = [
    ("Space Grotesk KP", "SpaceGrotesk"),
    ("Inter KP", "Inter"),
    ("JetBrains Mono KP", "JetBrainsMono"),
]


def load(stem: str) -> tuple[bytes, str]:
    """Prefer woff2 if the assets repo ever ships it; fall back to the ttf."""
    woff2 = VENDOR / f"{stem}.woff2"
    if woff2.exists():
        return woff2.read_bytes(), "woff2"
    ttf = VENDOR / f"{stem}.ttf"
    if not ttf.exists():
        raise SystemExit(
            f"missing font {ttf}\n"
            "The sibling keplaria-assets repo must be checked out at "
            f"{VENDOR.parent.parent} for this build."
        )
    return ttf.read_bytes(), "truetype"


def main() -> int:
    blocks, total = [], 0
    for family, stem in FACES:
        data, fmt = load(stem)
        total += len(data)
        b64 = base64.b64encode(data).decode()
        mime = "font/woff2" if fmt == "woff2" else "font/ttf"
        blocks.append(
            f'@font-face{{font-family:"{family}";'
            f'src:url(data:{mime};base64,{b64}) format("{fmt}");'
            f"font-weight:100 900;font-style:normal;font-display:swap;}}"
        )

    html = SRC.read_text().replace("/*__FONTS__*/", "\n".join(blocks))

    # Escape only after </style>: the base64 payload above is already ASCII,
    # and running the whole document through would waste a pass over ~1.5 MB.
    head, sep, rest = html.partition("</style>")
    rest = "".join(c if ord(c) < 128 else f"&#{ord(c)};" for c in rest)
    html = head + sep + rest

    OUT.write_text(html)
    print(f"fonts {total / 1024:.0f}KB raw -> page {OUT.stat().st_size / 1024 / 1024:.2f}MB")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
