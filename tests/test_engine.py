import csv
from pathlib import Path
from datetime import datetime, timezone
from copy import deepcopy
from decimal import Decimal
import pytest
from app.engine import evaluate

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    with (ROOT / "data" / "01_data" / f"{name}.csv").open(
        encoding="utf-8-sig", newline=""
    ) as f:
        return list(csv.DictReader(f))


C = {r["contract_id"]: r for r in load("contracts")}
M = {r["milestone_id"]: r for r in load("milestones")}
P = {r["party_id"]: r for r in load("parties")}
E = load("events")
R = load("payment_rules")
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def check(mid, events=None, m=None, rules=None):
    m = m or M[mid]
    return evaluate(
        C[m["contract_id"]],
        m,
        E if events is None else events,
        P,
        R if rules is None else rules,
    )


with (ROOT / "tests" / "fixtures" / "decision_sample.csv").open(
    encoding="utf-8-sig", newline=""
) as f:
    SAMPLES = list(csv.DictReader(f))


@pytest.mark.parametrize("row", SAMPLES, ids=lambda r: r["scenario_id"])
def test_public_samples(row):
    result = check(row["milestone_id"])
    assert result["predicted_decision"] == row["expected_decision"], result
    assert result["reason_code"] == row["reason_code"]
    assert Decimal(result["payable_amount_vnd_mn"]) == Decimal(
        row["payable_amount_vnd_mn"]
    )
    assert Decimal(result["penalty_amount_vnd_mn"]) == Decimal(
        row["penalty_amount_vnd_mn"]
    )


def test_all_milestones_money_invariants():
    outcomes = set()
    for mid, m in M.items():
        r = check(mid)
        outcomes.add(r["predicted_decision"])
        payable = Decimal(r["payable_amount_vnd_mn"])
        penalty = Decimal(r["penalty_amount_vnd_mn"])
        planned = Decimal(m["planned_payment_vnd_mn"])
        assert 0 <= payable <= planned
        if r["predicted_decision"] in ("HOLD", "REJECT"):
            assert payable == 0 and penalty == 0
        else:
            assert payable + penalty == planned
    assert outcomes == {"RELEASE", "HOLD", "REJECT", "PENALTY"}


def test_missing_and_unknown_rules_hold():
    assert check("MS0003", rules=[])["predicted_decision"] == "HOLD"
    broken = deepcopy(M["MS0003"])
    broken["condition_logic"] = "ANY"
    assert check("MS0003", m=broken)["predicted_decision"] == "HOLD"


def test_wrong_organization_evidence_holds():
    events = deepcopy(E)
    target = next(e for e in events if e["milestone_id"] == "MS0003")
    target["actor_party_id"] = "B001"
    assert check("MS0003", events)["reason_code"] == "UNAUTHORIZED_EVENT"


def test_duplicate_does_not_change_money():
    events = deepcopy(E)
    e = deepcopy(next(e for e in E if e["milestone_id"] == "MS0003"))
    e["event_id"] = "duplicate"
    events.append(e)
    assert (
        check("MS0003", events)["payable_amount_vnd_mn"]
        == check("MS0003")["payable_amount_vnd_mn"]
    )
    assert "duplicate" in check("MS0003", events)["diagnostics"]["duplicate_event_ids"]


def test_future_events_excluded():
    events = deepcopy(E)
    for e in events:
        if e["milestone_id"] == "MS0003":
            e["event_timestamp"] = "2027-01-01T00:00:00"
    m = M["MS0003"]
    assert (
        evaluate(C[m["contract_id"]], m, events, P, R, NOW)["reason_code"]
        == "MISSING_REQUIRED_EVENT"
    )
