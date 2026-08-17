"""Direct tests of console/store.py's guard against an unaddressable case_id.

Empirical finding, established with a `TestClient` against both `console.public`
and `console.review` before writing this file: a `%2F`-encoded case_id never
reaches either route handler. Starlette's router itself decodes and re-splits
the path before dispatch, so `GET /cases/FOO%2FBAR` and
`POST /review/FOO%2FBAR/decide` both 404 at the router with Starlette's own
`{"detail": "Not Found"}` body — `console.public.case_detail`,
`console.review.decide`, and therefore `case_id_is_addressable` itself are
never invoked. An HTTP-level test of this guard would 404 for a reason that
has nothing to do with `case_id_is_addressable`, so it would pass whether the
guard exists or not — exactly the kind of test that passes for the wrong
reason this branch has already had to correct seven times. The guard is
pinned directly instead.
"""

from __future__ import annotations

from console.store import case_id_is_addressable, load_case


def test_a_case_id_containing_a_slash_is_not_addressable():
    assert case_id_is_addressable("FOO/BAR") is False
    assert case_id_is_addressable("TEST-abc123") is True


def test_loading_an_unaddressable_case_id_is_refused_not_a_crash(db):
    """Without the guard, `.document("FOO/BAR")` builds a reference with an
    odd number of path segments, which raises a bare `ValueError` on
    construction — an unhandled 500, not a refusal. `load_case` must return
    the same `(None, [])` it returns for any other unknown case, never let
    that exception through.
    """
    case, commands = load_case(db, "FOO/BAR")
    assert case is None
    assert commands == []


def test_failed_and_dead_commands_are_both_listed(db):
    """Both states are operational work: `failed` will be retried, `dead` will
    not, and a reviewer needs to see each for different reasons."""
    import uuid

    from app.state.commands import (
        DEAD,
        FAILED,
        MAX_EXECUTION_ATTEMPTS,
        claim_command,
        record_failure,
    )
    from console.store import list_failed_commands

    failing = f"FAIL-{uuid.uuid4().hex[:12]}"
    dying = f"DEAD-{uuid.uuid4().hex[:12]}"

    claim_command(db, failing, "create_supplier", 1, {"supplier_name": "A"})
    record_failure(db, failing, "create_supplier", 1, "HTTP 503")

    claim_command(db, dying, "create_supplier", 1, {"supplier_name": "B"})
    for _ in range(MAX_EXECUTION_ATTEMPTS):
        record_failure(db, dying, "create_supplier", 1, "HTTP 500")

    listed = list_failed_commands(db, limit=1000)
    by_case = {c["case_id"]: c for c in listed}

    assert by_case[failing]["status"] == FAILED
    assert by_case[dying]["status"] == DEAD
    assert by_case[dying]["execution_attempts"] == MAX_EXECUTION_ATTEMPTS
