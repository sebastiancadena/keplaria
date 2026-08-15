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
