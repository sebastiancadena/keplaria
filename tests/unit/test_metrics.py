"""The judge run's scoreboard is computed from the ledger, never asserted.

Every metric here has to survive the question "what edit would make this
fail?", because a scoreboard that reports a plausible number from absent
state is worse than no scoreboard: it is a measured-looking claim with
nothing behind it. Two shapes in particular are pinned deliberately —
`execution_attempts` is absent (not 0) when a command never failed, and
the grounded extraction lands in `certificate`, never in `evidence`.
"""

from app.metrics import scoreboard


def _case(**overrides):
    case = {
        "case_id": "JR-B-000000",
        "case_version": 5,
        "phase": "done",
        "supplier": "Distribuidora Llanos Azules SAS",
        "policy": {"band": "clear", "score": 0.0, "factors_fired": []},
        "certificate": {
            "evidence_version": 2,
            "expiry_date": "2028-01-01",
            "extracted_at": "2027-01-20",
            "document_checksum": "807c25e0",
        },
        "lifecycle": {"state": "active", "cycle": 2},
        "approval": None,
    }
    case.update(overrides)
    return case


def _command(action, cycle=1, status="done", **overrides):
    command = {
        "action": action,
        "cycle": cycle,
        "status": status,
        "attempts": 1,
        "external_id": "Distribuidora Llanos Azules SAS",
        "payload": {"supplier_name": "Distribuidora Llanos Azules SAS"},
    }
    command.update(overrides)
    return command


LIFECYCLE_TIMELINE = [
    {"event_type": "new_supplier_packet", "effective_date": "2026-01-05"},
    {"event_type": "renewal_due", "effective_date": "2026-10-01"},
    {"event_type": "renewal_due", "effective_date": "2026-12-01"},
    {"event_type": "evidence_overdue", "effective_date": "2027-01-15"},
    {"event_type": "certificate_received", "effective_date": "2027-01-20"},
]


def test_simulated_business_time_spans_the_whole_timeline():
    """The lifecycle clock is simulated and must be reported as such — this
    is the span the events describe, not wall-clock time the run took."""
    board = scoreboard(cases=[], commands=[], timeline=LIFECYCLE_TIMELINE)

    assert board["simulated_business_days"] == 380


def test_enforced_hold_days_measure_the_window_the_supplier_was_restricted():
    """Replaces the counterfactual 'exposure avoided', which needed a
    baseline for when a human would have noticed. This is a thing the system
    did: overdue on 2027-01-15, released on 2027-01-20, five days in which
    the supplier could not be purchased from."""
    board = scoreboard(
        cases=[],
        commands=[_command("apply_hold"), _command("clear_hold", cycle=2)],
        timeline=LIFECYCLE_TIMELINE,
    )

    assert board["enforced_hold_days"] == 5


def test_no_enforced_hold_is_claimed_when_the_hold_never_executed():
    """The dates alone do not prove a restriction — the ERP write does. A
    claimed hold that stayed pending restricted nobody."""
    board = scoreboard(
        cases=[],
        commands=[_command("apply_hold", status="pending")],
        timeline=LIFECYCLE_TIMELINE,
    )

    assert board["enforced_hold_days"] is None


def test_completed_steps_are_reported_against_their_denominator():
    """A count without a denominator is not a measurement. A step the run
    attempted and failed must still appear in the total, or a broken run
    reports the same '5 of 5' a clean one does."""
    timeline = LIFECYCLE_TIMELINE[:4] + [
        {"event_type": "certificate_received", "effective_date": "2027-01-20", "ok": False}
    ]

    board = scoreboard(cases=[], commands=[], timeline=timeline)

    assert board["workflow_steps_completed"] == 4
    assert board["workflow_steps_total"] == 5


def test_manual_steps_eliminated_is_none_without_a_recorded_baseline():
    """No baseline, no claim. Returning 0 here would read as 'nothing was
    eliminated', and returning a guess would be the invented validation the
    strategy explicitly forbids."""
    board = scoreboard(cases=[], commands=[], timeline=LIFECYCLE_TIMELINE)

    assert board["manual_steps_eliminated"] is None


def test_manual_steps_eliminated_subtracts_the_interventions_the_system_still_needed():
    """The system did not eliminate the step it escalated to a human. A
    baseline of 14 manual steps against one policy-required intervention is
    13 eliminated, not 14."""
    parked = _case(approval={"decision": "approved", "actor": "reviewer@example.com"})

    board = scoreboard(
        cases=[parked],
        commands=[],
        timeline=LIFECYCLE_TIMELINE,
        baseline={"manual_steps": 14, "validation": "author-timed, not practitioner-reviewed"},
    )

    assert board["manual_steps_eliminated"] == 13
    assert board["baseline_validation"] == "author-timed, not practitioner-reviewed"


