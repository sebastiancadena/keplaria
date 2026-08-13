"""Pub/Sub push adapter.

Cloud Run IAM verifies the subscription's OIDC token before this code runs; the
handler then validates the envelope and the canonical event schema itself. The
Firestore transaction is the authoritative dedupe point, so the engine is
invoked only after a successful claim, and the claim is only marked dispatched
once the engine call actually succeeds — so a transient engine failure is
retried by Pub/Sub redelivery instead of silently dropping the case.

Every response is 200 unless the request itself is malformed, or the engine
call failed: Pub/Sub retries non-2xx forever, which is exactly what we want
for a malformed envelope (fix the producer) or a transient engine failure
(retry), and exactly what we don't want for a duplicate or an unparseable
event (no retry can fix either, so those are acked).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from pydantic import ValidationError

from app.schemas import CanonicalEvent
from app.state.firestore import claim_event, get_client, mark_dispatched
from ingress.engine_client import invoke_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("keplaria.ingress")

api = FastAPI(title="keplaria-ingress")


@api.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@api.post("/pubsub/push")
def push(envelope: Any = Body(...)) -> dict:
    # Synchronous on purpose: get_client/claim_event/invoke_engine are all
    # blocking I/O. FastAPI runs a sync path operation in a threadpool, so a
    # slow engine call no longer stalls the single-worker event loop for
    # every other concurrent push.
    if not isinstance(envelope, dict):
        raise HTTPException(status_code=400, detail="not a Pub/Sub push envelope")
    message = envelope.get("message")
    if not isinstance(message, dict) or "data" not in message:
        raise HTTPException(status_code=400, detail="not a Pub/Sub push envelope")

    try:
        payload = json.loads(base64.b64decode(message["data"]))
        event = CanonicalEvent(**payload)
    except (ValidationError, ValueError, TypeError, binascii.Error) as exc:
        # Unparseable events are logged and acked; redelivery cannot fix them.
        # Never log the raw exception: Pydantic embeds the failing field's
        # actual value in ValidationError, and this project's data-handling
        # contract permits case IDs and masked values in logs only.
        if isinstance(exc, ValidationError):
            logger.warning(
                "rejecting malformed event: %s",
                exc.errors(include_url=False, include_input=False),
            )
        else:
            logger.warning(
                "rejecting malformed event: %s: %s", type(exc).__name__, str(exc)[:200]
            )
        return {"status": "invalid", "detail": str(exc)[:200]}

    db = get_client()
    claim = claim_event(db, event.case_id, event.event_id, event.model_dump())

    if not claim.claimed:
        logger.info(
            "event %s for case %s not claimed: %s",
            event.event_id,
            event.case_id,
            claim.reason,
        )
        return {
            "status": "duplicate" if claim.reason == "duplicate_event" else claim.reason,
            "case_version": claim.case_version,
        }

    logger.info(
        "claimed event %s for case %s at version %s (%s)",
        event.event_id,
        event.case_id,
        claim.case_version,
        claim.reason or "initial",
    )
    try:
        result = invoke_engine(event.model_dump())
    except Exception as exc:
        # The claim already landed; do NOT mark it dispatched. A non-2xx here
        # makes Pub/Sub redeliver, which claim_event now treats as a
        # redispatch rather than a duplicate, so the case is retried instead
        # of permanently stranded.
        logger.warning(
            "engine invocation failed for event %s case %s: %s: %s",
            event.event_id,
            event.case_id,
            type(exc).__name__,
            str(exc)[:200],
        )
        raise HTTPException(status_code=503, detail="engine invocation failed") from exc

    mark_dispatched(db, event.case_id, event.event_id)
    return {
        "status": "claimed",
        "case_version": claim.case_version,
        "session_id": result.get("session_id"),
    }
