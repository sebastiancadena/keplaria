"""Deterministic graph nodes.

State moves through `Event(state=...)` rather than direct `ctx.state` writes, so
every transition is replayable from persisted event history.
"""

from __future__ import annotations

import json
import os

import httpx
from google.adk.agents.context import Context
from google.adk.events.event import Event
from opentelemetry import trace

from app.policy import PolicyError, validate_route
from app.schemas import CanonicalEvent, ScreeningResult
from app.state.commands import DONE, claim_command
from app.state.firestore import CASES, get_client

YENTE_BASE_URL = os.environ.get("YENTE_BASE_URL", "http://10.10.0.2:8000")
YENTE_DATASET = os.environ.get("YENTE_DATASET", "keplaria_synthetic")
# yente drops results below `cutoff` entirely; the default 0.5 would hide the
# sub-threshold candidates a reviewer needs to judge a false positive.
YENTE_CUTOFF = 0.0

tracer = trace.get_tracer("keplaria.nodes")


def _record_outcome(db, case_id: str, phase: str, routing: dict | None, screening: dict | None) -> None:
    """Persist a compact routing/screening summary onto the case doc.

    Session state is invisible outside the engine; this is what lets
    verify.py (and anyone else reading Firestore) substantiate the routing
    and screening decisions without reaching into the graph.

    The persisted `screening` block is a record of what yente returned, not a
    gate: nothing downstream reads `flagged` as a condition. See
    `queue_supplier` for why, and for what is still missing.
    """
    summary = None
    if screening:
        candidates = screening.get("candidates", [])
        summary = {
            "reachable": screening.get("reachable"),
            "endpoint": screening.get("endpoint"),
            "flagged": screening.get("flagged", []),
            "candidate_count": len(candidates),
            "candidates": [
                {"id": c["id"], "score": c["score"], "match": c["match"]}
                for c in candidates[:3]
            ],
        }
    # merge=True rather than update(): queue_supplier/quarantine_case run
    # after claim_event in production (the case doc always exists by then),
    # but integration tests that drive the graph directly without going
    # through the ingress adapter never create it — merge tolerates both.
    db.collection(CASES).document(case_id).set(
        {"phase": phase, "routing": routing, "screening": summary}, merge=True
    )


def parse_case(node_input, ctx: Context) -> Event:
    """Validate the inbound event against the canonical schema."""
    raw = node_input
    if hasattr(raw, "parts"):  # START emits types.Content
        raw = "".join(part.text or "" for part in raw.parts)
    if isinstance(raw, str):
        raw = json.loads(raw)

    event = CanonicalEvent(**raw)
    return Event(
        output=event.model_dump(),
        state={"case": event.model_dump()},
    )


def apply_route(node_input, ctx: Context) -> Event:
    """Validate the coordinator's proposal and pick the executable branch.

    Any PolicyError blocks the case rather than skipping it: a refused
    proposal must never reach queue_supplier. validate_route already
    encodes the one legitimate empty route (an event type that requires no
    agents, e.g. evidence_overdue) as a normal return rather than a raise, so
    checking `refused is not None` here is sufficient to distinguish
    "genuinely nothing to do" from "the proposal was rejected" without
    duplicating the ALLOWED_ROUTES policy table in this module.

    The evidence agent is not built yet, so a permitted 'evidence' selection is
    recorded and skipped rather than silently dropped.
    """
    case = ctx.state.get("case", {})
    event_type = case.get("event_type", "")

    proposed = list((node_input or {}).get("route", []))
    reason = (node_input or {}).get("reason", "")

    try:
        route = validate_route(event_type, proposed)
        refused = None
    except PolicyError as exc:
        # A rejected proposal is a policy outcome the trace must show, and it
        # must never fall through to a side effect — quarantine_case is the
        # only node this can reach next.
        route, refused = [], str(exc)

    decision = {
        "proposed": proposed,
        "route": route,
        "reason": reason,
        "refused": refused,
        "pending_implementation": [a for a in route if a == "evidence"],
    }

    if refused is not None:
        next_route = "blocked"
    elif "compliance" in route:
        next_route = "screen"
    else:
        next_route = "skip"

    return Event(
        output=decision,
        state={"routing": decision},
        route=next_route,
    )