def test_a_parked_case_counts_as_one_policy_required_intervention():
    """A human decision the policy demanded, not every human action."""
    parked = _case(
        case_id="JR-A-000000",
        approval={
            "actor": "reviewer@example.com",
            "decision": "approved",
            "case_version": 1,
        },
        policy={"band": "review", "score": 0.25, "factors_fired": [
            {"id": "SUBTHRESHOLD_CANDIDATE", "weight": 0.25}
        ]},
    )

    board = scoreboard(cases=[parked, _case()], commands=[], timeline=[])

    assert board["policy_required_interventions"] == 1


def test_a_command_that_failed_once_then_succeeded_counts_as_retried():
    """`execution_attempts` is written only by record_failure, so a present
    value of 1 on a done command IS one failure followed by a success. The
    `> 1` reading of this field produced a false all-clear on day 6."""
    retried = _command("create_supplier", execution_attempts=1)

    board = scoreboard(cases=[], commands=[retried], timeline=[])

    assert board["commands_retried_then_succeeded"] == 1


def test_a_command_that_never_failed_is_not_counted_as_retried():
    """The absent field is the common case: record_failure never ran, so
    there is no `execution_attempts` key at all. Defaulting it to 0 and
    testing `>= 1` would be correct; defaulting to 1 or reading a missing
    key as a retry would report fabricated retries on a clean run."""
    clean = _command("create_supplier")
    assert "execution_attempts" not in clean

    board = scoreboard(cases=[], commands=[clean], timeline=[])

    assert board["commands_retried_then_succeeded"] == 0


def test_one_write_per_action_and_cycle_means_no_duplicate_write():
    """The point of the retry metric: bounded retry must not double-write."""
    retried = _command("create_supplier", execution_attempts=2)

    board = scoreboard(cases=[], commands=[retried], timeline=[])

    assert board["duplicate_writes_after_retry"] == 0


def test_two_done_writes_of_the_same_action_and_cycle_are_a_duplicate():
    """The metric must be ABLE to report a duplicate, or reporting zero
    proves nothing. Two done create_supplier commands for one supplier in
    one cycle is what a double-write would actually look like in the
    ledger — deterministic command ids are supposed to make it impossible,
    and this is the assertion that the impossibility is being measured
    rather than assumed."""
    first = _command("create_supplier", execution_attempts=1)
    second = _command("create_supplier", execution_attempts=1)

    board = scoreboard(cases=[], commands=[first, second], timeline=[])

    assert board["duplicate_writes_after_retry"] == 1


def test_a_supplier_create_counts_the_erp_fields_a_human_would_have_typed():
    """Counted at the ERP write boundary, from the action's declared field
    set — not from the source document. The certificate fixture carries four
    data values and the system persists one of them, so counting the
    document would report a number unrelated to what was actually entered."""
    created = _command(
        "create_supplier",
        payload={
            "supplier_name": "Distribuidora Llanos Azules SAS",
            "country": "Colombia",
            "email_id": "distribuidora-llanos-azules-sas@example.com",
        },
    )

    board = scoreboard(cases=[], commands=[created], timeline=[])

    # supplier_name, supplier_group, supplier_type, country, email_id
    assert board["fields_without_rekeying"] == 5


def test_a_supplier_create_without_an_address_counts_one_field_fewer():
    """email_id is omitted from the create payload entirely when falsy, so
    the count has to follow the payload rather than the action's maximum."""
    created = _command(
        "create_supplier",
        payload={"supplier_name": "Andes Verde Import Export SAS", "country": "Colombia"},
    )

    board = scoreboard(cases=[], commands=[created], timeline=[])

    assert board["fields_without_rekeying"] == 4


def test_a_command_that_never_executed_enters_no_fields():
    """A parked or refused command wrote nothing, so it rekeyed nothing."""
    parked = _command("create_supplier", status="pending")

    board = scoreboard(cases=[], commands=[parked], timeline=[])

    assert board["fields_without_rekeying"] == 0


def test_the_same_action_in_a_later_cycle_is_not_a_duplicate():
    """A renewal legitimately repeats every action in a new cycle — that is
    why command ids carry the cycle. Counting cycle 2's attach_evidence as a
    duplicate of cycle 1's would make every renewed supplier look broken."""
    board = scoreboard(
        cases=[],
        commands=[
            _command("attach_evidence", cycle=1, external_id="9161a08a96"),
            _command("attach_evidence", cycle=2, external_id="fc302b6f7d"),
        ],
        timeline=[],
    )

    assert board["duplicate_writes_after_retry"] == 0
