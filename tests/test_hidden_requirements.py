"""Independent regression cases for hidden-data robustness.

The cases are intentionally generated from the supplied data pack without
using public labels as inference inputs.
"""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.engine import evaluate
from .test_engine import C, E, M, P, R, check


WORKFLOW_PAIRS = (
    ("GOODS_SUPPLY", "WF_GOODS_4STAGE"),
    ("SERVICES", "WF_SERVICE_4STAGE"),
    ("MAINTENANCE", "WF_MAINTENANCE_3STAGE"),
    ("SOFTWARE_IMPLEMENTATION", "WF_SOFTWARE_5STAGE"),
)


def contract_milestone_for_pair(contract_type, workflow_template):
    for contract in C.values():
        if (
            contract["contract_type"] == contract_type
            and contract["workflow_template"] == workflow_template
        ):
            for milestone in M.values():
                if milestone["contract_id"] == contract["contract_id"]:
                    return deepcopy(contract), deepcopy(milestone)
    raise AssertionError(f"No fixture for {contract_type}/{workflow_template}")


def result(contract, milestone, events=None):
    return evaluate(
        contract,
        milestone,
        E if events is None else events,
        P,
        R,
    )


@pytest.mark.parametrize("contract_type,workflow_template", WORKFLOW_PAIRS)
def test_valid_workflow_pairs_have_a_decision(contract_type, workflow_template):
    contract, milestone = contract_milestone_for_pair(
        contract_type, workflow_template
    )
    decision = result(contract, milestone)["predicted_decision"]
    assert decision in {"RELEASE", "HOLD", "REJECT", "PENALTY"}


def test_contract_milestone_mismatch_fails_safe():
    contract = deepcopy(C["CT0001"])
    milestone = deepcopy(M["MS0005"])
    outcome = result(contract, milestone)
    assert outcome["predicted_decision"] == "HOLD"
    assert outcome["payable_amount_vnd_mn"] == "0.00"


@pytest.mark.parametrize(
    "offset",
    [
        timedelta(seconds=-1),
        timedelta(days=1),
    ],
    ids=["before-contract-signing", "after-contract-end"],
)
def test_required_event_outside_contract_dates_holds(offset):
    contract = deepcopy(C["CT0001"])
    milestone = deepcopy(M["MS0003"])
    events = deepcopy(E)
    event = next(e for e in events if e["milestone_id"] == "MS0003")
    start = datetime.fromisoformat(contract["signing_date"])
    end = datetime.fromisoformat(contract["end_date"])
    event["event_timestamp"] = (
        (start + offset if offset.total_seconds() < 0 else end + offset)
        .isoformat()
    )

    outcome = result(contract, milestone, events)

    assert outcome["predicted_decision"] == "HOLD"
    assert event["event_id"] in outcome["diagnostics"]["invalid_timing_event_ids"]


def test_required_events_in_wrong_order_hold():
    contract = deepcopy(C["CT0001"])
    milestone = deepcopy(M["MS0002"])
    events = deepcopy(E)
    delivery = next(e for e in events if e["milestone_id"] == "MS0002" and e["event_type"] == "DELIVERY")
    invoice = next(e for e in events if e["milestone_id"] == "MS0002" and e["event_type"] == "INVOICE")
    delivery["event_timestamp"], invoice["event_timestamp"] = (
        invoice["event_timestamp"],
        delivery["event_timestamp"],
    )

    outcome = result(contract, milestone, events)

    assert outcome["predicted_decision"] == "HOLD"
    assert outcome["diagnostics"]["invalid_sequence"] is True


@pytest.mark.parametrize(
    "condition",
    [
        "",
        "EVENT:DELIVERY AND UNKNOWN_TERM",
        "EVENT:DELIVERY  AND NO_OPEN_DISPUTE",
    ],
)
def test_invalid_condition_configuration_holds(condition):
    milestone = deepcopy(M["MS0002"])
    milestone["required_condition"] = condition
    assert check("MS0002", m=milestone)["predicted_decision"] == "HOLD"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda e: e.update(event_status="DRAFT"),
        lambda e: e.update(actor_party_id="B001"),
        lambda e: e.update(event_timestamp="not-a-datetime"),
    ],
    ids=["invalid-status", "wrong-actor", "malformed-timestamp"],
)
def test_malformed_or_unauthorized_required_event_holds(mutator):
    events = deepcopy(E)
    event = next(e for e in events if e["milestone_id"] == "MS0003")
    mutator(event)
    assert check("MS0003", events)["predicted_decision"] == "HOLD"


