"""Pure helpers for the frozen-artifact capture: what an image should carry,
whether it does, and which files the deployed graph actually imports.

Nothing here talks to the network or to git; `capture.py` feeds these
functions what it discovered and records what they say.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

_COPY = re.compile(r"^\s*COPY\s+(?!--from)(\S+)\s+(\S+)\s*$", re.MULTILINE)
_SUMMARY = re.compile(r"^=+ (.+?) in [\d.]+s.*=+$", re.MULTILINE)


def copied_paths(dockerfile: str) -> list[tuple[str, str]]:
    """`(repo_path, image_path)` for every `COPY` that takes from the build
    context. `COPY --from=` lines bring in other images and are skipped."""
    return [
        (src, dst.lstrip("/"))
        for src, dst in _COPY.findall(dockerfile)
    ]


def blob_sha(data: bytes) -> str:
    """The hash `git ls-tree` prints for a file with these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _is_bytecode(path: str) -> bool:
    return "__pycache__/" in path or path.endswith((".pyc", ".pyo"))


def compare(tree: dict[str, str], image: dict[str, bytes],
            copies: list[tuple[str, str]]) -> dict:
    """Compare the files an image carries with the frozen tree.

    `tree` maps repo paths to git blob hashes (from `git ls-tree -r`);
    `image` maps image paths (no leading slash) to bytes. Only the paths a
    `COPY` line carries are compared. Every disagreement is drift: a file
    whose bytes differ, a tree file the image lacks (added after the build),
    or an image file the tree lacks (removed after the build). Bytecode is
    ignored because the tree never holds it.
    """
    matched, mismatched, absent, extra = [], [], [], []
    for src, dst in copies:
        src_is_file = src in tree
        expected = {src: tree[src]} if src_is_file else {
            path: sha for path, sha in tree.items() if path.startswith(src + "/")
        }
        found: dict[str, bytes] = {}
        for ipath, data in image.items():
            if src_is_file and ipath == dst:
                found[src] = data
            elif not src_is_file and ipath.startswith(dst + "/") and not _is_bytecode(ipath):
                found[src + "/" + ipath[len(dst) + 1:]] = data
        for path, sha in expected.items():
            if path not in found:
                absent.append(path)
            elif blob_sha(found[path]) == sha:
                matched.append(path)
            else:
                mismatched.append(path)
        extra.extend(path for path in found if path not in expected)
    result = {
        "matched": sorted(matched),
        "mismatched": sorted(mismatched),
        "absent_from_image": sorted(absent),
        "extra_in_image": sorted(extra),
    }
    result["ok"] = not (mismatched or absent or extra)
    return result


def overlay(layers: list[dict[str, bytes]]) -> dict[str, bytes]:
    """Apply image layers in order: later files win, `.wh.` entries delete."""
    files: dict[str, bytes] = {}
    for layer in layers:
        for path, data in layer.items():
            head, _, name = path.rpartition("/")
            if name.startswith(".wh."):
                target = f"{head}/{name[4:]}" if head else name[4:]
                files.pop(target, None)
                for key in [k for k in files if k.startswith(target + "/")]:
                    del files[key]
            else:
                files[path] = data
    return files


def _module_path(root: Path, dotted: str) -> str | None:
    rel = dotted.replace(".", "/")
    for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
        if (root / candidate).is_file():
            return candidate
    return None


def import_closure(root: Path, entry: str) -> set[str]:
    """Repo-relative paths of every first-party module reachable from
    `entry` (e.g. `app.agent`) by static import, package `__init__`s
    included. Third-party imports are not followed; a module in the package
    that nothing imports is not in the closure."""
    top = entry.split(".")[0]
    seen: set[str] = set()
    todo = [entry]
    while todo:
        dotted = todo.pop()
        path = _module_path(root, dotted)
        if path is None or path in seen:
            continue
        seen.add(path)
        parts = dotted.split(".")
        for i in range(1, len(parts)):
            todo.append(".".join(parts[:i]))
        tree = ast.parse((root / path).read_text())
        package = parts[:-1] if path.endswith(".py") and not path.endswith("__init__.py") else parts
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == top:
                        todo.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[: len(package) - node.level + 1]
                    module = ".".join(base + ([node.module] if node.module else []))
                else:
                    module = node.module or ""
                if module.split(".")[0] != top:
                    continue
                todo.append(module)
                for alias in node.names:
                    todo.append(f"{module}.{alias.name}")
    return seen


def parse_pytest_summary(output: str) -> dict[str, int]:
    """Counts from pytest's final `=== N passed, M deselected in Ts ===` line."""
    counts = {"passed": 0, "failed": 0, "deselected": 0, "errors": 0}
    match = _SUMMARY.findall(output)
    if not match:
        raise ValueError("no pytest summary line found")
    for part in match[-1].split(","):
        number, _, word = part.strip().partition(" ")
        key = {"error": "errors"}.get(word, word)
        if key in counts:
            counts[key] = int(number)
    return counts
