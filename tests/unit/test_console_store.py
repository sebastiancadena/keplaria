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
