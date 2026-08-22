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

from app.catalog import CatalogLoadError, get_catalog
from app.state.firestore import get_client
from console.projection import public_case
from console.store import list_cases, load_case

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
    cases = [public_case(case) for case in list_cases(db)]
    return templates.TemplateResponse(
        request=request, name="cases.html", context={"cases": cases}
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

    manifests = {agent.id: agent for agent in catalog.agents}
    return templates.TemplateResponse(
        request=request,
        name="fleet.html",
        context={
            "catalog": catalog,
            "shared_agents": [a for a in catalog.agents if not a.routable],
            "departments": [
                {
                    "name": name,
                    "scope": scope,
                    "agents": [manifests[a] for a in scope.permitted_agents],
                }
                for name, scope in catalog.departments.items()
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
    return templates.TemplateResponse(
        request=request,
        name="case.html",
        context={"case": public_case(case, commands)},
    )
