"""Pub/Sub push adapter.

Cloud Run IAM verifies the subscription's OIDC token before this code runs; the
handler then validates the envelope and the canonical event schema itself. The
Firestore transaction is the authoritative dedupe point, so the engine is
invoked only after a successful claim.

Every response is 200 unless the request itself is malformed: Pub/Sub retries
non-2xx forever, and a duplicate or an unparseable event is not something a
retry can fix.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from app.schemas import CanonicalEvent
from app.state.firestore import claim_event, get_client
from ingress.engine_client import invoke_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("keplaria.ingress")

api = FastAPI(title="keplaria-ingress")


@api.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@api.post("/pubsub/push")
async def push(request: Request) -> dict:
    envelope = await request.json()
    message = (envelope or {}).get("message")
    if not isinstance(message, dict) or "data" not in message:
        raise HTTPException(status_code=400, detail="not a Pub/Sub push envelope")

    try:
        payload = json.loads(base64.b64decode(message["data"]))
        event = CanonicalEvent(**payload)
    except (ValidationError, ValueError, binascii.Error) as exc:
        # Unparseable events are logged and acked; redelivery cannot fix them.
        logger.warning("rejecting malformed event: %s", exc)
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
        "claimed event %s for case %s at version %s",
        event.event_id,
        event.case_id,
        claim.case_version,
    )
    result = invoke_engine(event.model_dump())
    return {
        "status": "claimed",
        "case_version": claim.case_version,
        "session_id": result.get("session_id"),
    }
