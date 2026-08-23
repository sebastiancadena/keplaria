"""Two Frappe identities, and only one of them may reach the executor path.

Until 2026-08-23 there was one credential: the site owner's, a full System
Manager, used by the deployed executor AND by `scripts/erp.py purge`. The
public copy called the ERP identity "scoped" on the strength of a separate
no-role token that nothing in production used, which a cold read caught as an
overclaim.

The split is what makes the claim true, so it is what these tests protect:

- `frappe_client()` — the scoped executor identity. Everything the deployed
  services run goes through it.
- `frappe_admin_client()` — the owner identity. Local human-triggered
  maintenance only; it is never in Secret Manager and never on a deployed
  service.

The failure this guards against is silent in both directions. An executor
that quietly picked up the admin variables would pass every functional test
while re-granting itself delete on the live ERP; a maintenance script pointed
at the scoped key would fail only at the moment someone needed to purge.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from app.executor import frappe

ROOT = Path(__file__).resolve().parents[2]

SCOPED_VARS = {"FRAPPE_API_KEY", "FRAPPE_API_SECRET"}
ADMIN_VARS = {"FRAPPE_ADMIN_API_KEY", "FRAPPE_ADMIN_API_SECRET"}


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("FRAPPE_SITE", "https://example.invalid/")
    monkeypatch.setenv("FRAPPE_API_KEY", "scoped-key")
    monkeypatch.setenv("FRAPPE_API_SECRET", "scoped-secret")
    monkeypatch.setenv("FRAPPE_ADMIN_API_KEY", "admin-key")
    monkeypatch.setenv("FRAPPE_ADMIN_API_SECRET", "admin-secret")


def test_executor_client_carries_the_scoped_token(creds):
    with frappe.frappe_client() as client:
        assert client.headers["Authorization"] == "token scoped-key:scoped-secret"


def test_admin_client_carries_the_owner_token(creds):
    with frappe.frappe_admin_client() as client:
        assert client.headers["Authorization"] == "token admin-key:admin-secret"


def test_admin_client_refuses_to_fall_back_to_the_scoped_token(creds, monkeypatch):
    """A missing admin credential is an error, never a silent downgrade.

    Falling back would hand the maintenance script the scoped key, whose
    deletes 403 — turning "you have not configured the admin credential" into
    "the ERP refused", which is a different bug to chase.
    """
    monkeypatch.delenv("FRAPPE_ADMIN_API_KEY")
    with pytest.raises(KeyError):
        frappe.frappe_admin_client()


def _env_vars_read_by(path: Path) -> set[str]:
    """Every string passed to os.environ[...] or os.environ.get(...) in a file."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            if node.value.attr == "environ" and isinstance(node.slice, ast.Constant):
                found.add(node.slice.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and isinstance(node.func.value, ast.Attribute):
                if node.func.value.attr == "environ" and node.args:
                    if isinstance(node.args[0], ast.Constant):
                        found.add(node.args[0].value)
    return found


def _calls_named(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_executor_operations_never_reach_for_the_admin_credential():
    """No module on the deployed path may name the admin variables.

    Checked across `app/`, not only `frappe.py`, because the credential is an
    environment read: any module could pick it up, and the deployed services
    have no admin credential to pick up in the first place — so this fails
    here rather than 500ing there.
    """
    offenders = {
        str(path.relative_to(ROOT)): sorted(ADMIN_VARS & _env_vars_read_by(path))
        for path in sorted((ROOT / "app").rglob("*.py"))
        if ADMIN_VARS & _env_vars_read_by(path)
        and path.name != "frappe.py"
    }
    assert offenders == {}


def test_only_the_admin_client_reads_the_admin_credential():
    """Within frappe.py, the admin variables belong to exactly one function."""
    tree = ast.parse((ROOT / "app" / "executor" / "frappe.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name != "frappe_admin_client":
            names = {
                sub.slice.value
                for sub in ast.walk(node)
                if isinstance(sub, ast.Subscript)
                and isinstance(sub.value, ast.Attribute)
                and sub.value.attr == "environ"
                and isinstance(sub.slice, ast.Constant)
            }
            assert not (ADMIN_VARS & names), f"{node.name} reads an admin credential"


def test_maintenance_script_runs_as_admin_and_never_as_the_executor():
    """`scripts/erp.py` deletes records; the scoped identity cannot.

    Asserted over the call names rather than the import list, because
    importing both and using the wrong one is the mistake worth catching.
    """
    calls = _calls_named(ROOT / "scripts" / "erp.py")
    assert "frappe_admin_client" in calls
    assert "frappe_client" not in calls