@pytest.mark.parametrize(
    "grace_days,completion_delta,expected",
    [
        ("0", 0, "RELEASE"),
        ("2", 0, "RELEASE"),
        ("2", 1, "PENALTY"),
    ],
    ids=["on-deadline", "at-grace-boundary", "one-day-after-grace"],
)
def test_penalty_boundaries(grace_days, completion_delta, expected):
    milestone = deepcopy(M["MS0003"])
    milestone["grace_period_days"] = grace_days
    milestone["due_date"] = "2026-06-25"
    event = next(e for e in deepcopy(E) if e["milestone_id"] == "MS0003")
    event["event_timestamp"] = (
        datetime.fromisoformat(milestone["due_date"])
        + timedelta(days=int(grace_days) + completion_delta)
    ).isoformat()
    events = [event if e["event_id"] == event["event_id"] else e for e in E]
    outcome = check("MS0003", events, m=milestone)
    assert outcome["predicted_decision"] == expected
    if expected == "PENALTY":
        assert Decimal(outcome["penalty_amount_vnd_mn"]) > 0


def test_duplicate_at_five_minutes_is_not_new_evidence():
    events = deepcopy(E)
    original = next(e for e in events if e["milestone_id"] == "MS0003")
    duplicate = deepcopy(original)
    duplicate["event_id"] = "EDGE-DUPLICATE"
    duplicate["event_timestamp"] = (
        datetime.fromisoformat(original["event_timestamp"]) + timedelta(minutes=5)
    ).isoformat()
    events.append(duplicate)
    outcome = check("MS0003", events)
    assert outcome["predicted_decision"] == "RELEASE"
    assert "EDGE-DUPLICATE" in outcome["diagnostics"]["duplicate_event_ids"]


def test_duplicate_after_five_minutes_does_not_satisfy_missing_event_twice():
    events = deepcopy(E)
    original = next(e for e in events if e["milestone_id"] == "MS0003")
    duplicate = deepcopy(original)
    duplicate["event_id"] = "EDGE-NON-DUPLICATE"
    duplicate["event_timestamp"] = (
        datetime.fromisoformat(original["event_timestamp"]) + timedelta(minutes=5, seconds=1)
    ).isoformat()
    events.append(duplicate)
    outcome = check("MS0003", events)
    assert outcome["predicted_decision"] == "RELEASE"
    assert "EDGE-NON-DUPLICATE" not in outcome["diagnostics"]["duplicate_event_ids"]


@pytest.mark.parametrize(
    "reason",
    [
        "OPEN_DISPUTE",
        "REJECTION_PRESENT",
        "UNAUTHORIZED_EVENT",
        "MISSING_REQUIRED_EVENT",
        "APPROVAL_QUORUM_NOT_MET",
        "LATE_COMPLETION",
        "ALL_CONDITIONS_MET",
    ],
)
def test_first_match_decision_categories_are_safe(reason):
    outcomes = [check(mid) for mid in M]
    matching = [o for o in outcomes if o["reason_code"] == reason]
    if matching:
        for outcome in matching:
            if outcome["predicted_decision"] in {"HOLD", "REJECT"}:
                assert outcome["payable_amount_vnd_mn"] == "0.00"


def test_every_output_decision_has_trace_and_nonnegative_processing_time():
    for milestone_id in M:
        outcome = check(milestone_id)
        assert outcome["predicted_decision"] in {"RELEASE", "HOLD", "REJECT", "PENALTY"}
        assert outcome["audit_trace_id"]
        assert outcome["processing_time_ms"] >= 0
        assert 1 <= len(outcome["trace"]) <= 8
        assert outcome["trace"][-1]["matched"] is True
