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
# send_supplier_message (read). Supplier Group: leaf_supplier_group (read).
# Communication: send_supplier_message (create). File: attach_evidence
# (read for the idempotency lookup, create for upload_file).
GRANTS: dict[str, set[str]] = {
    "Supplier": {"read", "write", "create"},
    "Supplier Group": {"read"},
    "Communication": {"read", "create"},
    "File": {"read", "create"},
}

# Every permission flag Frappe stores on a Custom DocPerm row. The harness
# checks the granted set against GRANTS over THIS list rather than over the
# flags that happen to be set, so a right added in a future Frappe version is
# a failure by default instead of an unnoticed grant.
PERM_FLAGS = [
    "read", "write", "create", "delete", "submit", "cancel", "amend",
    "report", "export", "import", "share", "print", "email",
    "set_user_permissions",
]

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
