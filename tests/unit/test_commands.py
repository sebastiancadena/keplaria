"""The command ledger makes every side effect retry-safe."""

from app.state.commands import (
    DONE,
    PENDING,
    claim_command,
    command_id,
    get_command,
    record_failure,
    record_success,
)

PAYLOAD = {"supplier_name": "Comercializadora Andes Verde SAS", "country": "Colombia"}


def test_command_id_is_deterministic():
    assert command_id("CASE-1", "create_supplier", 1) == "CASE-1:create_supplier:c1"
    assert command_id("CASE-1", "create_supplier", 1) == command_id(
        "CASE-1", "create_supplier", 1
    )


def test_a_later_cycle_does_not_collide_with_an_earlier_one(db, case_id):
    claim_command(db, case_id, "request_renewal", 1, PAYLOAD)
    record_success(db, case_id, "request_renewal", 1, "COMM-0001", {"ok": True})

    claim = claim_command(db, case_id, "request_renewal", 2, PAYLOAD)

    assert claim.acquired is True, (
        "cycle 2 must be a distinct command; sharing cycle 1's ID would make "
        "the second renewal a silent no-op"
    )
    assert claim.status == PENDING


def test_the_command_document_records_its_cycle(db, case_id):
    claim_command(db, case_id, "request_renewal", 3, PAYLOAD)

    assert get_command(db, case_id, "request_renewal", 3)["cycle"] == 3


def test_first_claim_is_acquired(db, case_id):
    claim = claim_command(db, case_id, "create_supplier", 1, PAYLOAD)

    assert claim.acquired is True
    assert claim.status == PENDING
    assert claim.external_id is None


def test_claim_after_success_is_refused_and_returns_the_external_id(db, case_id):
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    record_success(db, case_id, "create_supplier", 1, "Andes Verde SAS", {"ok": True})

    claim = claim_command(db, case_id, "create_supplier", 1, PAYLOAD)

    assert claim.acquired is False, "a completed command must never run twice"
    assert claim.status == DONE
    assert claim.external_id == "Andes Verde SAS"
    assert claim.result == {"ok": True}


def test_claim_after_failure_is_reacquired_and_counts_attempts(db, case_id):
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    claim = claim_command(db, case_id, "create_supplier", 1, PAYLOAD)

    assert claim.acquired is True
    assert get_command(db, case_id, "create_supplier", 1)["attempts"] == 2


def test_claim_interrupted_before_record_is_reacquired(db, case_id):
    """A process that dies between the ERP call and record_success leaves the
    command pending; the retry must be allowed to re-drive it."""
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)

    claim = claim_command(db, case_id, "create_supplier", 1, PAYLOAD)

    assert claim.acquired is True
    assert claim.status == PENDING


def test_record_success_persists_the_external_id(db, case_id):
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    record_success(db, case_id, "create_supplier", 1, "Andes Verde SAS", {"ok": True})

    stored = get_command(db, case_id, "create_supplier", 1)
    assert stored["status"] == DONE
    assert stored["external_id"] == "Andes Verde SAS"
    assert stored["payload"]["country"] == "Colombia"


def test_get_command_returns_none_when_absent(db, case_id):
    assert get_command(db, case_id, "create_supplier", 1) is None


def test_execution_attempts_are_counted_separately_from_claims(db, case_id):
    """`attempts` counts graph-side claims; `execution_attempts` counts executor
    runs. Conflating them would cap retries on the wrong quantity."""
    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    stored = get_command(db, case_id, "create_supplier", 1)
    assert stored["attempts"] == 1
    assert stored["execution_attempts"] == 1


def test_a_failure_below_the_cap_stays_failed(db, case_id):
    from app.state.commands import FAILED, MAX_EXECUTION_ATTEMPTS

    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    for _ in range(MAX_EXECUTION_ATTEMPTS - 1):
        status = record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    assert status == FAILED
    stored = get_command(db, case_id, "create_supplier", 1)
    assert stored["status"] == FAILED
    assert stored["execution_attempts"] == MAX_EXECUTION_ATTEMPTS - 1
    assert stored.get("died_at") is None


def test_the_capped_failure_is_dead_not_failed(db, case_id):
    from app.state.commands import DEAD, MAX_EXECUTION_ATTEMPTS

    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    for _ in range(MAX_EXECUTION_ATTEMPTS):
        status = record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    assert status == DEAD
    stored = get_command(db, case_id, "create_supplier", 1)
    assert stored["status"] == DEAD
    assert stored["died_at"] is not None
    assert stored["error"] == "HTTP 503", "the last error must survive the cap"


def test_a_dead_command_is_never_reclaimed(db, case_id):
    """The graph re-claims on every event, and a review-band case re-parks on
    every later event. Without this refusal a dead command would be reset to
    PENDING and resurrected forever, making the cap meaningless."""
    from app.state.commands import DEAD, MAX_EXECUTION_ATTEMPTS

    claim_command(db, case_id, "create_supplier", 1, PAYLOAD)
    for _ in range(MAX_EXECUTION_ATTEMPTS):
        record_failure(db, case_id, "create_supplier", 1, "HTTP 503")

    claim = claim_command(db, case_id, "create_supplier", 1, PAYLOAD)

    assert claim.acquired is False
    assert claim.status == DEAD
    assert get_command(db, case_id, "create_supplier", 1)["status"] == DEAD


def test_a_new_cycle_is_unaffected_by_a_dead_earlier_cycle(db, case_id):
    """`command_id` is cycle-scoped, so a dead command never blocks the next
    lifecycle turn. This is why resurrection is unnecessary."""
    from app.state.commands import MAX_EXECUTION_ATTEMPTS

    claim_command(db, case_id, "request_renewal", 1, PAYLOAD)
    for _ in range(MAX_EXECUTION_ATTEMPTS):
        record_failure(db, case_id, "request_renewal", 1, "HTTP 503")

    claim = claim_command(db, case_id, "request_renewal", 2, PAYLOAD)

    assert claim.acquired is True
    assert claim.status == PENDING
