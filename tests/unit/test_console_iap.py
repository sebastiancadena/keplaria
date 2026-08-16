"""Identity is verified, not asserted.

The plaintext X-Goog-Authenticated-User-Email header is spoofable by anything
that reaches the service by a path other than the authenticating proxy, and
`approval.actor` is the one field an audit record cannot afford to have wrong.
Every case below fails closed.

The `audience` and `certs_url` kwargs passed to `verify_token` ARE the
security boundary, not incidental plumbing: IAP's signing keys are global, so
a signature alone only proves some IAP-fronted service minted the token —
without an audience check, a valid assertion captured from someone else's
service verifies here too. Tests that stub `verify_token` capture and assert
on those two kwargs rather than discarding them, so a change that silently
drops the audience or certs_url check fails a test, not just a review.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from google.auth import exceptions as google_auth_exceptions

import console.iap
from console.iap import require_reviewer

AUDIENCE = "/projects/1/locations/us-central1/services/x"


def test_a_missing_assertion_is_refused(monkeypatch):
    monkeypatch.setenv("IAP_AUDIENCE", AUDIENCE)
    with pytest.raises(HTTPException) as exc:
        require_reviewer("")
    assert exc.value.status_code == 403


def test_a_garbage_assertion_is_refused(monkeypatch):
    """No network call: verify_token is mocked to raise, as it genuinely
    would for a malformed token, so this test cannot pass for the wrong
    reason (e.g. an unmocked outbound call to gstatic failing offline)."""
    monkeypatch.setenv("IAP_AUDIENCE", AUDIENCE)

    def _raise(assertion, request, audience=None, certs_url=None):
        raise ValueError("Wrong number of segments in token")

    monkeypatch.setattr("console.iap.id_token.verify_token", _raise)
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
    monkeypatch.setenv("IAP_AUDIENCE", AUDIENCE)
    seen = {}

    def _verify(assertion, request, audience=None, certs_url=None):
        seen["audience"] = audience
        seen["certs_url"] = certs_url
        return {"email": "reviewer@example.com", "sub": "accounts.google.com:1"}

    monkeypatch.setattr("console.iap.id_token.verify_token", _verify)
    assert require_reviewer("a.valid.jwt") == "reviewer@example.com"
    assert seen["audience"] == AUDIENCE
    assert seen["certs_url"] == console.iap.CERTS_URL


def test_a_verified_assertion_without_an_email_is_refused(monkeypatch):
    monkeypatch.setenv("IAP_AUDIENCE", AUDIENCE)
    seen = {}

    def _verify(assertion, request, audience=None, certs_url=None):
        seen["audience"] = audience
        seen["certs_url"] = certs_url
        return {"sub": "x"}

    monkeypatch.setattr("console.iap.id_token.verify_token", _verify)
    with pytest.raises(HTTPException) as exc:
        require_reviewer("a.valid.jwt")
    assert exc.value.status_code == 403
    assert seen["audience"] == AUDIENCE
    assert seen["certs_url"] == console.iap.CERTS_URL


def test_a_token_minted_for_a_different_audience_is_refused(monkeypatch):
    """A signature alone is not evidence for this service: IAP's signing keys
    are global, so an assertion captured from a different IAP-fronted
    service must be rejected on audience, not accepted on signature.

    This deployment expects AUDIENCE; the stub plays the real verify_token's
    behaviour for a token minted for some other, foreign audience — it
    raises rather than returning claims, exactly as the real IAP verifier
    does when the token's embedded audience does not match the `audience`
    kwarg it was asked to check against.
    """
    monkeypatch.setenv("IAP_AUDIENCE", AUDIENCE)

    def _verify_rejects_foreign_audience(assertion, request, audience=None, certs_url=None):
        assert audience == AUDIENCE  # the check this dependency must perform
        raise ValueError("Token has wrong audience")

    monkeypatch.setattr(
        "console.iap.id_token.verify_token", _verify_rejects_foreign_audience
    )
    with pytest.raises(HTTPException) as exc:
        require_reviewer("a.token.minted.for.a.different.service")
    assert exc.value.status_code == 403


def test_a_cert_fetch_failure_is_reported_as_unavailable_not_refused(monkeypatch):
    """verify_token fetches IAP's public keys before it inspects the token.
    If that fetch fails, nothing has been said about the assertion yet — an
    unreachable cert endpoint must surface as 503, not as a rejected
    identity indistinguishable from a bad token."""
    monkeypatch.setenv("IAP_AUDIENCE", AUDIENCE)

    def _raise(assertion, request, audience=None, certs_url=None):
        raise google_auth_exceptions.TransportError("connection refused")

    monkeypatch.setattr("console.iap.id_token.verify_token", _raise)
    with pytest.raises(HTTPException) as exc:
        require_reviewer("a.valid.jwt")
    assert exc.value.status_code == 503
