"""The scoped executor identity, declared once and read by both scripts.

`provision.py` writes this into the ERP; `harness.py` reads the ERP back and
refuses anything that does not match. Keeping the declaration in one module is
what makes the harness a check rather than a restatement: widening the role in
the ERP turns the harness red, and widening it here without a reason to shows
up as a diff on a file whose only purpose is to be the contract.
"""

from __future__ import annotations

ROLE = "Keplaria Executor"
USER = "keplaria-executor@keplaria.example"

# Exactly the rights app/executor/frappe.py exercises, and nothing else.
# Supplier: create_supplier_if_absent (create), set/clear_supplier_hold (write),
# send_supplier_message (read, and `email` — Frappe's communication.email.make
# checks the `email` right on the REFERENCE document before it will queue mail
# about that document, and that method is the only whitelisted path to the
# mail queue; granted 2026-08-27, when the send was found to have been filing
# correspondence and mailing nothing). Supplier Group: leaf_supplier_group
# (read). Communication: send_supplier_message (create). File: attach_evidence
# (read for the idempotency lookup, create for upload_file).
GRANTS: dict[str, set[str]] = {
    "Supplier": {"read", "write", "create", "email"},
    "Supplier Group": {"read"},
    "Communication": {"read", "create"},
    "File": {"read", "create"},
}

# Every permission flag Frappe stores on a Custom DocPerm row. `provision.py`
# writes the row flag by flag over THIS list, so a right the contract does not
# name is turned off whether Frappe seeded it or a person set it.
PERM_FLAGS = [
    "read", "write", "create", "delete", "submit", "cancel", "amend",
    "report", "export", "import", "share", "print", "email",
    "set_user_permissions", "select", "impersonate", "mask",
]

# The harness deliberately does NOT read the list above. It derives what the
# role holds from the live row's own 0/1 columns, minus these, so a right a
# future Frappe version adds is measured the day it appears rather than
# staying invisible until someone remembers to extend a hardcoded list.
# `select`, `impersonate` and `mask` are that exact case: three v16 columns
# this list did not carry until 2026-08-27, while the file claimed a new
# right would fail by default. All three read 0. `if_owner` is a modifier
# rather than a right, so it is excluded from the set and asserted to be off
# separately: a row that granted its rights only over records the executor
# created would describe a different identity than the one claimed.
NON_PERMISSION_FIELDS = {"docstatus", "idx", "permlevel", "if_owner"}

# Rights the executor must NOT hold on anything it can write. Asserted
# directly against the live API, not inferred from the permission rows.
FORBIDDEN_METHODS = ["delete"]

# Doctypes whose reachability would make "scoped" a false description. The
# executor is a supplier-record writer; nothing here is a supplier record.
HIGH_VALUE_DOCTYPES = [
    "Company", "Customer", "Sales Invoice", "Purchase Invoice",
    "Purchase Order", "Payment Entry", "Journal Entry", "Bank Account",
    "Employee", "Email Account", "Supplier Quotation",
]

# Doctypes the role does not grant but Frappe's own desk baseline leaves
# readable to any System User. Recorded rather than hidden: the public claim
# says the executor is scoped to supplier records, and that claim has to
# survive someone running this check. `User` returns names only, and the site
# has three. If this set ever GROWS the harness fails, because a widened role
# is exactly what it would look like.
INHERITED_READS = ["Contact", "Address", "Item", "User"]

# Rights Frappe's stock configuration grants the implicit `All` role — which
# every authenticated user carries, so a custom role cannot scope them away.
# Measured on 2026-08-23: `File` gave every user delete with no owner
# restriction (any user could delete any attachment on the site, including
# another supplier's certificate), and `Communication` gave every user delete
# on rows they created. Both are revoked here.
#
# Narrow by construction: the only users on this site are the owner, who
# holds System Manager and keeps delete through that role, and two
# deliberately unprivileged bots. Reversible by setting the flag back.
BASELINE_REVOCATIONS: list[tuple[str, str, str]] = [
    ("File", "All", "delete"),
    ("Communication", "All", "delete"),
]
