"""Pub/Sub push adapter.

Cloud Run IAM verifies the subscription's OIDC token before this code runs; the
handler then validates the envelope and the canonical event schema itself. The
Firestore transaction is the authoritative dedupe point, so the engine is
invoked only after a successful claim, and the claim is only marked dispatched
once the engine call actually succeeds — so a transient engine failure is
retried by Pub/Sub redelivery instead of silently dropping the case.

The engine's graph only ever queues the ERP command (see app.nodes.commit_commands
for why: no public internet egress from the PSC-attached engine). This ingress
adapter, an ordinary Cloud Run service with normal egress, is what actually
drains the outbox via app.executor.runner.execute_pending_commands — once
right after a successful engine invocation, and again, best-effort, on a
duplicate-event redelivery.

Every response is 200 unless the request itself is malformed, the engine call
failed, or draining the outbox after a fresh claim failed: Pub/Sub retries a
non-2xx response with backoff, bounded by the keplaria-events-push
subscription's deadLetterPolicy (maxDeliveryAttempts 5) rather than by the
message's 7-day retention window — a message that keeps failing exhausts its
delivery attempts and is dead-lettered long before retention would otherwise
let it quietly expire. That retry-with-eventual-dead-letter behaviour is
exactly what we want for a malformed envelope (a structurally broken Pub/Sub
push payload — fix the producer) or a transient engine failure or failed
command execution (both worth retrying), and exactly what we don't want for
an unparseable event (a well-formed envelope whose inner event data fails
schema validation — no retry can fix that, so it is acked immediately
instead). The dead-letter topic pushes to POST /pubsub/dead below, which
records the event in Firestore so it survives past what retention alone
would have preserved and can be inspected, rather than simply vanishing. A
duplicate is always acked too, deliberately,
*regardless of whether draining the outbox there succeeds* — the alternative
(503 on a failed drain) would make Pub/Sub redeliver the duplicate itself,
recreating the redelivery storm this project already had once. This means
the duplicate-path drain is a bounded, best-effort repair, not a standing
self-healing guarantee: it only ever gets one attempt per duplicate delivery
that happens to arrive, and a persistently failing command (bad credentials,
an ERP outage) stays `failed` until some other, unrelated event for the same
case happens to arrive and triggers another drain, or until the Cloud
Scheduler sweep (POST /admin/sweep) re-drives it. That sweep is what makes
the duplicate-path drain no longer the only second chance a command gets, and
the MAX_EXECUTION_ATTEMPTS cap is what stops it retrying forever. See
app/executor/runner.py's module docstring for the same point in more detail.
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

from app.executor.runner import REFUSED_BY_POLICY, execute_pending_commands
from app.executor.sweep import sweep_failed_commands
from app.schemas import CanonicalEvent
from app.state.commands import DEAD
from app.state.dead_events import record_dead_event
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
            # redeliver *this* message again to retry the drain. That is no
            # longer the only second chance a command gets, though: a command
            # that keeps failing here stays `failed` and is re-driven by the
            # Cloud Scheduler sweep (POST /admin/sweep, every 15 minutes),
            # bounded by MAX_EXECUTION_ATTEMPTS — after which it parks as
            # `dead` and is never retried again, here or anywhere else. See
            # app/executor/runner.py's module docstring.
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

    refused = [r for r in command_results if r.get("status") == REFUSED_BY_POLICY]
    if refused:
        # The runner's guard is a silent no-op by design (see
        # app/executor/runner.py): it never raises and never surfaces as
        # `failed`, so without this line a wiring bug that refused every
        # write forever would look identical to "no events arrived". This is
        # observability only — the 200 ack below is unchanged, because a
        # policy refusal is deterministic and retrying it is pointless.
        #
        # A refusal is no longer necessarily an anomaly. park_case claims the
        # commands it parks, so every review-band case refuses on every drain
        # until a human approves it — that is the system working. The log
        # therefore carries the gate's own band and the applying approval id
        # alongside the effective band, because "gate said review, no
        # approval yet" and "gate said clear but something refused anyway"
        # are the two readings this line has to keep distinguishable.
        logger.warning(
            "command execution refused by policy for event %s case %s: %s",
            event.event_id,
            event.case_id,
            [
                {
                    "action": r.get("action"),
                    "band": r.get("band"),
                    "gate_band": r.get("gate_band"),
                    "approval_id": r.get("approval_id"),
                }
                for r in refused
            ],
        )

    dead = [r for r in command_results if r.get("status") == DEAD]
    if dead:
        # Deliberately NOT a 503. A dead command has exhausted
        # MAX_EXECUTION_ATTEMPTS, and making Pub/Sub redeliver over it would
        # turn the cap into an infinite retry on the one command the system
        # decided to stop retrying. Logged at error level because a dead
        # command is a real operational event needing a human, unlike a
        # policy refusal.
        logger.error(
            "command execution is dead for event %s case %s: %s",
            event.event_id,
            event.case_id,
            [{"action": r.get("action"), "error": r.get("error")} for r in dead],
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


@api.post("/admin/sweep")
def admin_sweep() -> dict:
    """Re-drive outbox commands that failed and were never retried.

    Called by the keplaria-command-sweep Cloud Scheduler job every 15
    minutes. Authorization is Cloud Run IAM — the caller needs an OIDC token
    from a service account holding roles/run.invoker on this service, the
    same mechanism that protects /pubsub/push. There is deliberately no
    application-level check here to go stale beside it.

    Never invokes the engine, so it costs none of the Agent Runtime query
    quota; it only reads Firestore and writes to the ERP.
    """
    db = get_client()
    try:
        summary = sweep_failed_commands(db)
    except Exception as exc:
        # A sweep that could not run at all is worth retrying, so Scheduler
        # should see a non-2xx. This is not the same judgement as an
        # individual dead command, which is acked precisely so it is NOT
        # retried.
        logger.warning(
            "sweep failed: %s: %s", type(exc).__name__, str(exc)[:200]
        )
        raise HTTPException(status_code=503, detail="sweep failed") from exc

    if summary["commands_dead"]:
        logger.error(
            "sweep parked %s command(s) as dead across cases %s",
            summary["commands_dead"],
            summary["case_ids"],
        )
    if summary["commands_refused"]:
        # A refusal during a sweep is a narrower thing than a refusal on the
        # push path: the sweep only visits cases holding a `failed` command,
        # so this means a command that failed while its case was `clear` is
        # now sitting in a case that no longer is. It cannot progress and it
        # cannot die — the drain never calls the ERP, so execution_attempts
        # never increments — and the sweep will find it again every run. That
        # is accepted (see app/executor/sweep.py), which is exactly why it has
        # to be said out loud once per sweep rather than counted as work.
        logger.warning(
            "sweep refused %s command(s) still awaiting a human decision "
            "across cases %s",
            summary["commands_refused"],
            summary["case_ids"],
        )
    return summary


# The attribute Pub/Sub stamps on every message it forwards to a dead-letter
# topic, holding the number of deliveries the SOURCE subscription made before
# giving up. It is a string, like every Pub/Sub message attribute.
_DELIVERY_COUNT_ATTRIBUTE = "CloudPubSubDeadLetterSourceDeliveryCount"


def _delivery_count(message: dict) -> int:
    """How many times the source subscription delivered before dead-lettering.

    The attribute is read FIRST and the envelope's `deliveryAttempt` field is
    only a fallback, which is the opposite of what it looks like it should be.
    Pub/Sub populates `deliveryAttempt` only on subscriptions that themselves
    carry a dead-letter policy, and infra/events/setup.sh deliberately creates
    keplaria-events-dead-push without one — a dead letter has nowhere further
    to escalate to. On this endpoint that field is therefore always absent, so
    reading it alone would record `delivery_attempt: 0` on every dead-lettered
    event, and /review/failures would show 0 in a Deliveries column sitting
    beside prose saying the event was rejected five times. Do not "simplify"
    this back to the field.

    Parsed defensively because the attribute is a string arriving from an
    external system and this handler is contractually obliged to always return
    200: an unreadable count degrades to 0 rather than taking down the only
    path that records the event at all.
    """
    attributes = message.get("attributes")
    raw = (
        attributes.get(_DELIVERY_COUNT_ATTRIBUTE)
        if isinstance(attributes, dict)
        else None
    )
    if raw is None:
        raw = message.get("deliveryAttempt")
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "dead-letter delivery count is not an integer (%s); recording 0",
            str(raw)[:50],
        )
        return 0


@api.post("/pubsub/dead")
def dead_letter(envelope: Any = Body(...)) -> dict:
    """Record an event that exhausted redelivery on keplaria-events-push.

    ALWAYS returns 200, including when the payload is unparseable and when
    the Firestore write fails. This is the last stop: there is nowhere left
    to retry to, and a non-2xx here would only make the dead-letter
    subscription redeliver the dead letter. Everything that goes wrong is
    logged instead.

    The event is deliberately NOT re-processed. It arrived here because the
    ingress rejected it five times; automatically feeding it back into the
    same path is how a dead-letter queue becomes a slower retry loop.
    """
    message = envelope.get("message") if isinstance(envelope, dict) else None
    if not isinstance(message, dict):
        logger.error("dead-letter push with no message envelope; acking anyway")
        return {"status": "acked", "recorded": False}

    delivery_attempt = _delivery_count(message)
    event_id = None
    case_id = None
    payload: dict = {}
    try:
        payload = json.loads(base64.b64decode(message["data"]))
        event_id = payload.get("event_id")
        case_id = payload.get("case_id")
    except (ValueError, TypeError, KeyError, binascii.Error) as exc:
        # Never log the payload itself — the data-handling contract permits
        # case identifiers and masked values, not arbitrary event bodies.
        logger.error(
            "dead-lettered event is unparseable: %s: %s",
            type(exc).__name__,
            str(exc)[:200],
        )

    # messageId is the fallback key: an event whose body would not parse still
    # deserves a durable record, and Pub/Sub's own id is the only identity it
    # has left.
    key = event_id or message.get("messageId")
    if not key:
        logger.error("dead-lettered event has no usable id; acking anyway")
        return {"status": "acked", "recorded": False}

    logger.error(
        "event dead-lettered after %s deliveries: event %s case %s",
        delivery_attempt,
        key,
        case_id,
    )
    try:
        record_dead_event(get_client(), key, case_id, delivery_attempt, payload)
    except Exception as exc:
        logger.error(
            "failed to record dead-lettered event %s: %s: %s",
            key,
            type(exc).__name__,
            str(exc)[:200],
        )
        return {"status": "acked", "recorded": False}

    return {"status": "acked", "recorded": True, "event_id": key}

