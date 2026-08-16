"""The public, read-only case console.

This app holds a read-only Firestore identity and mounts no route that writes.
It renders the allowlist projection, never a raw case document, so a field
added to the case document later is invisible here until someone adds it to
console.projection. The projection may lag the authoritative state; it can
never authorize anything.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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


@api.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(case_id: str, request: Request):
    db = get_client()
    case, commands = load_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="no such case")
    return templates.TemplateResponse(
        request=request,
        name="case.html",
        context={"case": public_case(case, commands)},
    )
