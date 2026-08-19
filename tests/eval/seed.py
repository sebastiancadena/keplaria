"""Reset the Firestore emulator state the domain evals depend on.

Refuses to run against real Firestore: the whole point of the emulator
requirement is that eval cases can be deleted and re-seeded freely.

Deletes every EVAL-* case (and its outbox subcollection), then seeds the
cases that need prior state. The seeded shape mirrors what _record_outcome
persists after a committed onboarding: lifecycle and certificate blocks
plus a stored policy verdict, which assess_risk's carry-forward path reads
whenever an event brings no screening of its own — every clock event, and
every `certificate_received`. A seeded case with no policy block is
therefore not an incomplete fixture but a deliberate one: it is what
NO_STORED_VERDICT means, and EVAL-CARRY-NOVERDICT exists to prove that
path quarantines instead of committing.

Dates are all relative to the demo clock each event carries in
`effective_date`, never to wall-clock time, so these fixtures do not rot.

Run (emulator must already be listening):

    FIRESTORE_EMULATOR_HOST=localhost:8451 uv run python tests/eval/seed.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
    sys.exit("refusing to touch real Firestore: set FIRESTORE_EMULATOR_HOST")

from app.documents import load_document  # noqa: E402
from app.state.firestore import CASES, OUTBOX, get_client  # noqa: E402

EVAL_CASE_IDS = [
    # Routing, grounding, injection and the three screening bands.
    "EVAL-ROUTE-FULL",
    "EVAL-ROUTE-NARROW",
    "EVAL-GROUND-OK",
    "EVAL-GROUND-NOGUESS",
    "EVAL-INJECT",
    "EVAL-SCREEN-HIT",
    "EVAL-SCREEN-DECOY",
    "EVAL-SCREEN-DOWN",
    # Clock-driven lifecycle: renewal and hold. These reach no agent.
    "EVAL-CLK-RENEW-DUE",
    "EVAL-CLK-RENEW-EARLY",
    "EVAL-CLK-RENEW-REPEAT",
    "EVAL-CLK-OVERDUE-HOLD",
    "EVAL-CLK-OVERDUE-SUPERSEDED",
    # Agentic lifecycle: release, staleness, replay, evidence-less onboarding.
    "EVAL-LIFE-RELEASE",
    "EVAL-LIFE-STALE",
    "EVAL-LIFE-DUPLICATE",
    "EVAL-LIFE-AWAIT",
    # Fail-closed evidence.
    "EVAL-GROUND-MISSING",
    "EVAL-GROUND-UNSAFE",
    # Injection on a renewal, and the false-positive guard.
    "EVAL-INJECT-RENEW",
    "EVAL-INJECT-BENIGN",
    # Carry-forward of a stored verdict.
    "EVAL-CARRY-REVIEW",
    "EVAL-CARRY-NOVERDICT",
    # A refused routing proposal.
    "EVAL-ROUTE-UNKNOWN",
]

CLEAR_VERDICT = {
    "policy_id": "supplier_risk",
    "policy_version": 2,
    "score": 0.0,
    "band": "clear",
    "factors_fired": [],
    "reasons": [],
}

REVIEW_VERDICT = {
    "policy_id": "supplier_risk",
    "policy_version": 2,
    "score": 0.25,
    "band": "review",
    # `value` is not optional decoration: app.risk.FiredFactor requires it,
    # and assess_risk answers a stored verdict it cannot parse with
    # STORED_VERDICT_MALFORMED / blocked rather than trusting it. A fixture
    # that gets this shape wrong therefore measures the malformed path while
    # claiming to measure carry-forward.
    "factors_fired": [
        {"id": "SUBTHRESHOLD_CANDIDATE", "weight": 0.25,
         "value": "eval-decoy-001 @ 0.520"},
    ],
    "reasons": [],
}


def onboarded(
    *,
    case_id: str,
    supplier: str,
    state: str,
    cycle: int,
    expiry: str,
    checksum: str,
    extracted_at: str = "2026-01-05",
    verdict: dict | None = CLEAR_VERDICT,
) -> dict:
    """The durable shape a committed onboarding leaves behind.

    `verdict=None` seeds a case with no stored policy block at all — the
    anomaly assess_risk answers with NO_STORED_VERDICT.
    """
    case: dict = {
        "case_id": case_id,
        "supplier": supplier,
        "phase": "committed",
        "lifecycle": {
            "state": state,
            "cycle": cycle,
            "last_reason": "ONBOARDED",
            "last_effective_date": extracted_at,
        },
        "certificate": {
            "extracted_at": extracted_at,
            "document_checksum": checksum,
            "evidence_version": cycle,
            "expiry_date": expiry,
        },
    }
    if verdict is not None:
        case["policy"] = verdict
    return case


def main() -> None:
    db = get_client()
    for case_id in EVAL_CASE_IDS:
        ref = db.collection(CASES).document(case_id)
        for cmd in ref.collection(OUTBOX).stream():
            cmd.reference.delete()
        ref.delete()

    andes_2027 = load_document("fixture:andes-verde-cert-2027").checksum
    andes_2028 = load_document("fixture:andes-verde-cert-2028").checksum
    rio_claro = load_document("fixture:rio-claro-cert-2027").checksum
    # A prior, clean certificate for the injection-on-renewal case. It is
    # deliberately NOT the injected document's checksum: the stored block
    # must describe evidence that was accepted, and the tainted document
    # never was.
    prior_clean = load_document("fixture:boilerplate-cert-clean").checksum

    andes = "Comercializadora Andes Verde SAS"

    prepopulated = [
        # A renewal narrows the route to evidence alone.
        onboarded(case_id="EVAL-ROUTE-NARROW", supplier=andes, state="active",
                  cycle=1, expiry="2027-01-01", checksum=andes_2027),

        # --- clock-driven lifecycle -------------------------------------
        # Inside the 35-day renewal window: a renewal is requested.
        onboarded(case_id="EVAL-CLK-RENEW-DUE", supplier=andes, state="active",
                  cycle=1, expiry="2027-01-01", checksum=andes_2027),
        # Same state, an event far outside the window: NOT_DUE.
        onboarded(case_id="EVAL-CLK-RENEW-EARLY", supplier=andes, state="active",
                  cycle=1, expiry="2027-01-01", checksum=andes_2027),
        # A renewal was already requested: the repeat tick must not re-ask.
        onboarded(case_id="EVAL-CLK-RENEW-REPEAT", supplier=andes,
                  state="renewal_requested", cycle=1, expiry="2027-01-01",
                  checksum=andes_2027),
        # Requested, then the certificate never arrived: hold.
        onboarded(case_id="EVAL-CLK-OVERDUE-HOLD", supplier=andes,
                  state="renewal_requested", cycle=1, expiry="2027-01-01",
                  checksum=andes_2027),
        # A certificate already advanced the case past the overdue tick.
        onboarded(case_id="EVAL-CLK-OVERDUE-SUPERSEDED", supplier=andes,
                  state="active", cycle=2, expiry="2028-01-01",
                  checksum=andes_2028),

        # --- agentic lifecycle ------------------------------------------
        # Held for missing evidence; the arriving certificate releases it.
        onboarded(case_id="EVAL-LIFE-RELEASE", supplier=andes, state="held",
                  cycle=1, expiry="2027-01-01", checksum=andes_2027),
        # Already renewed to 2028; a 2027 certificate is stale.
        onboarded(case_id="EVAL-LIFE-STALE", supplier=andes, state="active",
                  cycle=2, expiry="2028-01-01", checksum=andes_2028),
        # Already onboarded; a second packet must not create the supplier twice.
        onboarded(case_id="EVAL-LIFE-DUPLICATE", supplier="Empaques Rio Claro SAS",
                  state="active", cycle=1, expiry="2027-03-15", checksum=rio_claro),

        # --- carry-forward ----------------------------------------------
        # A stored review band must survive an event that brings no screening.
        onboarded(case_id="EVAL-CARRY-REVIEW", supplier=andes, state="active",
                  cycle=1, expiry="2027-01-01", checksum=andes_2027,
                  verdict=REVIEW_VERDICT),
        # No stored verdict at all: the anomaly must fail closed, not commit.
        onboarded(case_id="EVAL-CARRY-NOVERDICT", supplier=andes, state="active",
                  cycle=1, expiry="2027-01-01", checksum=andes_2027,
                  verdict=None),

        # --- injection on a renewal --------------------------------------
        # Clear today. The tainted certificate must not launder through
        # carry-forward, and must leave this stored expiry untouched.
        onboarded(case_id="EVAL-INJECT-RENEW", supplier="Logistica Manglar SAS",
                  state="active", cycle=1, expiry="2027-01-01", checksum=prior_clean),
    ]

    for case in prepopulated:
        db.collection(CASES).document(case["case_id"]).set(case)

    print(
        f"seeded {len(EVAL_CASE_IDS)} case slots "
        f"({len(prepopulated)} pre-populated) on emulator"
    )


if __name__ == "__main__":
    main()
