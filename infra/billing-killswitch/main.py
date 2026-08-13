"""Billing kill switch: detaches the billing account when spend exceeds the cap.

Triggered by Cloud Billing budget notifications on a Pub/Sub topic. The budget's
`budgetAmount` IS the hard cap: once reported `costAmount` exceeds it, billing is
detached from the project and everything stops. Re-attach manually in the console
to recover. DRY_RUN=true logs the decision without detaching.
"""

import base64
import json
import os

import functions_framework
from googleapiclient import discovery

PROJECT_ID = os.environ.get("PROJECT_ID", "keplaria")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"


@functions_framework.cloud_event
def kill_billing(cloud_event):
    payload = json.loads(
        base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8")
    )
    cost = float(payload.get("costAmount", 0))
    cap = float(payload.get("budgetAmount", 0))

    if cost <= cap:
        print(f"OK: cost {cost} <= cap {cap}")
        return

    if DRY_RUN:
        print(f"DRY_RUN: would detach billing — cost {cost} > cap {cap}")
        return

    billing = discovery.build("cloudbilling", "v1", cache_discovery=False)
    name = f"projects/{PROJECT_ID}"
    info = billing.projects().getBillingInfo(name=name).execute()
    if not info.get("billingEnabled"):
        print("Billing already disabled; nothing to do")
        return

    billing.projects().updateBillingInfo(
        name=name, body={"billingAccountName": ""}
    ).execute()
    print(f"BILLING DETACHED from {PROJECT_ID}: cost {cost} > cap {cap}")
