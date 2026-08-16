"""Who approved this, established by signature rather than by assertion.

The proxy forwards two things: a plaintext email header and a signed JWT. Only
the second is evidence. The first is a convenience that becomes a forgery the
moment the service is reachable by any path that does not traverse the proxy —
a misconfigured ingress setting, a direct service URL, a future migration.

Every failure here is closed: no identity, no decision.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"


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
            google_requests.Request(),
            audience=audience,
            certs_url=CERTS_URL,
        )
    except Exception as exc:
        # Deliberately broad: a malformed token, a bad signature, an expired
        # one and a wrong audience are all the same answer here, and the
        # distinction is not something to leak to the caller.
        raise HTTPException(
            status_code=403, detail="invalid identity assertion"
        ) from exc
    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=403, detail="assertion carries no email")
    return email