def quarantine_case(node_input, ctx: Context) -> Event:
    """Terminal node for a refused routing proposal.

    Records the refusal and stops. No Firestore command claim, no ERP call —
    a rejected coordinator proposal must produce zero writes.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")
    routing = ctx.state.get("routing")

    with tracer.start_as_current_span("quarantine_case") as span:
        span.set_attribute("keplaria.case_id", case_id)
        span.set_attribute("keplaria.quarantined", True)
        _record_outcome(get_client(), case_id, "quarantined", routing, ctx.state.get("screening"))

    return Event(
        output={
            "status": "quarantined",
            "case_id": case_id,
            "routing": ctx.state.get("routing"),
        }
    )


def screen_supplier(node_input, ctx: Context) -> Event:
    """Screen the supplier against self-hosted yente over private VPC.

    Records the outcome either way: an unreachable service is a result the trace
    must show, not an exception that hides the network.
    """
    case = ctx.state.get("case", {})
    name = case.get("supplier", "")
    query = {"queries": {"q": {"schema": "Company", "properties": {"name": [name]}}}}

    with tracer.start_as_current_span("screen_supplier") as span:
        span.set_attribute("keplaria.case_id", case.get("case_id", ""))
        try:
            response = httpx.post(
                f"{YENTE_BASE_URL}/match/{YENTE_DATASET}",
                params={"cutoff": YENTE_CUTOFF},
                json=query,
                timeout=30,
            )
            response.raise_for_status()
            results = response.json()["responses"]["q"]["results"]
            candidates = [
                {
                    "id": r["id"],
                    "caption": r["caption"],
                    "score": r["score"],
                    "match": r["match"],
                    "topics": r.get("properties", {}).get("topics", []),
                }
                for r in results
            ]
            screening = ScreeningResult(
                endpoint=YENTE_BASE_URL,
                supplier=name,
                reachable=True,
                candidates=candidates,
                flagged=[c["id"] for c in candidates if c["match"]],
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            screening = ScreeningResult(
                endpoint=YENTE_BASE_URL,
                supplier=name,
                reachable=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        span.set_attribute("keplaria.screening_reachable", screening.reachable)

    payload = screening.model_dump()
    return Event(output=payload, state={"screening": payload})


def queue_supplier(node_input, ctx: Context) -> Event:
    """Claim the create_supplier command and stop. Never calls the ERP.

    IMPORTANT — screening does not gate this write. `ctx.state["screening"]`
    (including `flagged`, the yente match outcome) is recorded and advisory
    only in this slice: it is attached to the output and persisted onto the
    case doc via `_record_outcome`, but nothing here reads it as a condition.
    A screening hit does NOT prevent the create_supplier command from being
    claimed and, downstream, executed against the ERP. Gating on screening
    results is deterministic policy/risk work that has not been built yet —
    this node unconditionally queues the command regardless of what
    screen_supplier found.

    The Agent Runtime engine's PSC-I network attachment routes egress through
    keplaria-vpc, whose Cloud NAT is ENDPOINT_TYPE_VM only — it does not cover
    a PSC-I NIC, so the engine has a path to the private VPC (yente) but none
    to the public internet. A Frappe Cloud call from inside this node times
    out on TCP connect every time, deterministically; it is not a transient
    fault to retry around.

    This is also the architecturally correct split, not just a network
    workaround: the ERP executor is a deterministic non-agent component and a
    separate authorization boundary from the graph. This node's only job is
    to record the deterministic command via claim_command; the actual write
    happens in app.executor.runner.execute_pending_commands, run from the
    ingress (ordinary Cloud Run, normal egress) after the engine call
    returns, and again — idempotently — on any duplicate-event redelivery.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")
    supplier = case.get("supplier", "")

    db = get_client()
    payload = {"supplier_name": supplier, "country": "Colombia"}

    with tracer.start_as_current_span("queue_supplier") as span:
        span.set_attribute("keplaria.case_id", case_id)
        claim = claim_command(db, case_id, "create_supplier", payload)

        if not claim.acquired and claim.status == DONE:
            span.set_attribute("keplaria.command_replayed", True)
            _record_outcome(db, case_id, "executed", ctx.state.get("routing"), ctx.state.get("screening"))
            return Event(
                output={
                    "status": "already_executed",
                    "case_id": case_id,
                    "external_id": claim.external_id,
                    "screening": ctx.state.get("screening"),
                    "routing": ctx.state.get("routing"),
                }
            )

        span.set_attribute("keplaria.command_queued", True)
        _record_outcome(db, case_id, "queued", ctx.state.get("routing"), ctx.state.get("screening"))

    return Event(
        output={
            "status": "command_queued",
            "case_id": case_id,
            "screening": ctx.state.get("screening"),
            "routing": ctx.state.get("routing"),
        }
    )
