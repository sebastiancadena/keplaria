"""The inbox transaction is the system's dedupe and versioning boundary."""

from app.state.firestore import claim_event, mark_dispatched


def _event(event_id: str, case_id: str, **extra) -> dict:
    event = {
        "event_id": event_id,
        "case_id": case_id,
        "event_type": "new_supplier_packet",
        "schema_version": 1,
        "supplier": "Comercializadora Andes Verde SAS",
    }
    event.update(extra)
    return event


def test_first_event_creates_case_at_version_1(db, case_id):
    result = claim_event(db, case_id, "evt-1", _event("evt-1", case_id))

    assert result.claimed is True
    assert result.case_version == 1

    case = db.collection("cases").document(case_id).get().to_dict()
    assert case["case_id"] == case_id
    assert case["case_version"] == 1
    assert case["phase"] == "processing"


def test_duplicate_event_is_rejected_and_does_not_bump_version(db, case_id):
    claim_event(db, case_id, "evt-1", _event("evt-1", case_id))
    mark_dispatched(db, case_id, "evt-1")

    result = claim_event(db, case_id, "evt-1", _event("evt-1", case_id))

    assert result.claimed is False
    assert result.reason == "duplicate_event"
    case = db.collection("cases").document(case_id).get().to_dict()
    assert case["case_version"] == 1, "a duplicate must never advance the case"


def test_undispatched_redelivery_is_redispatched_without_advancing_version(db, case_id):
    """A claim whose engine call never succeeded must be retried, not dropped.

    Without a `mark_dispatched` in between, a redelivery of the same event is
    not yet a duplicate — the previous claim never made it to the engine.
    """
    claim_event(db, case_id, "evt-1", _event("evt-1", case_id))

    result = claim_event(db, case_id, "evt-1", _event("evt-1", case_id))

    assert result.claimed is True
    assert result.reason == "redispatch"
    assert result.case_version == 1
    case = db.collection("cases").document(case_id).get().to_dict()
    assert case["case_version"] == 1, "a redispatch must never advance the case again"


def test_second_distinct_event_advances_the_case(db, case_id):
    claim_event(db, case_id, "evt-1", _event("evt-1", case_id))

    result = claim_event(db, case_id, "evt-2", _event("evt-2", case_id))

    assert result.claimed is True
    assert result.case_version == 2


def test_stale_event_is_rejected(db, case_id):
    claim_event(db, case_id, "evt-1", _event("evt-1", case_id))
    claim_event(db, case_id, "evt-2", _event("evt-2", case_id))

    stale = _event("evt-3", case_id, expected_case_version=1)
    result = claim_event(db, case_id, "evt-3", stale)

    assert result.claimed is False
    assert result.reason == "stale_event"
    assert db.collection("cases").document(case_id).get().to_dict()["case_version"] == 2


def test_matching_expected_version_is_accepted(db, case_id):
    claim_event(db, case_id, "evt-1", _event("evt-1", case_id))

    fresh = _event("evt-2", case_id, expected_case_version=1)
    result = claim_event(db, case_id, "evt-2", fresh)

    assert result.claimed is True
    assert result.case_version == 2


def test_inbox_record_is_written_for_a_claimed_event(db, case_id):
    claim_event(db, case_id, "evt-1", _event("evt-1", case_id))

    inbox = (
        db.collection("cases").document(case_id).collection("inbox").document("evt-1").get()
    )
    assert inbox.exists
    assert inbox.to_dict()["event_type"] == "new_supplier_packet"
