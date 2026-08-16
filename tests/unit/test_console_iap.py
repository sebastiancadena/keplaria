"""Identity is verified, not asserted.

The plaintext X-Goog-Authenticated-User-Email header is spoofable by anything
that reaches the service by a path other than the authenticating proxy, and
`approval.actor` is the one field an audit record cannot afford to have wrong.
Every case below fails closed.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from console.iap import require_reviewer


def test_a_missing_assertion_is_refused(monkeypatch):
    monkeypatch.setenv("IAP_AUDIENCE", "/projects/1/locations/us-central1/services/x")
    with pytest.raises(HTTPException) as exc:
        require_reviewer("")
    assert exc.value.status_code == 403


def test_a_garbage_assertion_is_refused(monkeypatch):
    monkeypatch.setenv("IAP_AUDIENCE", "/projects/1/locations/us-central1/services/x")
    with pytest.raises(HTTPException) as exc:
        require_reviewer("not-a-jwt")
    assert exc.value.status_code == 403


def test_an_unconfigured_audience_refuses_rather_than_trusting_anything(monkeypatch):
    """A deploy that forgot the audience must not become an open endpoint."""
    monkeypatch.delenv("IAP_AUDIENCE", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_reviewer("anything-at-all")
    assert exc.value.status_code == 503


def test_a_verified_assertion_yields_the_email(monkeypatch):
    monkeypatch.setenv("IAP_AUDIENCE", "/projects/1/locations/us-central1/services/x")
    monkeypatch.setattr(
        "console.iap.id_token.verify_token",
        lambda assertion, request, audience=None, certs_url=None: {
            "email": "reviewer@example.com",
            "sub": "accounts.google.com:1",
        },
    )
    assert require_reviewer("a.valid.jwt") == "reviewer@example.com"


def test_a_verified_assertion_without_an_email_is_refused(monkeypatch):
    monkeypatch.setenv("IAP_AUDIENCE", "/projects/1/locations/us-central1/services/x")
    monkeypatch.setattr(
        "console.iap.id_token.verify_token",
        lambda assertion, request, audience=None, certs_url=None: {"sub": "x"},
    )
    with pytest.raises(HTTPException) as exc:
        require_reviewer("a.valid.jwt")
    assert exc.value.status_code == 403
