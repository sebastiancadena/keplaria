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


def test_a_command_with_no_timestamps_does_not_break_the_page(db):
    """Outbox documents are schemaless and both timestamps can be absent.

    Sorting on `updated_at or created_at or 0` then compares a
    `DatetimeWithNanoseconds` against an `int`, which raises `TypeError` —
    a 500 on /review/failures at exactly the moment someone is trying to
    look at a broken command. The row is written directly rather than
    through claim_command, because claim_command always writes both
    timestamps and could never produce the document this guards against.
    """
    import uuid

    from app.state.commands import FAILED, claim_command, record_failure
    from app.state.firestore import CASES, OUTBOX
    from console.store import list_failed_commands

    timeless = f"NOTS-{uuid.uuid4().hex[:12]}"
    normal = f"TS-{uuid.uuid4().hex[:12]}"

    db.collection(CASES).document(timeless).collection(OUTBOX).document(
        f"{timeless}:create_supplier:c1"
    ).set(
        {
            "command_id": f"{timeless}:create_supplier:c1",
            "case_id": timeless,
            "action": "create_supplier",
            "cycle": 1,
            "status": FAILED,
            "error": "HTTP 503",
        }
    )
    claim_command(db, normal, "create_supplier", 1, {"supplier_name": "A"})
    record_failure(db, normal, "create_supplier", 1, "HTTP 503")

    listed = list_failed_commands(db, limit=1000)
    by_case = {c.get("case_id"): c for c in listed}

    assert timeless in by_case, "an undated row must still be listed, not dropped"
    assert normal in by_case


class _FakeSnapshot:
    def __init__(self, data: dict):
        self._data = data

    def to_dict(self) -> dict:
        return self._data


class _FakeQuery:
    """Just enough Firestore query surface for list_failed_commands.

    A fake rather than the emulator on purpose: the `db` fixture is a shared
    database that every other test writes failed and dead commands into, so a
    test about which rows survive a small `limit` could never be deterministic
    against it — the limit would be filled by other tests' rows before this
    test's own were reached.
    """

    def __init__(self, rows_by_status: dict[str, list[dict]]):
        self._rows_by_status = rows_by_status
        self._status: str | None = None
        self._limit: int | None = None

    def where(self, filter=None):
        self._status = filter.value
        return self

    def limit(self, count: int):
        self._limit = count
        return self

    def stream(self):
        rows = self._rows_by_status.get(self._status, [])
        return [_FakeSnapshot(row) for row in rows[: self._limit]]


class _FakeDb:
    def __init__(self, rows_by_status: dict[str, list[dict]]):
        self._rows_by_status = rows_by_status

    def collection_group(self, _name: str) -> _FakeQuery:
        return _FakeQuery(self._rows_by_status)


def test_dead_commands_cannot_be_hidden_behind_failed_ones():
    """A page full of `failed` rows must not push `dead` rows off it.

    Both queries run with the same `limit`, so merging them and truncating
    the result meant 50 failed commands could evict every dead one — and the
    dead command is the one that will never be retried and therefore the only
    one that actually needs a human. The dead rows here are also the OLDEST,
    which is exactly the case a newest-first truncation would drop.
    """
    from datetime import UTC, datetime, timedelta

    from console.store import list_failed_commands

    base = datetime(2026, 8, 1, tzinfo=UTC)

    rows = {
        "failed": [
            {
                "case_id": f"FAILED-{i}",
                "status": "failed",
                "updated_at": base + timedelta(hours=1 + i),
            }
            for i in range(50)
        ],
        "dead": [{"case_id": "DEAD-1", "status": "dead", "updated_at": base}],
    }

    listed = list_failed_commands(_FakeDb(rows), limit=50)

    assert len(listed) == 50, "the limit is still respected"
    assert "DEAD-1" in [r["case_id"] for r in listed], (
        "a dead command must not be evicted by newer failed ones"
    )
