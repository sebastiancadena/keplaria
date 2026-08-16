"""The authenticated review service: commit a decision, then release the work.

Every route is behind the authenticating proxy, including /healthz — the proxy
protects the service, not a selection of its paths, so there is no route here
whose absence of a guard could be a mistake.

The decision handler performs exactly the composition
tests/unit/test_approval_release.py pins: commit_approval, and
execute_pending_commands only if that commit returned committed. Draining is
deliberately not inside commit_approval, which records a decision and grants
nothing.

CSRF posture, stated rather than assumed: /review/{case_id}/decide is
POST-only (never reachable by GET), and its authentication is a header IAP
itself adds after validating the proxy's own session cookie for this domain —
this code never trusts a client-supplied identity header directly. That still
leaves a residual forged-submission risk if a signed-in reviewer's browser is
made to POST to this path from another origin while their IAP session is
live; nothing here adds an origin check or a per-form anti-CSRF token, and
that gap is called out as an open concern rather than silently declared
closed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.executor.runner import execute_pending_commands
from app.state.approvals import commit_approval
from app.state.firestore import get_client
from console.iap import require_reviewer
from console.store import list_cases, load_case

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

api = FastAPI(title="keplaria-review")
api.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

AWAITING = "awaiting_approval"

# A refusal reason is a thing that happened to a person, not a code to look up.
REFUSAL_MESSAGES = {
    "duplicate_approval": "This case was already decided at this version.",
    "stale_approval": "The case moved on since this page was loaded. Re-read it "
                      "and decide again against the current version.",
    "not_awaiting": "This case is not parked for review.",
    "unknown_case": "No such case.",
    "invalid_decision": "That is not a decision this service accepts.",
}


@api.get("/healthz")
def healthz(reviewer: str = Depends(require_reviewer)) -> dict:
    return {"status": "ok"}


@api.get("/review", response_class=HTMLResponse)
def review_list(request: Request, reviewer: str = Depends(require_reviewer)):
    db = get_client()
    parked = [c for c in list_cases(db) if c.get("phase") == AWAITING]
    return templates.TemplateResponse(
        request=request,
        name="review_list.html",
        context={"cases": parked, "reviewer": reviewer},
    )


@api.get("/review/{case_id}", response_class=HTMLResponse)
def review_case(
    case_id: str, request: Request, reviewer: str = Depends(require_reviewer)
):
    db = get_client()
    case, commands = load_case(db, case_id)
    if case is None:
        return templates.TemplateResponse(
            request=request,
            name="review_result.html",
            context={"case_id": case_id, "message": REFUSAL_MESSAGES["unknown_case"],
                     "committed": False, "executed": []},
            status_code=200,
        )
    pending = [c for c in commands if c.get("status") != "done"]
    return templates.TemplateResponse(
        request=request,
        name="review_case.html",
        context={
            "case": case,
            "commands": commands,
            "pending": pending,
            "reviewer": reviewer,
            "expected_case_version": case.get("case_version"),
        },
    )


@api.post("/review/{case_id}/decide", response_class=HTMLResponse)
def decide(
    case_id: str,
    request: Request,
    decision: str = Form(...),
    expected_case_version: int = Form(...),
    reviewer: str = Depends(require_reviewer),
):
    db = get_client()
    # Derived, not generated: a double click, a browser retry and a resubmitted
    # form all produce the same id, so the second attempt is refused as a
    # duplicate with no client-side idempotency token to plumb through. The
    # consequence, accepted deliberately: a decision is final for a given case
    # version.
    approval_id = f"{case_id}:v{expected_case_version}"
    result = commit_approval(
        db, case_id, approval_id, expected_case_version, decision, reviewer
    )

    executed: list[dict] = []
    if result.committed:
        # Only after a committed decision, and in this order. A rejection
        # drains too — and correctly executes nothing, because the effective
        # band is blocked.
        executed = execute_pending_commands(db, case_id)

    message = (
        f"Decision recorded: {decision}."
        if result.committed
        else REFUSAL_MESSAGES.get(result.reason, "The decision was refused.")
    )
    return templates.TemplateResponse(
        request=request,
        name="review_result.html",
        context={
            "case_id": case_id,
            "committed": result.committed,
            "reason": result.reason,
            "message": message,
            "case_version": result.case_version,
            "executed": executed,
        },
    )
