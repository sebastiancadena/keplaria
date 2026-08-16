"""The authenticated review service: commit a decision, then release the work.

Every route is behind the authenticating proxy, including /healthz — the proxy
protects the service, not a selection of its paths, so there is no route here
whose absence of a guard could be a mistake.

The decision handler performs exactly the composition
tests/unit/test_approval_release.py pins: commit_approval, and
execute_pending_commands only if that commit returned committed. Draining is
deliberately not inside commit_approval, which records a decision and grants
nothing.

CSRF posture: /review/{case_id}/decide is POST-only (never reachable by
GET), and its *authentication* is a header IAP itself injects server-side
after validating the proxy's own session cookie for this domain — this code
never trusts a client-supplied identity header directly. That authentication
fact does NOT defend against CSRF, though: IAP adds the header after
checking a cookie, and a form-encoded POST is a CORS-simple request, so an
attacker page can make a signed-in reviewer's browser submit
`decision=approved` against a guessed case id and version with no preflight
and without ever reading the response — the browser attaches the IAP session
cookie on its own. `_is_cross_site` below is the mitigation: it refuses when
`Sec-Fetch-Site` (a header only the browser sets, never page script) says the
request did not originate same-site, and refuses when a present `Origin`
header's HOST does not match this request's own host — computed from the
request itself, deliberately not a new configured-origin environment
variable (`IAP_AUDIENCE` already showed what a config item that ships unset
costs).

The host comparison deliberately drops the scheme. Behind Cloud Run and IAP,
TLS terminates upstream and this container is reached over plain HTTP with
no `--proxy-headers`/forwarded-proto trust configured (that is deploy
configuration, out of scope here), so `request.url.scheme` reads `"http"`
even though the browser's `Origin` always reads `"https"`. Comparing scheme
would refuse every legitimate decision in production — a same-host,
different-scheme request is not what an attacker's `Origin` ever looks like
(their host can never match ours regardless of scheme), so dropping it costs
no real defence. It also drops the port for the same reason: the port this
container sees is Cloud Run's internal port, not the public one the browser
used, so comparing anything but the bare host would fail the same way. The
residual — a same-host, different-scheme *or* different-port request being
treated as safe — is exactly the case IAP's forced TLS and its own port
already make unreachable in this deployment.

The residual, stated rather than assumed: this defends a browser making the
request on a tricked page. It does nothing against a scripted caller that
already holds a stolen IAP session cookie and simply omits both headers —
that caller was never a browser and had no `Sec-Fetch-Site` to spoof or
suppress.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.executor.runner import effective_band, execute_pending_commands
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


def _is_cross_site(request: Request) -> bool:
    """True when a browser-set signal says this request did not originate
    same-site. See the module docstring for exactly what this does and does
    not defend against.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return True
    origin = request.headers.get("origin")
    if origin is not None:
        # Host only — deliberately not scheme or port. See the module
        # docstring: this container sees "http" and an internal port behind
        # Cloud Run/IAP regardless of what the browser used, so comparing
        # either would refuse every legitimate decision in production. An
        # attacker's Origin carries the attacker's own host and can never
        # match ours under any scheme or port, so dropping them costs no
        # real defence.
        if urlparse(origin).hostname != request.url.hostname:
            return True
    return False


def _case_is_decided(case: dict) -> bool:
    """True when a human decision currently applies to `case`.

    Delegates to app.executor.runner.effective_band, the one place that
    already knows an approval only applies while the case sits at the
    version it was committed against — a decision that has gone stale
    because the case advanced must NOT hide the case from the queue or the
    Approve/Reject form again, because it still needs a human look.
    """
    _, _, approval_id = effective_band(case)
    return approval_id is not None


@api.get("/healthz")
def healthz(reviewer: str = Depends(require_reviewer)) -> dict:
    return {"status": "ok"}


@api.get("/review", response_class=HTMLResponse)
def review_list(request: Request, reviewer: str = Depends(require_reviewer)):
    db = get_client()
    # A case whose phase is still AWAITING but whose current-version approval
    # already applies has been decided; commit_approval never touches phase
    # (only app.nodes' park_case does), so phase alone cannot tell "parked"
    # from "decided, awaiting the graph's next pass." Filtering on
    # _case_is_decided is what keeps a released case off this list instead
    # of it sitting here forever with a form that can only ever be refused
    # as a duplicate.
    parked = [
        c for c in list_cases(db)
        if c.get("phase") == AWAITING and not _case_is_decided(c)
    ]
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
            "decided": _case_is_decided(case),
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
    if _is_cross_site(request):
        # A hard refusal, not a rendered review_result.html: this is a
        # security boundary, not a domain outcome the reviewer needs to read
        # prose about. See the module docstring for exactly what this does
        # and does not defend against.
        raise HTTPException(status_code=403, detail="cross-site request blocked")

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
        # drains too: PERMISSIVE commands are correctly refused because the
        # effective band is blocked, but a RESTRICTIVE command (apply_hold)
        # bypasses that band guard by design — see
        # app.executor.runner.execute_pending_commands's docstring — and
        # still executes here even on a rejected case. "Drains" is not a
        # synonym for "executes nothing."
        executed = execute_pending_commands(db, case_id)

    # Never re-read as `.actor`: the audit record's actor field is who
    # decided, not what — this is only ever the decision word, and only
    # ever surfaced for the case currently being refused, not some other
    # reviewer's case.
    existing_decision = None
    if not result.committed:
        existing_case, _ = load_case(db, case_id)
        if existing_case:
            existing_decision = (existing_case.get("approval") or {}).get("decision")

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
            "existing_decision": existing_decision,
        },
    )
