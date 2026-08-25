"""The frozen-artifact capture ties every deployed container to one commit
by CONTENT, because nothing in the deploy path records a SHA: Cloud Build is
fed a tarball of the working tree, and the engine image lives in a tenant
project that refuses pulls. So the capture reads the files out of each image
and compares them, blob by blob, with `git ls-tree` at the frozen commit.

These tests cover the pure half of that: which paths a Dockerfile carries,
what counts as drift, and which repository files the engine's graph actually
imports (so a commit that touched only the console cannot be mistaken for a
change to the deployed graph, and vice versa).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from spikes.freeze import provenance as p


def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def test_copy_lines_map_image_paths_back_to_repo_paths():
    text = """
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /srv
COPY console/pyproject.toml /srv/console/pyproject.toml
COPY app /srv/app
COPY policy /srv/policy
"""
    assert p.copied_paths(text) == [
        ("console/pyproject.toml", "srv/console/pyproject.toml"),
        ("app", "srv/app"),
        ("policy", "srv/policy"),
    ]


def test_compare_reports_match_mismatch_absent_and_extra():
    tree = {
        "app/a.py": blob(b"same"),
        "app/b.py": blob(b"old"),
        "app/new.py": blob(b"added after the build"),
        "constraints.txt": blob(b"pins"),
        "tests/x.py": blob(b"never copied"),
    }
    image = {
        "srv/app/a.py": b"same",
        "srv/app/b.py": b"new",
        "srv/app/stray.py": b"not in the tree",
        "usr/lib/python3.13/site-packages/x.py": b"ignored",
    }
    copies = [("app", "srv/app"), ("constraints.txt", "srv/constraints.txt")]
    result = p.compare(tree, image, copies)
    assert result["matched"] == ["app/a.py"]
    assert result["mismatched"] == ["app/b.py"]
    assert result["absent_from_image"] == ["app/new.py", "constraints.txt"]
    assert result["extra_in_image"] == ["app/stray.py"]
    assert result["ok"] is False


def test_compare_is_ok_only_when_nothing_drifted():
    tree = {"app/a.py": blob(b"x"), "policy/p.json": blob(b"{}")}
    image = {"srv/app/a.py": b"x", "srv/policy/p.json": b"{}"}
    copies = [("app", "srv/app"), ("policy", "srv/policy")]
    assert p.compare(tree, image, copies)["ok"] is True


def test_compare_ignores_bytecode_the_tree_never_holds():
    tree = {"app/a.py": blob(b"x")}
    image = {"srv/app/a.py": b"x", "srv/app/__pycache__/a.cpython-313.pyc": b"\0"}
    assert p.compare(tree, image, [("app", "srv/app")])["ok"] is True


def test_import_closure_follows_only_first_party_imports(tmp_path: Path):
    pkg = tmp_path / "app"
    (pkg / "state").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "agent.py").write_text(
        "import json\nfrom app.nodes import x\nimport app.state.firestore as fs\n"
    )
    (pkg / "nodes.py").write_text("from .risk import assess\nfrom app import schemas\n")
    (pkg / "risk.py").write_text("from app.policy import load\n")
    (pkg / "policy.py").write_text("")
    (pkg / "schemas.py").write_text("")
    (pkg / "state" / "__init__.py").write_text("")
    (pkg / "state" / "firestore.py").write_text("from app.state import approvals\n")
    (pkg / "state" / "approvals.py").write_text("")
    (pkg / "unused.py").write_text("from app.risk import assess\n")
    closure = p.import_closure(tmp_path, "app.agent")
    assert closure == {
        "app/__init__.py",
        "app/agent.py",
        "app/nodes.py",
        "app/risk.py",
        "app/policy.py",
        "app/schemas.py",
        "app/state/__init__.py",
        "app/state/firestore.py",
        "app/state/approvals.py",
    }


def test_layer_overlay_last_layer_wins_and_whiteouts_delete():
    layers = [
        {"srv/app/a.py": b"v1", "srv/app/gone.py": b"x"},
        {"srv/app/a.py": b"v2", "srv/app/.wh.gone.py": b""},
    ]
    assert p.overlay(layers) == {"srv/app/a.py": b"v2"}


@pytest.mark.parametrize(
    "summary, expected",
    [
        ("=== 412 passed, 31 deselected in 14.02s ===", {"passed": 412, "failed": 0, "deselected": 31, "errors": 0}),
        ("== 3 failed, 400 passed, 2 errors in 9s ==", {"passed": 400, "failed": 3, "deselected": 0, "errors": 2}),
        ("627 passed, 15 deselected, 6 warnings in 20.43s", {"passed": 627, "failed": 0, "deselected": 15, "errors": 0}),
    ],
)
def test_pytest_summary_is_parsed(summary, expected):
    assert p.parse_pytest_summary("noise\n" + summary + "\n") == expected
