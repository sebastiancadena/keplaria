"""The public, read-only case console.

This app holds a read-only Firestore identity and mounts no route that writes.
It renders the allowlist projection, never a raw case document, so a field
added to the case document later is invisible here until someone adds it to
console.projection. The projection may lag the authoritative state; it can
never authorize anything.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.catalog import (
    CLOCK_EVENTS,
    COMMAND_ORDER,
    KNOWN_COMMANDS,
    CatalogLoadError,
    get_catalog,
)
from app.state.firestore import get_client
from console.grouping import group_by_supplier, route_label
from console.projection import public_case
from console.store import list_cases, list_inbox_for, load_case, load_inbox

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

api = FastAPI(title="keplaria-console")
api.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@api.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@api.get("/", response_class=HTMLResponse)
def index(request: Request):
    db = get_client()
    raw_cases = list_cases(db)
    # event_type lives on each case's inbox subcollection, never on the case
    # document itself (see console.store.load_inbox), so the list view has
    # to fetch it separately -- one inbox read per case already on the page,
    # never an unbounded scan.
    inbox_by_case = list_inbox_for(
        db, [c.get("case_id") for c in raw_cases if c.get("case_id")]
    )
    cases = [
        public_case(case, events=inbox_by_case.get(case.get("case_id"), []))
        for case in raw_cases
    ]
    # A count alone ("29 case(s)") says nothing a reader can use. Most of
    # these cases are the deployed-state evidence for a gate -- a ten-run
    # streak, a retry drill, a rejection pair -- so the list is legitimately
    # repetitive, and what makes it readable is knowing the shape of the
    # repetition before scrolling it: the tally, and one heading per supplier.
    phases: dict[str, int] = {}
    for case in cases:
        phase = case.get("phase") or "unknown"
        phases[phase] = phases.get(phase, 0) + 1
    groups = group_by_supplier(cases)
    for group in groups:
        for case in group["cases"]:
            case["route_label"] = route_label(case)
    return templates.TemplateResponse(
        request=request,
        name="cases.html",
        context={
            "groups": groups,
            "case_count": len(cases),
            "phase_counts": sorted(phases.items(), key=lambda kv: (-kv[1], kv[0])),
            "supplier_count": len(groups),
        },
    )


@api.get("/fleet", response_class=HTMLResponse)
def fleet(request: Request):
    """The fleet: each department's enforced scope, from the same catalog
    the authorization path loads. There is no separate presentational
    list — what renders here IS what routing and claim-time enforcement
    read, so this page cannot drift from authorization.
    """
    try:
        catalog = get_catalog()
    except CatalogLoadError:
        # Loudly, never an empty fleet: an empty page would read as "no
        # agents exist", when the truth is that the same load failure is
        # refusing every routing proposal right now.
        return templates.TemplateResponse(
            request=request,
            name="fleet_unavailable.html",
            context={},
            status_code=503,
        )

    # The page used to print every department's agents in full, which meant
    # rendering the same two manifests twice: procurement and compliance have
    # identical agent scope, and ~400 words of duplicate prose is exactly
    # what hid that from a reader. What differs between departments is which
    # cells are filled, so the scope renders as a matrix and each agent is
    # described once, below it.
    agent_columns = list(catalog.routable_ids())

    # Every command the lifecycle can issue, not only the ones some department
    # happens to permit. A command NO department may issue is a true and
    # load-bearing fact; sourcing the columns from the departments would
    # render it as a missing column instead of an empty one, which reads as
    # "no such command" rather than "nobody may issue it". Extras beyond the
    # declared order still appear, so adding a command cannot silently drop
    # it from this page.
    command_columns = list(COMMAND_ORDER) + sorted(
        KNOWN_COMMANDS - set(COMMAND_ORDER)
    )

    return templates.TemplateResponse(
        request=request,
        name="fleet.html",
        context={
            "catalog": catalog,
            "agents": catalog.agents,
            "agent_columns": agent_columns,
            "command_columns": command_columns,
            "scopes": [
                {
                    "name": name,
                    "description": scope.description,
                    "agents": [
                        (a, a in scope.permitted_agents) for a in agent_columns
                    ],
                    "commands": [
                        (c, c in scope.permitted_commands)
                        for c in command_columns
                    ],
                }
                for name, scope in catalog.departments.items()
            ],
            # Routing was absent from the fleet view entirely, though it is
            # half of what the page claims to show: app.policy validates a
            # proposed route against exactly this table, and app.catalog
            # refuses to load if a clock event maps to anything at all.
            "routes": [
                {
                    "event": event_type,
                    "agents": route,
                    "clock": event_type in CLOCK_EVENTS,
                }
                for event_type, route in catalog.event_routes.items()
            ],
        },
    )


@api.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(case_id: str, request: Request):
    db = get_client()
    case, commands = load_case(db, case_id)
    if case is None:
        # HTML, not FastAPI's default JSON {"detail": ...} — this is a page
        # a person mistyping a case id lands on, not an API client parsing a
        # body.
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={"case_id": case_id},
            status_code=404,
        )
    events = load_inbox(db, case_id)
    return templates.TemplateResponse(
        request=request,
        name="case.html",
        context={"case": public_case(case, commands, events)},
    )
