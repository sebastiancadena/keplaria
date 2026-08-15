"""Reset the Firestore emulator state the domain evals depend on.

Refuses to run against real Firestore: the whole point of the emulator
requirement is that eval cases can be deleted and re-seeded freely.

Deletes every EVAL-* case (and its outbox subcollection), then seeds the
one case that needs prior state: EVAL-ROUTE-NARROW, an already-onboarded
supplier whose stored 2027 certificate a `certificate_received` event
renews. The seeded shape mirrors what _record_outcome persists after a
committed onboarding: lifecycle and certificate blocks plus a stored clear
policy verdict, which assess_risk's carry-forward path requires — without
it a renewal scores NO_STORED_VERDICT and quarantines.

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
    "EVAL-ROUTE-FULL",
    "EVAL-ROUTE-NARROW",
    "EVAL-GROUND-OK",
    "EVAL-GROUND-NOGUESS",
    "EVAL-INJECT",
    "EVAL-SCREEN-HIT",
    "EVAL-SCREEN-DECOY",
    "EVAL-SCREEN-DOWN",
]


def main() -> None:
    db = get_client()
    for case_id in EVAL_CASE_IDS:
        ref = db.collection(CASES).document(case_id)
        for cmd in ref.collection(OUTBOX).stream():
            cmd.reference.delete()
        ref.delete()

    stored = load_document("fixture:andes-verde-cert-2027")
    db.collection(CASES).document("EVAL-ROUTE-NARROW").set(
        {
            "case_id": "EVAL-ROUTE-NARROW",
            "supplier": "Comercializadora Andes Verde SAS",
            "phase": "committed",
            "lifecycle": {
                "state": "active",
                "cycle": 1,
                "last_reason": "ONBOARDED",
                "last_effective_date": "2026-01-05",
            },
            "certificate": {
                "extracted_at": "2026-01-05",
                "document_checksum": stored.checksum,
                "evidence_version": 1,
                "expiry_date": "2027-01-01",
            },
            "policy": {
                "policy_id": "supplier_risk",
                "policy_version": 1,
                "score": 0.0,
                "band": "clear",
                "factors_fired": [],
                "reasons": [],
            },
        }
    )
    print(f"seeded {len(EVAL_CASE_IDS)} case slots (1 pre-populated) on emulator")


if __name__ == "__main__":
    main()
