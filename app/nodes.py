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
from google.cloud import firestore
from opentelemetry import trace
from pydantic import ValidationError

from app.documents import DocumentUnavailable, load_document
from app.grounding import RedactedDerivative, validate as grounding_validate
from app.injection import scan as scan_for_injection
from app.lifecycle import decide
from app.policy import CLOCK_EVENTS, PolicyError, validate_route
from app.risk import BLOCKED, CLEAR, REVIEW, RiskVerdict, assess_case, lifecycle_timing
from app.schemas import CanonicalEvent, ComplianceAssessment, ScreeningResult
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
    compliance: dict | None = None,
    injection: list | None = None,
) -> None:
    """Persist a compact routing/screening summary onto the case doc.

    Session state is invisible outside the engine; this is what lets
    spikes/thin_vertical/verify.py (and anyone else reading Firestore)
    substantiate the routing and screening decisions without reaching into
    the graph.

    The persisted `policy` block is the authoritative record of the gate's
    decision. app.executor.runner re-reads it before draining a command, so
    this is not merely a projection — it is read back for enforcement.

    The persisted `compliance` block is the durable audit record of the
    interpreter's assessment — the full recommendation, rationale, and
    per-candidate reasoning, not a trimmed summary. A trace span is fine for
    debugging a single run, but spans expire and are never the place a
    reviewer should have to go looking for why a case was escalated; that
    text has to still be readable from Firestore long after the run that
    produced it.

    A malformed `screening` dict (one app.risk.assess already rejected as
    SCREENING_MALFORMED) still flows in here from the quarantine_case /
    park_case terminals — the gate refusing to raise does not mean the
    thing it refused is well-formed. This function must persist a "why was
    this case decided this way" record for THAT input too, so it reads every
    candidate field with `.get()`, never `[]`, and never assumes `candidates`
    is a list of dicts.

    The persisted `injection` block is the durable proof that the document
    injection gate fired and where — pattern ids, page indices, offsets,
    counts. It must never carry the matched payload text, and that is
    enforced HERE, at the write boundary, by projecting each finding to
    exactly pattern_id/page/offset rather than storing the caller's list
    verbatim — not merely inherited from app.injection.Finding's current
    schema. A reviewer needs to substantiate the block without reaching into
    a trace that has since expired; they do not need, and must never be
    handed, a stored copy of the payload itself.

    `injection` distinguishes "never scanned" from "scanned and clean":
    `None` means no derivative ever existed to scan — a clock event, one
    with no document_ref, or one whose document_ref could not be loaded at
    all (load_document raising DocumentUnavailable leaves `derivative` None
    too, and an unreadable document genuinely was never scanned) — and this
    function writes no `injection` key at all, same absence discipline as
    routing/screening/policy/compliance above. An empty list means a document
    WAS scanned and came back clean, and that positive fact is worth
    recording — `{"tainted": False, "finding_count": 0, "findings": []}` —
    because it says the gate ran, not that it never applied.
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
    # merge=True rather than update(): commit_commands/quarantine_case run
    # after claim_event in production (the case doc always exists by then),
    # but integration tests that drive the graph directly without going
    # through the ingress adapter never create it — merge tolerates both.
    #
    # merge=True only skips a key that is ABSENT from the payload; a key
    # present with value None still overwrites whatever is durably stored
    # with null. Every caller of this function may legitimately lack one or
    # more of routing/screening/policy in ctx.state (a clock event carries no
    # routing or screening; quarantine_case/park_case can run before a policy
    # verdict exists), and that absence must read as "nothing new to say
    # here," never as "erase what was recorded earlier." So the payload is
    # built from only the non-None values.
    #
    # updated_at is always present, unlike every other key above: it is not
    # domain state a caller can legitimately omit, it is the write-boundary
    # invariant every reader of this collection (the console's list_cases
    # among them) depends on. claim_event sets it on the ingress path, but a
    # test that drives the graph directly, bypassing claim_event, must not
    # produce a case document lacking it — that document would be invisible
    # to any query ordered on the field, permanently, not just late.
    payload: dict = {"phase": phase, "updated_at": firestore.SERVER_TIMESTAMP}
    if routing is not None:
        payload["routing"] = routing
    if summary is not None:
        payload["screening"] = summary
    if policy is not None:
        payload["policy"] = policy
    if compliance is not None:
        payload["compliance"] = compliance
    if injection is not None:
        # Project each finding to exactly pattern_id/page/offset rather than
        # storing the list verbatim. app.injection.Finding carries only those
        # three fields today, so this is currently a no-op filter — but the
        # no-payload-text guarantee this function's docstring promises must
        # be enforced HERE, at the write boundary, not merely inherited from
        # whatever shape the caller happens to pass in. If a future scanner
        # widens Finding with a matched-text field, this projection is what
        # stops it reaching Firestore instead of silently starting to.
        # Read every field with `.get()`, same defensive discipline as the
        # screening-candidates projection above: injection is total and must
        # never raise on a malformed entry either.
        findings = [
            {
                "pattern_id": f.get("pattern_id") if isinstance(f, dict) else None,
                "page": f.get("page") if isinstance(f, dict) else None,
                "offset": f.get("offset") if isinstance(f, dict) else None,
            }
            for f in injection
        ]
        payload["injection"] = {
            "tainted": bool(findings),
            "finding_count": len(findings),
            "findings": findings,
        }
    db.collection(CASES).document(case_id).set(payload, merge=True)


def _format_pages(pages: list[str]) -> str:
    """Render page text with explicit zero-based page markers.

    The Evidence agent must cite a page index for every field it extracts,
    and app.grounding.validate checks that index against the derivative's
    page list — so the prompt must make the index-to-text mapping explicit,
    not just concatenate the pages and hope the model counts correctly.
    """
    return "\n\n".join(f"Page {i}:\n{text}" for i, text in enumerate(pages))


def load_case_state(node_input, ctx: Context) -> Event:
    """Reload durable case state, then classify the event.

    This is what makes a wake-up months later meaningful: the graph decides
    from the stored lifecycle and certificate blocks, not from whatever the
    event happens to carry.

    Routing here is the coordinator bypass. A clock-driven event engages no
    agents, so sending it to an LlmAgent would spend a model call to be told
    'no agents' and would put a delegation decision in the trace that was
    never made.

    Also publishes the derivative as flat, top-level state keys
    (`document_checksum`, `document_pages`) rather than only the nested
    `derivative` dict. ADK's instruction-template state injection
    (instructions_utils.inject_session_state) only resolves a bare
    identifier against a top-level session-state key — it has no subscript
    or attribute syntax for reaching into a nested dict — so evidence_agent's
    instruction templates against these flat keys, not `{derivative}`.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")
    event_type = case.get("event_type", "")

    snap = get_client().collection(CASES).document(case_id).get()
    case_state = (snap.to_dict() or {}) if snap.exists else {}

    derivative = None
    ref = case.get("document_ref")
    if ref:
        try:
            derivative = load_document(ref).model_dump()
        except DocumentUnavailable as exc:
            # Absent evidence, not a crash: the grounding gate is the single
            # place that decides what unusable evidence means, and it
            # quarantines rather than proceeding.
            #
            # The span carries only the exception type name, never str(exc)
            # or the ref itself: DocumentUnavailable's messages embed the raw
            # document_ref, and in this system a document_ref deterministically
            # names the entity it belongs to (e.g. a supplier's certificate
            # fixture) — so it is entity-identifying data, which this
            # project's telemetry contract keeps off spans. IDs, codes, and
            # counts only; entity-identifying values go to Firestore, never
            # to a span.
            derivative = None
            with tracer.start_as_current_span("document_unavailable") as span:
                span.set_attribute("keplaria.case_id", case_id)
                span.set_attribute("keplaria.document_error", type(exc).__name__)

    document_checksum = ""
    document_pages = ""
    # app.injection.scan is total and heuristic (see its module docstring):
    # it never raises, and it taints on a directive+machine-reader-signal
    # conjunction found in the document's own text, not on anything an event
    # carries. Scanning here, before a single page reaches document_pages, is
    # what makes the taint decision precede every other read of this
    # derivative in the graph.
    injection = scan_for_injection(derivative.get("pages") or []) if derivative else None
    tainted = bool(injection and injection.tainted)

    if derivative and not tainted:
        document_checksum = derivative.get("checksum", "")
        document_pages = _format_pages(derivative.get("pages") or [])

    if tainted:
        # The payload must not reach any state key an agent instruction can
        # resolve — and that holds regardless of which route this event
        # takes. document_pages/document_checksum are the ONLY channel by
        # which page text reaches a model prompt: ADK's instruction-template
        # state injection resolves a bare identifier against a top-level
        # session-state key (see this function's docstring), and both are
        # blanked above for a tainted document. apply_route does check
        # document_tainted and steers a tainted new_supplier_packet past
        # evidence_agent entirely — but the blanking here does not rely on
        # that: whichever route a future change picks, {document_pages} and
        # {document_checksum} resolve to nothing for this event either way.
        # Patterns and offsets only on the span, never the matched text
        # itself — this module's telemetry contract keeps entity-identifying
        # and payload values off spans (see the DocumentUnavailable branch
        # above for the same rule applied to a document_ref).
        with tracer.start_as_current_span("document_tainted") as span:
            span.set_attribute("keplaria.case_id", case_id)
            span.set_attribute("keplaria.injection_finding_count", len(injection.findings))
            span.set_attribute(
                "keplaria.injection_patterns",
                sorted({f.pattern_id for f in injection.findings}),
            )

    route = "clock" if event_type in CLOCK_EVENTS else "agentic"

    with tracer.start_as_current_span("load_case_state") as span:
        span.set_attribute("keplaria.case_id", case_id)
        span.set_attribute("keplaria.event_class", route)
        span.set_attribute(
            "keplaria.lifecycle_state",
            (case_state.get("lifecycle") or {}).get("state", "onboarding"),
        )

    return Event(
        output={"event_class": route},
        state={
            "case_state": case_state,
            # Deliberately still published even when tainted — not an
            # oversight. validate_evidence needs the raw derivative (page
            # text included) to feed app.grounding.validate, which returns
            # structured reason codes rather than forwarding text to a model.
            # No LlmAgent instruction references {derivative} (only the flat
            # document_pages/document_checksum keys resolve into a prompt),
            # so this is a bounded exposure — present in session state and
            # the persisted session store, but never reachable by a model —
            # not the "nowhere in state" claim the blanked keys above make.
            "derivative": derivative,
            "document_checksum": document_checksum,
            "document_pages": document_pages,
            "document_tainted": tainted,
            # None (not []) when there was no derivative to scan at all (a
            # clock event, or one with no document_ref) — distinct from a
            # document that was scanned and came back clean, which publishes
            # the genuinely empty list `injection.findings` produces. `scan`
            # is total, so `injection` is only ever None here when it was
            # never called; `injection is not None` (not truthiness) is what
            # keeps that distinction, since an InjectionVerdict with no
            # findings is still a truthy object. _record_outcome's `injection
            # is not None` guard downstream depends on this: without it,
            # every quarantined/parked/committed case — clock events
            # included — got an `injection: {tainted: false, ...}` block
            # implying a scan that never happened.
            "injection_findings": (
                [f.model_dump() for f in injection.findings]
                if injection is not None else None
            ),
            # The coordinator's channel to the event. Its node_input is this
            # Event's `output` — {"event_class": ...} — which names the event's
            # *class*, not its type, so without these keys the only place
            # `new_supplier_packet` appears is parse_case's output in session
            # history. Reading the class instead of the type is what produced
            # the observed empty-route flake. Published on both paths and as
            # flat top-level keys, for the same reason document_checksum and
            # document_pages are.
            "event_type": event_type,
            "supplier_name": case.get("supplier") or "",
            "has_document": "yes" if ref else "no",
        },
        route=route,
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

    A remaining PolicyError still blocks the case rather than skipping it: a
    refused proposal must never reach commit_commands. But validate_route no
    longer raises just because the coordinator over-proposed — a known agent
    the event type doesn't permit (e.g. `compliance` on `certificate_received`)
    is silently dropped from the route it returns, not refused. `refused`
    here is therefore reserved for what validate_route still does raise on:
    an unknown agent name, or a genuinely empty proposal on an event type
    that requires one. `dropped` records the narrowing separately, so the
    persisted case still shows exactly what the coordinator asked for versus
    what policy actually ran — an audit trail the earlier all-or-nothing
    refusal didn't need, because a refusal already carried `proposed` and an
    empty `route` was self-explanatory. A narrowed route needs the diff
    spelled out or a reviewer can't tell "coordinator proposed exactly this"
    from "coordinator proposed more and policy trimmed it."

    Evidence only has a document to extract from when the event actually
    carries a `document_ref`. A packet with no document is not a failure —
    app.lifecycle's AWAITING_EVIDENCE branch onboards the supplier and waits
    for a certificate to arrive later — so a permitted "evidence" route with
    no document reaches the gate as if evidence had nothing to do, not as a
    quarantine. validate_evidence's own NO_DOCUMENT path is reserved for the
    other case: a document_ref was given and could not be loaded, which is a
    real failure of a promise someone made.

    A tainted document (load_case_state's `document_tainted`) is treated the
    same way: evidence has nothing it may safely extract from, since
    document_pages/document_checksum were already blanked at load time. The
    case reaches compliance screening when the event type permits it — e.g.
    a `new_supplier_packet` where compliance is proposed alongside evidence —
    because an injected certificate is itself a fraud signal and the
    screening record is most worth having for exactly that case; but an
    event type whose permitted route is evidence-only (e.g.
    `certificate_received`) never reaches screening at all, tainted or not.
    This is deliberately not routed to "blocked" — that branch is reserved
    for a refused coordinator proposal, and taint is a fact about the
    document, not a routing failure. Either way the case still converges on
    assess_risk (via "screen" when compliance is proposed, or "skip"
    otherwise), which is what actually blocks a tainted case.
    """
    case = ctx.state.get("case", {})
    event_type = case.get("event_type", "")
    has_document = bool(case.get("document_ref"))
    tainted = bool(ctx.state.get("document_tainted"))

    proposed = list((node_input or {}).get("route", []))
    reason = (node_input or {}).get("reason", "")

    try:
        route = validate_route(event_type, proposed)
        refused = None
        dropped = [agent for agent in dict.fromkeys(proposed) if agent not in route]
    except PolicyError as exc:
        # A rejected proposal is a policy outcome the trace must show, and it
        # must never fall through to a side effect — quarantine_case is the
        # only node this can reach next.
        route, refused, dropped = [], str(exc), []

    evidence_skipped_no_document = "evidence" in route and not has_document
    evidence_skipped_tainted_document = "evidence" in route and has_document and tainted

    decision = {
        "proposed": proposed,
        "route": route,
        "dropped": dropped,
        "reason": reason,
        "refused": refused,
        "evidence_skipped_no_document": evidence_skipped_no_document,
        "evidence_skipped_tainted_document": evidence_skipped_tainted_document,
    }

    if refused is not None:
        next_route = "blocked"
    elif "evidence" in route and has_document and not tainted:
        # A tainted document is handled exactly like a permitted evidence
        # route with nothing to extract from: evidence has no work it may
        # do. Not "blocked" — that branch is reserved for a refused
        # coordinator proposal, and taint is a fact about the document, not
        # a routing failure. The case still reaches assess_risk (via
        # "screen" below when compliance is also proposed, or "skip"
        # otherwise), which is what actually blocks it — an injected
        # certificate is itself a fraud signal, so the entity is worth
        # screening precisely because it was tainted.
        next_route = "evidence"
    elif "compliance" in route:
        next_route = "screen"
    else:
        next_route = "skip"

    return Event(
        output=decision,
        state={"routing": decision},
        route=next_route,
    )


MAX_EVIDENCE_ATTEMPTS = 2


def validate_evidence(node_input, ctx: Context) -> Event:
    """Independently check the Evidence agent's output against the document.

    A schema-valid answer is not a grounded one. Every value must resolve to
    a verbatim span on a declared page of the exact document supplied, or the
    case quarantines with zero writes.

    The retry is bounded and explicit: one re-ask, then quarantine. The
    back-edge to the agent is what makes the second attempt a genuinely fresh
    extraction rather than a re-validation of the same output, and the
    attempt counter in state is what keeps it from looping.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")
    routing = ctx.state.get("routing") or {}
    attempts = int(ctx.state.get("evidence_attempts") or 0) + 1

    raw = node_input
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}

    derivative_state = ctx.state.get("derivative")
    with tracer.start_as_current_span("validate_evidence") as span:
        span.set_attribute("keplaria.case_id", case_id)
        span.set_attribute("keplaria.evidence_attempt", attempts)

        if not derivative_state:
            span.set_attribute("keplaria.grounding_reason", "NO_DOCUMENT")
            return Event(
                output={"grounded": False, "reason": "NO_DOCUMENT"},
                state={"evidence_attempts": attempts},
                route="ungrounded",
            )

        try:
            derivative = RedactedDerivative(**derivative_state)
        except ValidationError:
            # derivative_state is a plain dict written to session state
            # (see load_case_state) and read back here after a round trip
            # through ADK's session store under is_resumable=True. A
            # malformed round trip must quarantine, not raise out of the
            # node: the platform allows only one concurrent query, so a
            # raising node becomes retry pressure rather than a decision.
            # This is not a retry candidate the way an ungrounded extraction
            # is — the corrupted value is the stored derivative itself, so a
            # fresh Evidence Agent extraction would be validated against the
            # exact same malformed data on the next attempt.
            span.set_attribute("keplaria.grounding_reason", "DERIVATIVE_MALFORMED")
            return Event(
                output={"grounded": False, "reason": "DERIVATIVE_MALFORMED"},
                state={"evidence_attempts": attempts},
                route="ungrounded",
            )
        verdict = grounding_validate(raw if isinstance(raw, dict) else {}, derivative)
        span.set_attribute("keplaria.grounded", verdict.grounded)
        span.set_attribute("keplaria.grounding_reason", verdict.reason)

        if not verdict.grounded:
            route = "retry" if attempts < MAX_EVIDENCE_ATTEMPTS else "ungrounded"
            return Event(
                output={"grounded": False, "reason": verdict.reason,
                        "field": verdict.field, "attempt": attempts},
                state={"evidence_attempts": attempts},
                route=route,
            )

    next_route = "screen" if "compliance" in (routing.get("route") or []) else "skip"
    return Event(
        output={"grounded": True, "attempt": attempts},
        state={"evidence": raw, "evidence_attempts": attempts},
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
            compliance=ctx.state.get("compliance"),
            injection=ctx.state.get("injection_findings"),
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

    Claims the commands the case would run and executes none of them. The
    graph never reaches the ERP from any terminal; app.executor.runner does
    that, outside the engine and under a different identity, and it refuses
    every permissive command whose case is not `clear`. So these commands sit
    PENDING and drain to `refused_by_policy` on every pass until an approval
    applies — which is what makes a parked case releasable at all, and what
    lets a reviewer see what they are approving rather than only that someone
    is waiting.

    quarantine_case, by contrast, still claims nothing: a `blocked` case
    queues no work, and no approval can exist for one.

    The phase is `awaiting_approval`: a case parked pending a human decision.
    This is NOT a live pause — RequestInput is not in this graph. A later
    milestone replaces this node with a real pause on the same branch, keeping
    this contract.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")
    policy = ctx.state.get("policy")
    db = get_client()

    with tracer.start_as_current_span("park_case") as span:
        span.set_attribute("keplaria.case_id", case_id)
        span.set_attribute("keplaria.parked", True)
        decision, claimed = _claim_lifecycle_commands(db, case_id, case, ctx)
        span.set_attribute("keplaria.lifecycle_reason", decision.reason)
        span.set_attribute("keplaria.cycle", decision.cycle)
        span.set_attribute("keplaria.commands_parked", [c["action"] for c in claimed])
        _record_outcome(
            db,
            case_id,
            "awaiting_approval",
            ctx.state.get("routing"),
            ctx.state.get("screening"),
            policy,
            compliance=ctx.state.get("compliance"),
            injection=ctx.state.get("injection_findings"),
        )

    return Event(
        output={
            "status": "awaiting_approval",
            "case_id": case_id,
            "policy": policy,
            "routing": ctx.state.get("routing"),
            "commands": claimed,
        }
    )


def screen_supplier(node_input, ctx: Context) -> Event:
    """Screen the supplier against self-hosted yente over private VPC.

    Records the outcome either way: an unreachable service is a result the trace
    must show, not an exception that hides the network.

    Also decides whether the compliance interpreter runs at all. With no
    candidates to weigh, there is nothing for it to interpret, and an
    unreachable yente is left as a purely deterministic, fail-closed result —
    routing it to an LlmAgent would spend a model call to reason about an
    empty screen. Publishes `screening_candidates` and
    `screening_supplier_name` as flat top-level state keys for the same
    reason `load_case_state` publishes `document_checksum` /
    `document_pages`: instruction-template injection only resolves bare
    top-level keys, never a subscript into the nested `screening` dict.
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
    interpret = screening.reachable and bool(screening.candidates)
    return Event(
        output=payload,
        state={
            "screening": payload,
            "screening_candidates": json.dumps(screening.candidates, indent=2),
            "screening_supplier_name": name,
        },
        route="interpret" if interpret else "score",
    )


ALLOWED_RECOMMENDATIONS = ("corroborate_block", "escalate_review", "note_clear")


def apply_compliance(node_input, ctx: Context) -> Event:
    """Independently check the Compliance agent's output against the screening.

    This is the deterministic seam between the model and the risk gate: an
    LlmAgent's structured output is a claim, not a fact, and this is what
    holds it to the candidates screen_supplier actually returned. The agent
    cannot invent a candidate id out of nothing — every id it references
    must trace back to `ctx.state["screening"]`, and the recommendation must
    fall within the fixed vocabulary the gate understands. A malformed,
    ungrounded, or out-of-vocabulary assessment is recorded as invalid and
    carried forward for the gate to see; it is never raised, for the same
    reason validate_evidence never raises — the engine allows one concurrent
    query, so an exception here becomes retry pressure instead of a decision.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")
    screening = ctx.state.get("screening")
    screening = screening if isinstance(screening, dict) else {}
    raw_candidates = screening.get("candidates")
    candidates = raw_candidates if isinstance(raw_candidates, list) else []
    known_ids = {c.get("id") for c in candidates if isinstance(c, dict)}

    raw = node_input
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            # Falls back to None, not {}: apply_compliance must tell an
            # unparseable payload apart from a parsed-but-empty one, since
            # the two are recorded as different outcomes downstream (an
            # empty-but-valid assessment can still carry a recommendation,
            # an unparseable one never can).
            raw = None

    valid = True
    invalid_reason = None
    assessment = None
    if isinstance(raw, dict):
        try:
            assessment = ComplianceAssessment(**raw)
        except ValidationError:
            valid, invalid_reason = False, "UNPARSEABLE"
    else:
        valid, invalid_reason = False, "UNPARSEABLE"

    if assessment is not None:
        if assessment.recommendation not in ALLOWED_RECOMMENDATIONS:
            valid, invalid_reason = False, "BAD_RECOMMENDATION"
        elif any(a.candidate_id not in known_ids for a in assessment.assessments):
            valid, invalid_reason = False, "UNKNOWN_CANDIDATE_ID"

    record = assessment.model_dump() if assessment is not None else {}
    record["valid"] = valid
    if invalid_reason is not None:
        record["invalid_reason"] = invalid_reason

    with tracer.start_as_current_span("apply_compliance") as span:
        span.set_attribute("keplaria.case_id", case_id)
        span.set_attribute("keplaria.compliance_valid", valid)
        if invalid_reason is not None:
            span.set_attribute("keplaria.compliance_invalid_reason", invalid_reason)
        if valid:
            # Only a recommendation that passed the vocabulary check reaches
            # the span. A BAD_RECOMMENDATION value is arbitrary model output
            # with unbounded cardinality, and it could carry entity-
            # identifying text onto the trace; the invalid case is already
            # fully described by keplaria.compliance_invalid_reason above.
            span.set_attribute(
                "keplaria.compliance_recommendation", assessment.recommendation
            )

    return Event(output=record, state={"compliance": record})


def assess_risk(node_input, ctx: Context) -> Event:
    """The gate. Deterministic policy decides whether commands may be queued.

    Two modes. An event that performed its own screening is scored fresh. An
    event that brought no screening of its own carries the stored verdict
    forward instead.

    Carry-forward is not an optimization. Re-scoring from `screening=None`
    fires no factors, scores zero, and lands `clear`: the passage of time
    alone would launder a blocked supplier, and the executor's backstop
    reads exactly this stored band. A stored band therefore carries forward
    unchanged, and a case with no stored verdict at all is an anomaly that
    carries `blocked`.

    The condition is `screening is None`, not "is this a clock event" — an
    earlier version of this gate conditioned on event type, which happened
    to coincide with "no screening" for every clock event but silently
    stopped coinciding the moment an agentic event could also reach this
    node with no fresh screening of its own: certificate_received's route is
    `{evidence}` only (see app/policy.py's ALLOWED_ROUTES), so it never
    engages compliance and never populates `screening`. Scoring that fresh
    from `screening=None` would land `clear` regardless of what the last
    real screening found — laundering a previously blocked supplier via a
    mailed-in certificate, which is exactly the outcome carry-forward exists
    to prevent. Conditioning on the actual absence of screening, rather than
    on event type as a proxy for it, covers every event that can reach this
    node with nothing of its own to score, present or future.

    Only factor IDs reach the span; the values that triggered them go to
    Firestore via _record_outcome.
    """
    case = ctx.state.get("case", {})
    screening = ctx.state.get("screening")
    case_state = ctx.state.get("case_state") or {}
    carry_forward = screening is None

    with tracer.start_as_current_span("assess_risk") as span:
        if carry_forward:
            stored = case_state.get("policy")
            stored = stored if isinstance(stored, dict) else {}
            carried = True
            if stored.get("band"):
                try:
                    verdict = RiskVerdict(**stored)
                except ValidationError:
                    # The stored block came out of a schemaless Firestore
                    # document. A gate that raises here is worse than one that
                    # refuses: the platform allows a single concurrent query,
                    # so an exception becomes retry pressure instead of a
                    # decision. Fail closed and say why.
                    verdict = RiskVerdict(
                        policy_id="carry_forward", policy_version=0, score=1.0,
                        band=BLOCKED, reasons=["STORED_VERDICT_MALFORMED"],
                    )
            else:
                verdict = RiskVerdict(
                    policy_id="carry_forward", policy_version=0, score=1.0,
                    band=BLOCKED, reasons=["NO_STORED_VERDICT"],
                )
        else:
            verdict = assess_case(
                screening=screening,
                case={**case, "document_tainted": bool(ctx.state.get("document_tainted"))},
            )
            carried = False

        # Taint tightens both branches. It is load-bearing on the carry-
        # forward path above — that's the whole reason it exists: a
        # certificate_received event never screens, so without this, a
        # tainted certificate would inherit whatever band was stored before
        # this document arrived. On the fresh-scoring path it is currently
        # unreachable in practice, because DOCUMENT_INJECTION's factor weight
        # already clears the policy's block threshold inside assess_case, so
        # `verdict.band` is already BLOCKED by the time this guard runs;
        # here it stands as deliberate defence against a future reweighting
        # of that factor, not active logic today.
        #
        # It is deliberately allowed to tighten a carried-forward verdict,
        # unlike the compliance escalation below (whose own `not carried`
        # guard — not this block's position above it — is what keeps it off
        # the carried path). The two are not alike: the compliance record is
        # a model's interpretation of a screen, so it may only nudge a fresh
        # `clear` to `review`. Taint is a deterministic fact about the
        # document THIS event brought, established by code before any agent
        # ran. Carrying a verdict forward over evidence it does not cover is
        # exactly what carry-forward exists to prevent in the other
        # direction. One-directional, like every other guard here: it can
        # only tighten, so it cannot launder anything.
        #
        # Because this guard is `verdict.band != BLOCKED`, the taint marker
        # lands in a different place — or nowhere — depending on which path
        # produced `verdict`: `factors_fired` on fresh scoring; `reasons`
        # (appended here) on a carry-forward over a stored verdict that
        # wasn't already blocked; and neither field when the stored verdict
        # was already BLOCKED, or was missing/malformed (NO_STORED_VERDICT /
        # STORED_VERDICT_MALFORMED already forced BLOCKED above, so this
        # block never runs for them). A consumer that only reads the verdict
        # must check both `factors_fired` and `reasons` to detect taint; the
        # persisted `injection` block (see _record_outcome) is the reliable,
        # path-independent record and should be preferred for audit.
        if ctx.state.get("document_tainted") and verdict.band != BLOCKED:
            verdict = verdict.model_copy(
                update={
                    "band": BLOCKED,
                    "reasons": [*verdict.reasons, "DOCUMENT_INJECTION"],
                }
            )

        # The compliance record can only tighten a fresh clear verdict to
        # review, never loosen review/blocked and never touch a carried-
        # forward verdict — it has an opinion, not veto power over the gate.
        # corroborate_block gets its own branch even though the intended
        # path (a match: true candidate) already blocks on the deterministic
        # score alone: a low-scoring, no-match candidate can still read as a
        # near-exact name to the model, and that contradiction needs its own
        # reason code rather than silently riding through as clear.
        if not carried and verdict.band == CLEAR:
            compliance = ctx.state.get("compliance")
            if isinstance(compliance, dict):
                if not compliance.get("valid"):
                    verdict = verdict.model_copy(
                        update={
                            "band": REVIEW,
                            "reasons": [*verdict.reasons, "COMPLIANCE_ASSESSMENT_INVALID"],
                        }
                    )
                elif compliance.get("recommendation") == "escalate_review":
                    verdict = verdict.model_copy(
                        update={
                            "band": REVIEW,
                            "reasons": [*verdict.reasons, "COMPLIANCE_ESCALATION"],
                        }
                    )
                elif compliance.get("recommendation") == "corroborate_block":
                    verdict = verdict.model_copy(
                        update={
                            "band": REVIEW,
                            "reasons": [*verdict.reasons, "COMPLIANCE_BLOCK_CORROBORATION"],
                        }
                    )

        span.set_attribute("keplaria.case_id", case.get("case_id", ""))
        span.set_attribute("keplaria.policy_version", verdict.policy_version)
        span.set_attribute("keplaria.risk_score", verdict.score)
        span.set_attribute("keplaria.risk_band", verdict.band)
        span.set_attribute("keplaria.verdict_carried_forward", carried)
        span.set_attribute(
            "keplaria.factors_fired", [f.id for f in verdict.factors_fired]
        )

    payload = verdict.model_dump()
    return Event(output=payload, state={"policy": payload}, route=verdict.band)


def _claim_lifecycle_commands(db, case_id: str, case: dict, ctx: Context) -> tuple:
    """Run decide() and claim every command it names; persist lifecycle state.

    Shared by commit_commands and park_case, which differ only in what they
    then record and return. decide() is the single source of truth for what a
    case does next, and a second hand-rolled claim loop in the other terminal
    is exactly the drift that building _DRAIN_ORDER from app.lifecycle's
    constants was meant to prevent.

    Claiming is not executing. Nothing here reaches the ERP: claim_command
    writes an outbox row, and app.executor.runner decides — under a different
    identity, outside the graph — whether that row ever becomes a write. That
    separation is what lets park_case record a case's intended work without
    performing any of it.

    Returns (decision, claimed) where `claimed` is one dict per command with
    its action, cycle, queued/already_done/dead status, and any external_id a
    previous completed claim recorded.
    """
    case_state = ctx.state.get("case_state") or {}

    decision = decide(
        case_state=case_state or {"supplier": case.get("supplier")},
        event=case,
        evidence=ctx.state.get("evidence"),
        timing=lifecycle_timing(),
    )

    claimed: list[dict] = []
    for command in decision.commands:
        claim = claim_command(
            db, case_id, command.action, decision.cycle, command.payload
        )
        # A refused claim has more than one meaning, and reporting them all as
        # `queued` states something false in the very record built to make
        # stuck work visible. claim_command refuses a DEAD command instead of
        # resetting it to PENDING (see app/state/commands.py), and a
        # review-band case re-claims on every later event, so a command the
        # executor gave up on would otherwise be reported as freshly queued
        # for the rest of the case's life. Only an acquired claim is queued;
        # anything else reports the status the ledger actually holds, so a
        # future refusal reason cannot silently read as fresh work either.
        if claim.acquired:
            status = "queued"
        elif claim.status == DONE:
            status = "already_done"
        else:
            status = claim.status
        claimed.append({
            "action": command.action,
            "cycle": decision.cycle,
            "status": status,
            "external_id": claim.external_id,
        })

    lifecycle_block = {
        "state": decision.state,
        "cycle": decision.cycle,
        "last_effective_date": case.get("effective_date"),
        "last_reason": decision.reason,
    }
    # updated_at alongside lifecycle/certificate, not just phase/policy/etc
    # in _record_outcome above: a case whose only fresh write on a given pass
    # is a lifecycle advance (a clock tick with no new routing or policy
    # verdict) must still sort as recently touched wherever a query orders
    # on this field — see console/store.py's list_cases.
    update = {"lifecycle": lifecycle_block, "updated_at": firestore.SERVER_TIMESTAMP}
    if decision.certificate is not None:
        update["certificate"] = decision.certificate

    db.collection(CASES).document(case_id).set(update, merge=True)
    return decision, claimed


def commit_commands(node_input, ctx: Context) -> Event:
    """The `clear` terminal. Claims commands; never calls the ERP.

    Every path that may write converges here — onboarding and every clock
    branch alike — so there is one place that decides what a case does next
    and one audit record of why. Reached only via the assess_risk gate's
    `clear` branch, so by the time this node runs the case already carries a
    policy verdict that permits a write. A flagged supplier terminates at
    quarantine_case and claims nothing at all.

    park_case shares the claim step (see _claim_lifecycle_commands) but not
    the verdict: a near-match supplier parks with its commands queued and
    unreleased, and only an approval lets the executor drain them. So this is
    no longer the only terminal that claims — it is the only one that claims
    against a verdict already permitting execution.

    The graph still never calls the ERP itself. The Agent Runtime engine's
    PSC-I network attachment routes egress through keplaria-vpc, whose Cloud
    NAT is ENDPOINT_TYPE_VM only — it does not cover a PSC-I NIC, so the
    engine has a path to the private VPC (yente) but none to the public
    internet. A Frappe Cloud call from inside this node times out on TCP
    connect every time, deterministically; it is not a transient fault to
    retry around.

    This is also the architecturally correct split, not just a network
    workaround: the ERP executor is a deterministic non-agent component and a
    separate authorization boundary from the graph. This node's only job is
    to record each deterministic command `decide()` names via claim_command;
    the actual write happens in app.executor.runner.execute_pending_commands,
    run from the ingress (ordinary Cloud Run, normal egress) after the engine
    call returns, and again — idempotently — on any duplicate-event
    redelivery.

    A decision with zero commands (a refusal — e.g. NOT_DUE, ALREADY_HELD)
    must write nothing to the outbox: only the lifecycle/certificate blocks
    and the outcome summary are persisted, exactly like quarantine_case and
    park_case.
    """
    case = ctx.state.get("case", {})
    case_id = case.get("case_id", "")

    db = get_client()

    with tracer.start_as_current_span("commit_commands") as span:
        span.set_attribute("keplaria.case_id", case_id)
        decision, claimed = _claim_lifecycle_commands(db, case_id, case, ctx)
        span.set_attribute("keplaria.lifecycle_reason", decision.reason)
        span.set_attribute("keplaria.lifecycle_state", decision.state)
        span.set_attribute("keplaria.cycle", decision.cycle)
        span.set_attribute(
            "keplaria.commands", [c["action"] for c in claimed]
        )

        _record_outcome(
            db,
            case_id,
            "committed" if decision.commands else "no_action",
            ctx.state.get("routing"),
            ctx.state.get("screening"),
            ctx.state.get("policy"),
            compliance=ctx.state.get("compliance"),
            injection=ctx.state.get("injection_findings"),
        )

    return Event(
        output={
            "status": "committed" if decision.commands else "no_action",
            "case_id": case_id,
            "reason": decision.reason,
            "state": decision.state,
            "cycle": decision.cycle,
            "commands": claimed,
        }
    )
