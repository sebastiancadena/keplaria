"""Every image that ships `app/` must also ship the data directories `app/`
resolves at runtime.

This is the third time a runtime-required directory outside `app/` has had to
be reasoned about by hand: `policy/` and `fixtures/` each earned a comment in
a Dockerfile after the fact, and `catalog/` shipped on 2026-08-22 with no COPY
line at all, which turned `/fleet` into a 503 on the public console the moment
it was deployed.

The comments did not prevent it because a comment is not a check. This test
DISCOVERS the required directories from the code rather than listing them, so
the next `Path(__file__).resolve().parent.parent / "..."` constant is covered
the day it is written and not the day it breaks production.

The check is deliberately blunt: any image carrying `app/` must carry all of
them, rather than only the ones its own entrypoint happens to import today.
The directories total well under 100K, and the failure they cause is a silent
fail-closed outage — every case blocked or quarantined, with the container
building and starting cleanly. Paying a few kilobytes to make that
unreachable is the trade this repo has already chosen twice.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# `COPY <src> <dst>`, ignoring `COPY --from=...` stage/registry pulls, which
# bring in binaries rather than repo content.
_COPY = re.compile(r"^\s*COPY\s+(?!--from=)(?P<src>\S+)\s+\S+", re.MULTILINE)

# `Path(__file__).resolve().parent.parent / "<dir>"` inside app/ — a path that
# escapes the package and therefore must be copied separately.
_ESCAPES_APP = re.compile(
    r"""Path\(__file__\)\.resolve\(\)\.parent\.parent\s*/\s*["'](?P<dir>[^"']+)["']"""
)


def _dockerfiles() -> list[Path]:
    found = sorted(REPO_ROOT.glob("Dockerfile")) + sorted(
        REPO_ROOT.glob("*/Dockerfile")
    )
    assert found, "no Dockerfiles discovered — the glob is wrong, not the repo"
    return found


def _copied_sources(dockerfile: Path) -> set[str]:
    text = dockerfile.read_text()
    return {m.group("src").lstrip("./").rstrip("/") for m in _COPY.finditer(text)}


def runtime_data_dirs() -> set[str]:
    """Directories outside `app/` that `app/` reads at runtime."""
    dirs: set[str] = set()
    for source in (REPO_ROOT / "app").rglob("*.py"):
        for match in _ESCAPES_APP.finditer(source.read_text()):
            dirs.add(match.group("dir"))
    return dirs


def test_the_discovery_itself_finds_something() -> None:
    """A discovery that silently finds nothing would make every other
    assertion in this file vacuously true — the exact shape of containment
    check this repo has been bitten by before."""
    found = runtime_data_dirs()
    assert found, "discovered no runtime data directories — the regex has drifted"
    assert "catalog" in found, (
        "app/catalog.py's DEFAULT_CATALOG_PATH is the case that motivated this "
        "test; if it is no longer discovered, the regex no longer matches the code"
    )


@pytest.mark.parametrize(
    "dockerfile", _dockerfiles(), ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_images_carrying_app_also_carry_its_runtime_data(dockerfile: Path) -> None:
    copied = _copied_sources(dockerfile)
    if "app" not in copied:
        pytest.skip(f"{dockerfile.name} does not ship app/")

    missing = sorted(runtime_data_dirs() - copied)
    assert not missing, (
        f"{dockerfile.relative_to(REPO_ROOT)} copies app/ but not {missing}. "
        f"app/ resolves those at runtime, so the loader fails closed in the "
        f"container while every test here still passes — the container is the "
        f"only place the difference is visible."
    )


# --- dependency pins -------------------------------------------------------
#
# Both Dockerfiles install from a per-service pyproject.toml, which has version
# FLOORS, not pins. On 2026-08-24 google-cloud-firestore 2.29.0 (and its
# api-core / auth siblings) was released between two console builds two hours
# apart; the second image resolved it and every Firestore query failed with
# "400 Invalid database id %28default%29". The fix is a constraints file
# exported from uv.lock; these tests keep it wired in and keep it fresh.

_ROOT = Path(__file__).resolve().parents[2]
_CONSTRAINTS = _ROOT / "constraints.txt"


def _pins(text: str) -> dict[str, str]:
    return dict(
        line.split("==", 1) for line in text.splitlines()
        if "==" in line and not line.startswith(("#", "-"))
    )


@pytest.mark.parametrize("dockerfile", sorted(_ROOT.glob("*/Dockerfile")), ids=lambda p: p.parent.name)
def test_every_pip_install_is_constrained(dockerfile: Path) -> None:
    text = dockerfile.read_text()
    installs = [l for l in text.splitlines() if "pip install" in l]
    assert installs, f"{dockerfile}: no install line found"
    for line in installs:
        assert "-c /srv/constraints.txt" in line, f"{dockerfile}: unconstrained install: {line}"
    assert "COPY constraints.txt /srv/constraints.txt" in text


def test_constraints_match_the_lock() -> None:
    import subprocess
    exported = subprocess.run(
        ["uv", "export", "--frozen", "--no-hashes", "--no-dev", "--no-emit-project",
         "--format", "requirements-txt"],
        cwd=_ROOT, check=True, capture_output=True, text=True,
    ).stdout
    want, have = _pins(exported), _pins(_CONSTRAINTS.read_text())
    drift = {k: (have.get(k), v) for k, v in want.items() if have.get(k) != v}
    assert not drift, (
        "constraints.txt drifted from uv.lock; regenerate with\n"
        "  uv export --frozen --no-hashes --no-dev --no-emit-project --format requirements-txt"
        " | grep -vE '^(#|-e|\\s*$)' > constraints.txt\n" + str(drift)
    )
