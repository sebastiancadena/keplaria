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

from app.executor.frappe import FrappeError, create_or_update_supplier, frappe_client
from app.policy import PolicyError, validate_route
from app.schemas import CanonicalEvent, ScreeningResult
from app.state.commands import DONE, claim_command, record_failure, record_success
from app.state.firestore import get_client

YENTE_BASE_URL = os.environ.get("YENTE_BASE_URL", "http://10.10.0.2:8000")
YENTE_DATASET = os.environ.get("YENTE_DATASET", "keplaria_synthetic")
# yente drops results below `cutoff` entirely; the default 0.5 would hide the
# sub-threshold candidates a reviewer needs to judge a false positive.
YENTE_CUTOFF = 0.0

tracer = trace.get_tracer("keplaria.nodes")


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
        # A rejected proposal is a policy outcome the trace must show, and the
        # case still proceeds deterministically rather than failing open.
        route, refused = [], str(exc)

    decision = {
        "proposed": proposed,
        "route": route,
        "reason": reason,
        "refused": refused,
        "pending_implementation": [a for a in route if a == "evidence"],
    }
    return Event(
        output=decision,
        state={"routing": decision},
        route="screen" if "compliance" in route else "skip",
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


def execute_supplier(node_input, ctx: Context) -> Event:
    """Claim the command, create the supplier, record the external ID.

    This is the only node permitted to write to the ERP.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")
    supplier = case.get("supplier", "")

    db = get_client()
    payload = {"supplier_name": supplier, "country": "Colombia"}

    with tracer.start_as_current_span("execute_supplier") as span:
        span.set_attribute("keplaria.case_id", case_id)
        claim = claim_command(db, case_id, "create_supplier", payload)

        if not claim.acquired and claim.status == DONE:
            span.set_attribute("keplaria.command_replayed", True)
            return Event(
                output={
                    "status": "already_executed",
                    "case_id": case_id,
                    "external_id": claim.external_id,
                    "screening": ctx.state.get("screening"),
                    "routing": ctx.state.get("routing"),
                }
            )

        try:
            with frappe_client() as client:
                result = create_or_update_supplier(client, supplier)
        except (FrappeError, httpx.HTTPError) as exc:
            record_failure(db, case_id, "create_supplier", f"{type(exc).__name__}: {exc}")
            span.set_attribute("keplaria.command_failed", True)
            return Event(
                output={
                    "status": "failed",
                    "case_id": case_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        record_success(db, case_id, "create_supplier", result["external_id"], result)
        span.set_attribute("keplaria.external_id", result["external_id"])

    return Event(
        output={
            "status": "executed",
            "case_id": case_id,
            "external_id": result["external_id"],
            "created": result["created"],
            "screening": ctx.state.get("screening"),
            "routing": ctx.state.get("routing"),
        }
    )
