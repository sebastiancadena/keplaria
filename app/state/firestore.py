"""Firestore is authoritative for case phase, version, and processed event IDs.

Every inbound event passes through `claim_event` before any agent runs. The
transaction is the only thing standing between at-least-once delivery and
duplicate side effects, so it does the dedupe check and the version bump
atomically or not at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from google.cloud import firestore

CASES = "cases"
INBOX = "inbox"
OUTBOX = "outbox"


def get_client(database: str | None = None) -> firestore.Client:
    """Firestore client for the configured project and database."""
    return firestore.Client(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "keplaria"),
        database=database or os.environ.get("FIRESTORE_DATABASE", "(default)"),
    )


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of an inbox claim. `reason` is empty when `claimed` is True."""

    claimed: bool
    case_version: int
    reason: str = ""


def claim_event(
    db: firestore.Client, case_id: str, event_id: str, event: dict
) -> ClaimResult:
    """Claim `event_id` for `case_id`, creating or advancing the case.

    Rejects a duplicate `event_id` and an event whose declared
    `expected_case_version` no longer matches the stored one. Both rejections
    leave the case untouched.
    """
    case_ref = db.collection(CASES).document(case_id)
    inbox_ref = case_ref.collection(INBOX).document(event_id)

    @firestore.transactional
    def _claim(txn: firestore.Transaction) -> ClaimResult:
        # Firestore requires every read to precede every write in a transaction.
        inbox_snap = inbox_ref.get(transaction=txn)
        case_snap = case_ref.get(transaction=txn)
        case = case_snap.to_dict() or {}
        current_version = int(case.get("case_version", 0))

        if inbox_snap.exists:
            return ClaimResult(False, current_version, "duplicate_event")

        expected = event.get("expected_case_version")
        if expected is not None and int(expected) != current_version:
            return ClaimResult(False, current_version, "stale_event")

        version = current_version + 1
        if case_snap.exists:
            txn.update(
                case_ref,
                {
                    "case_version": version,
                    "phase": "processing",
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
        else:
            txn.set(
                case_ref,
                {
                    "case_id": case_id,
                    "case_version": version,
                    "phase": "processing",
                    "supplier": event.get("supplier"),
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )

        txn.set(
            inbox_ref,
            {
                "event_id": event_id,
                "event_type": event.get("event_type"),
                "schema_version": event.get("schema_version"),
                "case_version": version,
                "claimed_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return ClaimResult(True, version)

    return _claim(db.transaction())
