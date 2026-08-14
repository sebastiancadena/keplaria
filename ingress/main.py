"""Pub/Sub push adapter.

Cloud Run IAM verifies the subscription's OIDC token before this code runs; the
handler then validates the envelope and the canonical event schema itself. The
Firestore transaction is the authoritative dedupe point, so the engine is
invoked only after a successful claim, and the claim is only marked dispatched
once the engine call actually succeeds — so a transient engine failure is
retried by Pub/Sub redelivery instead of silently dropping the case.

The engine's graph only ever queues the ERP command (see app.nodes.queue_supplier
for why: no public internet egress from the PSC-attached engine). This ingress
adapter, an ordinary Cloud Run service with normal egress, is what actually
drains the outbox via app.executor.runner.execute_pending_commands — once
right after a successful engine invocation, and again, best-effort, on a
duplicate-event redelivery.

Every response is 200 unless the request itself is malformed, the engine call
failed, or draining the outbox after a fresh claim failed: Pub/Sub retries a
non-2xx response with backoff until the message's retention window (not
forever) elapses, after which it is dropped — there is no dead-letter topic
configured, so a message that keeps failing past retention is simply lost,
not parked anywhere for later inspection. That retry-with-eventual-drop
behaviour is exactly what we want for a malformed envelope (a structurally
broken Pub/Sub push payload — fix the producer) or a transient engine
failure or failed command execution (both worth retrying), and exactly what
we don't want for an unparseable event (a well-formed envelope whose inner
event data fails schema validation — no retry can fix that, so it is acked
immediately instead). A persistently failing engine call will eventually be
dropped once retention expires rather than retried indefinitely; building a
dead-letter path for that case is still open work. A duplicate is always
acked too, deliberately,
*regardless of whether draining the outbox there succeeds* — the alternative
(503 on a failed drain) would make Pub/Sub redeliver the duplicate itself,
recreating the redelivery storm this project already had once. This means
the duplicate-path drain is a bounded, best-effort repair, not a standing
self-healing guarantee: it only ever gets one attempt per duplicate delivery
that happens to arrive, and a persistently failing command (bad credentials,
an ERP outage) stays `failed` until some unrelated later event for the case
triggers another drain, or until a dedicated retry/DLQ path exists (not
built yet). See app/executor/runner.py's module docstring for the same point
in more detail.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException
from pydantic import ValidationError

from app.executor.runner import execute_pending_commands
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
        if claim.reason == "duplicate_event":
            # Opportunistic repair: a duplicate delivery costs nothing to the
            # engine (it is never invoked here), so it is a free chance to
            # retry any outbox command still not DONE. A case whose commands
            # are all DONE drains to a no-op, so this never disturbs the
            # no-duplicate-effect guarantee this branch exists to preserve
            # (idempotent execution over at-least-once delivery — not
            # transactional exactly-once; see app/executor/runner.py).
            #
            # This is a bounded, one-shot attempt, not a standing guarantee:
            # errors are logged, not raised, and this exact message is always
            # acked below regardless of the outcome, so Pub/Sub will never
            # redeliver *this* message again to retry the drain. A command
            # that keeps failing here stays `failed` until some other,
            # unrelated event for the same case happens to arrive and
            # triggers another drain — there is no dedicated retry/DLQ path
            # for it yet. See app/executor/runner.py's module docstring.
            try:
                execute_pending_commands(db, event.case_id)
            except Exception as exc:
                logger.warning(
                    "command execution failed while draining duplicate event "
                    "%s case %s: %s: %s",
                    event.event_id,
                    event.case_id,
                    type(exc).__name__,
                    str(exc)[:200],
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
        #
        # The Agent Runtime engine allows only 1 concurrent query and 30/min;
        # a 429 here is expected load shedding, not a broken engine. It gets
        # its own greppable log line so an operator can tell "rate limited,
        # the subscription's backoff will handle it" apart from "the engine
        # is actually down" at a glance — both still return 503 to Pub/Sub,
        # since the subscription's retry backoff (not a loop in this handler)
        # is what should absorb it.
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            logger.warning(
                "engine rate limited for event %s case %s (429) — deferring to "
                "subscription backoff",
                event.event_id,
                event.case_id,
            )
        else:
            logger.warning(
                "engine invocation failed for event %s case %s: %s: %s",
                event.event_id,
                event.case_id,
                type(exc).__name__,
                str(exc)[:200],
            )
        raise HTTPException(status_code=503, detail="engine invocation failed") from exc

    mark_dispatched(db, event.case_id, event.event_id)

    try:
        command_results = execute_pending_commands(db, event.case_id)
    except Exception as exc:
        # The engine already ran and was already marked dispatched — it must
        # never be re-invoked over this. A non-2xx here only makes Pub/Sub
        # redeliver, which is a duplicate_event by now (dispatched=True), so
        # the retry re-enters the opportunistic-repair branch above instead
        # of spending engine quota again.
        logger.warning(
            "command execution failed for event %s case %s: %s: %s",
            event.event_id,
            event.case_id,
            type(exc).__name__,
            str(exc)[:200],
        )
        raise HTTPException(status_code=503, detail="command execution failed") from exc

    refused_bands = [r.get("band") for r in command_results if r.get("status") == "refused_by_policy"]
    if refused_bands:
        # The runner's guard is a silent no-op by design (see
        # app/executor/runner.py): it never raises and never surfaces as
        # `failed`, so without this line a wiring bug that refused every
        # write forever would look identical to "no events arrived". This is
        # observability only — the 200 ack below is unchanged, because a
        # policy refusal is deterministic and retrying it is pointless.
        logger.warning(
            "command execution refused by policy for event %s case %s: bands=%s",
            event.event_id,
            event.case_id,
            refused_bands,
        )

    if any(r.get("status") == "failed" for r in command_results):
        logger.warning(
            "command execution reported a failure for event %s case %s: %s",
            event.event_id,
            event.case_id,
            command_results,
        )
        raise HTTPException(status_code=503, detail="command execution failed")

    return {
        "status": "claimed",
        "case_version": claim.case_version,
        "session_id": result.get("session_id"),
    }
