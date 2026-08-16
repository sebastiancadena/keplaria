"""Firestore is authoritative for case phase, version, and processed event IDs.

Every inbound event passes through `claim_event` before any agent runs. The
transaction is the only thing standing between at-least-once delivery and
duplicate side effects, so it does the dedupe check and the version bump
atomically or not at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from google.cloud import firestore

CASES = "cases"
INBOX = "inbox"
OUTBOX = "outbox"


@lru_cache(maxsize=None)
def get_client(database: str | None = None) -> firestore.Client:
    """Firestore client for the configured project and database.

    Cached with `lru_cache`, keyed on `database`: building a client runs ADC
    discovery (a metadata-server round trip on Cloud Run) and opens a gRPC
    channel that is never explicitly closed, and every route on the public
    console — as well as every route on the review service — called this
    fresh on every single request before this cache existed. That is a
    per-request network round trip and a leaked channel for a service whose
    entire job is serving public pages. Safe to cache: the env vars this
    resolves (FIRESTORE_PROJECT_ID/GOOGLE_CLOUD_PROJECT/FIRESTORE_DATABASE)
    are fixed for the life of a deployed process, never changed mid-process
    the way a test might monkeypatch them — and any test that needs a
    different client swaps the imported `get_client` name itself (see
    tests/unit/test_nodes_routing.py, test_ingress.py), not this function's
    return value, so the cache is invisible to them.

    FIRESTORE_PROJECT_ID, not GOOGLE_CLOUD_PROJECT: on Agent Runtime,
    GOOGLE_CLOUD_PROJECT is a reserved env var the platform overwrites with
    the numeric project number, not the project ID string (agents-cli deploy
    silently drops whatever value ships in .env, warning "Ignoring reserved
    Agent Runtime env var"). Firestore's resource path requires the string
    ID — a client built with the numeric project number 404s with "The
    database (default) does not exist for project <number>" even though the
    database is real, because commit_commands's claim_command call is the
    only Firestore write made from inside the engine, and it is the call
    that would hit this. FIRESTORE_PROJECT_ID exists specifically to give
    that call the string ID back. Both FIRESTORE_PROJECT_ID and
    GOOGLE_CLOUD_PROJECT are set explicitly (both to "keplaria") on the
    deployed ingress too — nothing here works "by accident" on either
    runtime. Same pattern as AGENT_ENGINE_LOCATION vs GOOGLE_CLOUD_LOCATION
    in ingress/engine_client.py — two consumers needing different values for
    what looks like one setting.
    """
    project = os.environ.get("FIRESTORE_PROJECT_ID") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT", "keplaria"
    )
    return firestore.Client(
        project=project,
        database=database or os.environ.get("FIRESTORE_DATABASE", "(default)"),
    )


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of an inbox claim.

    `reason` is empty on a fresh claim, `"redispatch"` when `claimed` is True
    but this event was already claimed and never successfully dispatched, and
    `"duplicate_event"` / `"stale_event"` when `claimed` is False.
    """

    claimed: bool
    case_version: int
    reason: str = ""


def claim_event(
    db: firestore.Client, case_id: str, event_id: str, event: dict
) -> ClaimResult:
    """Claim `event_id` for `case_id`, creating or advancing the case.

    Rejects a duplicate `event_id` that was already dispatched, and an event
    whose declared `expected_case_version` no longer matches the stored one.
    Both rejections leave the case untouched.

    An `event_id` that was claimed but never marked dispatched (the engine
    call after the original claim failed, or is still in flight) is a
    *redispatch*: the claim is honoured again, at the `case_version` recorded
    on the original claim, without bumping `case_version` a second time. This
    is what lets a transient engine failure be retried by Pub/Sub redelivery
    instead of permanently stranding the case.
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
            inbox_data = inbox_snap.to_dict() or {}
            if inbox_data.get("dispatched"):
                return ClaimResult(False, current_version, "duplicate_event")
            # A real write, not just a read-only return: a transaction with no
            # write has nothing for Firestore's optimistic concurrency to
            # conflict on, so two concurrent redeliveries of the same
            # undispatched event could otherwise both be admitted as a
            # redispatch. Bumping redispatch_count and refreshing claimed_at
            # makes this transaction a genuine writer, so only one of two
            # concurrent attempts commits. case_version is deliberately NOT
            # touched here — a redispatch replays at the version already
            # recorded on the original claim, it does not advance it.
            txn.update(
                inbox_ref,
                {
                    "redispatch_count": firestore.Increment(1),
                    "claimed_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return ClaimResult(
                True, int(inbox_data.get("case_version", current_version)), "redispatch"
            )

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
                "dispatched": False,
                "claimed_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return ClaimResult(True, version)

    return _claim(db.transaction())


def mark_dispatched(db: firestore.Client, case_id: str, event_id: str) -> None:
    """Record that `event_id` was successfully handed off to the engine.

    Called only after the engine invocation for a claimed event succeeds.
    Until this is called, a redelivery of the same event is a *redispatch*,
    not a *duplicate* — see `claim_event`.
    """
    inbox_ref = db.collection(CASES).document(case_id).collection(INBOX).document(event_id)
    inbox_ref.update({"dispatched": True})
