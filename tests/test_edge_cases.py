from copy import deepcopy
from .test_engine import check, M, E, R


def test_unknown_expression_and_condition_hold():
    rules = deepcopy(R)
    for r in rules:
        if r["release_rule_code"] == M["MS0003"]["release_rule_code"]:
            r["condition_expression"] = "UNKNOWN"
    assert check("MS0003", rules=rules)["reason_code"] == "UNRESOLVED_CONDITION"
    m = deepcopy(M["MS0003"])
    m["required_condition"] += " AND MANUAL_REVIEW"
    assert check("MS0003", m=m)["predicted_decision"] == "HOLD"


def test_open_dispute_precedes_rejection():
    events = deepcopy(E)
    e = deepcopy(
        next(
            e
            for e in E
            if e["milestone_id"] == "MS0007" and e["event_type"] == "ACCEPTANCE"
        )
    )
    e.update(event_id="test-open", event_type="DISPUTE_OPENED", event_status="OPEN")
    events.append(e)
    assert check("MS0007", events)["reason_code"] == "OPEN_DISPUTE"


def test_invalid_resolver_cannot_unblock():
    events = deepcopy(E)
    opening = deepcopy(next(e for e in events if e["milestone_id"] == "MS0003"))
    opening.update(
        event_id="open-test", event_type="DISPUTE_OPENED", event_status="OPEN"
    )
    resolved = dict(
        opening,
        event_id="resolve-test",
        event_type="DISPUTE_RESOLVED",
        event_status="RESOLVED",
        reference_event_id="open-test",
        actor_party_id="B001",
    )
    events.extend([opening, resolved])
    assert check("MS0003", events)["reason_code"] == "OPEN_DISPUTE"


def test_duplicate_approver_does_not_satisfy_quorum():
    events = deepcopy(E)
    e = deepcopy(
        next(
            e
            for e in E
            if e["milestone_id"] == "MS0048" and e["event_type"] == "APPROVAL"
        )
    )
    e.update(event_id="test-approval", document_id="another-doc")
    events.append(e)
    assert check("MS0048", events)["reason_code"] == "APPROVAL_QUORUM_NOT_MET"


def test_invalid_status_is_not_evidence():
    events = deepcopy(E)
    for e in events:
        if e["milestone_id"] == "MS0003":
            e["event_status"] = "DRAFT"
    assert check("MS0003", events)["reason_code"] == "MISSING_REQUIRED_EVENT"


def test_penalty_cap_and_grace_boundary():
    m = deepcopy(M["MS0003"])
    m["due_date"] = "2026-06-25"
    m["grace_period_days"] = "2"
    assert check("MS0003", m=m)["predicted_decision"] == "RELEASE"
    m["due_date"] = "2025-01-01"
    m["penalty_rate_pct_per_day"] = "10"
    m["max_penalty_pct"] = "5"
    r = check("MS0003", m=m)
    assert (
        r["predicted_decision"] == "PENALTY" and r["penalty_amount_vnd_mn"] == "19.35"
    )
