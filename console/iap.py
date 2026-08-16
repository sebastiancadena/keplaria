"""Who approved this, established by signature rather than by assertion.

The proxy forwards two things: a plaintext email header and a signed JWT. Only
the second is evidence. The first is a convenience that becomes a forgery the
moment the service is reachable by any path that does not traverse the proxy —
a misconfigured ingress setting, a direct service URL, a future migration.

Verification is against the audience this deployment was told to expect
(`IAP_AUDIENCE`) and against Google's published IAP public keys (`CERTS_URL`).
The audience check is the security boundary, not a formality: the IAP
signing keys are global, so a signature alone only proves "IAP minted this
for some audience recently" — a valid assertion captured from a different
IAP-fronted service and replayed here would verify without it.

Certs are fetched fresh on every call; there is no TTL cache. That fetch can
itself fail (network/egress trouble), and that failure is deliberately not
folded into "invalid identity assertion" — an unreachable cert endpoint is an
availability problem, not a rejected reviewer, and the two must read
differently to whoever is debugging a stuck approval live.

Every failure here is closed: no identity, no decision.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"

# Reused across requests rather than built fresh per call. This does not
# cache certificates (verify_token still fetches them over HTTPS on every
# call, deliberately — see module docstring); it only reuses the underlying
# HTTP session/connection pool.
_REQUEST = google_requests.Request()


def _audience() -> str:
    audience = os.environ.get("IAP_AUDIENCE", "")
    if not audience:
        # A service that cannot state who it is cannot check that a token was
        # addressed to it, and a token addressed elsewhere is not evidence
        # about this service. Refusing every decision is the only safe
        # direction; the alternative is an approval endpoint that accepts
        # tokens minted for something else entirely.
        raise HTTPException(status_code=503, detail="reviewer identity unconfigured")
    return audience


def require_reviewer(x_goog_iap_jwt_assertion: str = Header(default="")) -> str:
    """Return the verified reviewer email, or raise.

    FastAPI dependency. Tests substitute it through `api.dependency_overrides`;
    there is deliberately no bypass environment variable in shipped code.
    """
    audience = _audience()
    if not x_goog_iap_jwt_assertion:
        raise HTTPException(status_code=403, detail="no identity assertion")
    try:
        claims = id_token.verify_token(
            x_goog_iap_jwt_assertion,
            _REQUEST,
            audience=audience,
            certs_url=CERTS_URL,
        )
    except google_auth_exceptions.TransportError as exc:
        # verify_token fetches IAP's public keys before it ever looks at the
        # token. If that fetch fails, nothing has been said about the
        # assertion's validity yet — this is our dependency being down, not
        # a rejected identity, and conflating the two turns an infrastructure
        # outage into a mystery 403 on the approval path.
        raise HTTPException(
            status_code=503, detail="identity verification unavailable"
        ) from exc
    except Exception as exc:
        # Deliberately broad otherwise: a malformed token, a bad signature,
        # an expired one and a wrong audience are all the same answer here,
        # and the distinction is not something to leak to the caller.
        raise HTTPException(
            status_code=403, detail="invalid identity assertion"
        ) from exc
    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=403, detail="assertion carries no email")
    return email
