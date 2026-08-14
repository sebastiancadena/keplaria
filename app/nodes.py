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
from app.risk import assess_case
from app.schemas import CanonicalEvent, ScreeningResult
from app.state.commands import DONE, claim_command
from app.state.firestore import CASES, get_client

YENTE_BASE_URL = os.environ.get("YENTE_BASE_URL", "http://10.10.0.2:8000")
YENTE_DATASET = os.environ.get("YENTE_DATASET", "keplaria_synthetic")
# yente drops results below `cutoff` entirely; the default 0.5 would hide the
# sub-threshold candidates a reviewer needs to judge a false positive.
YENTE_CUTOFF = 0.0

tracer = trace.get_tracer("keplaria.nodes")


def _record_outcome(
    db,
    case_id: str,
    phase: str,
    routing: dict | None,
    screening: dict | None,
    policy: dict | None = None,
) -> None:
    """Persist a compact routing/screening summary onto the case doc.

    Session state is invisible outside the engine; this is what lets
    verify.py (and anyone else reading Firestore) substantiate the routing
    and screening decisions without reaching into the graph.

    The persisted `policy` block is the authoritative record of the gate's
    decision. app.executor.runner re-reads it before draining a command, so
    this is not merely a projection — it is read back for enforcement.

    A malformed `screening` dict (one app.risk.assess already rejected as
    SCREENING_MALFORMED) still flows in here from the quarantine_case /
    park_case terminals — the gate refusing to raise does not mean the
    thing it refused is well-formed. This function must persist a "why was
    this case decided this way" record for THAT input too, so it reads every
    candidate field with `.get()`, never `[]`, and never assumes `candidates`
    is a list of dicts.
    """
    summary = None
    if screening:
        raw_candidates = screening.get("candidates", [])
        candidates = raw_candidates if isinstance(raw_candidates, list) else []
        summary = {
            "reachable": screening.get("reachable"),
            "endpoint": screening.get("endpoint"),
            "flagged": screening.get("flagged", []),
            "candidate_count": len(candidates),
            "candidates": [
                {
                    "id": c.get("id") if isinstance(c, dict) else None,
                    "score": c.get("score") if isinstance(c, dict) else None,
                    "match": c.get("match") if isinstance(c, dict) else None,
                }
                for c in candidates[:3]
            ],
        }
    # merge=True rather than update(): queue_supplier/quarantine_case run
    # after claim_event in production (the case doc always exists by then),
    # but integration tests that drive the graph directly without going
    # through the ingress adapter never create it — merge tolerates both.
    db.collection(CASES).document(case_id).set(
        {"phase": phase, "routing": routing, "screening": summary, "policy": policy},
        merge=True,
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
        _record_outcome(
            get_client(),
            case_id,
            "quarantined",
            routing,
            ctx.state.get("screening"),
            ctx.state.get("policy"),
        )

    return Event(
        output={
            "status": "quarantined",
            "case_id": case_id,
            "routing": ctx.state.get("routing"),
        }
    )


def park_case(node_input, ctx: Context) -> Event:
    """Terminal node for the `review` band — a case parked for a human.

    Zero writes: no command claim, no ERP call, exactly like quarantine_case.

    The phase is `awaiting_approval`: a case parked pending a human decision.
    This is NOT a live pause — RequestInput is not in this graph. A later
    milestone replaces this node with a real pause on the same branch.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")
    policy = ctx.state.get("policy")

    with tracer.start_as_current_span("park_case") as span:
        span.set_attribute("keplaria.case_id", case_id)
        span.set_attribute("keplaria.parked", True)
        _record_outcome(
            get_client(),
            case_id,
            "awaiting_approval",
            ctx.state.get("routing"),
            ctx.state.get("screening"),
            policy,
        )

    return Event(
        output={
            "status": "awaiting_approval",
            "case_id": case_id,
            "policy": policy,
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


def assess_risk(node_input, ctx: Context) -> Event:
    """The gate. Deterministic policy decides whether the ERP command may be queued.

    Routes on the band, never on model output. Reached from BOTH the screened
    branch and the skip branch, so no path to queue_supplier bypasses a
    verdict — which is what lets the executor treat a missing verdict as an
    anomaly to refuse rather than a state it must tolerate.

    Only factor IDs reach the span. The values that triggered them go to
    Firestore via _record_outcome: the data handling contract keeps
    entity-identifying values out of telemetry.
    """
    case = ctx.state.get("case", {})
    screening = ctx.state.get("screening")

    with tracer.start_as_current_span("assess_risk") as span:
        verdict = assess_case(screening=screening, case=case)
        span.set_attribute("keplaria.case_id", case.get("case_id", ""))
        span.set_attribute("keplaria.policy_version", verdict.policy_version)
        span.set_attribute("keplaria.risk_score", verdict.score)
        span.set_attribute("keplaria.risk_band", verdict.band)
        span.set_attribute(
            "keplaria.factors_fired", [f.id for f in verdict.factors_fired]
        )

    payload = verdict.model_dump()
    return Event(output=payload, state={"policy": payload}, route=verdict.band)


def queue_supplier(node_input, ctx: Context) -> Event:
    """Claim the create_supplier command and stop. Never calls the ERP.

    Reached only via the assess_risk gate's `clear` branch, so by the time
    this node runs the case already carries a policy verdict that permits an
    ERP command. A flagged or near-match supplier terminates at
    quarantine_case or park_case instead and never arrives here.

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
        claim = claim_command(db, case_id, "create_supplier", 1, payload)

        if not claim.acquired and claim.status == DONE:
            span.set_attribute("keplaria.command_replayed", True)
            _record_outcome(
                db,
                case_id,
                "executed",
                ctx.state.get("routing"),
                ctx.state.get("screening"),
                ctx.state.get("policy"),
            )
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
        _record_outcome(
            db,
            case_id,
            "queued",
            ctx.state.get("routing"),
            ctx.state.get("screening"),
            ctx.state.get("policy"),
        )

    return Event(
        output={
            "status": "command_queued",
            "case_id": case_id,
            "screening": ctx.state.get("screening"),
            "routing": ctx.state.get("routing"),
        }
    )
