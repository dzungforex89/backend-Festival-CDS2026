"""Deterministic first-match engine. No eval, no public-label dependency."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from time import perf_counter
from uuid import uuid4

ENGINE = "ContractFlow/1.0"
EXPRESSIONS = {
    "OPEN_DISPUTE": "open_dispute == TRUE",
    "REJECTION_PRESENT": "valid_rejection_event_count > 0",
    "UNAUTHORIZED_EVENT": "unauthorized_required_event_count > 0",
    "MISSING_REQUIRED_EVENT": "required_event_completion_rate < 1.0",
    "APPROVAL_QUORUM_NOT_MET": "approval_required == TRUE AND valid_approval_count < required_approval_quorum",
    "LATE_COMPLETION": "late_penalty_applicable == TRUE AND actual_completion_date > due_date + grace_period_days",
    "ALL_CONDITIONS_MET": "all_required_conditions_met == TRUE AND open_dispute == FALSE AND valid_rejection_event_count == 0",
    "UNRESOLVED_CONDITION": "ELSE",
}


def yes(v):
    return str(v).upper() == "TRUE"


def parts(v):
    return [x for x in (v or "").split(";") if x]


def stamp(v):
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return d.replace(tzinfo=timezone(timedelta(hours=7))) if d.tzinfo is None else d


def money(v):
    return Decimal(str(v)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def event_within_contract(e, c):
    event_date = stamp(e["event_timestamp"]).date()
    return datetime.fromisoformat(c["signing_date"]).date() <= event_date <= datetime.fromisoformat(
        c["end_date"]
    ).date()


def required_sequence_is_valid(evidence, required):
    positions = {
        event_type: stamp(next(e for e in evidence if e["event_type"] == event_type)["event_timestamp"])
        for event_type in required
    }
    return all(
        positions[left] <= positions[right]
        for left, right in zip(required, required[1:])
    )


def authorized(e, c, m, parties):
    p = parties.get(e.get("actor_party_id"), {})
    t = e.get("event_type")
    if p.get("status") != "ACTIVE" or p.get("party_type") != e.get("actor_party_type"):
        return False
    role = (
        p.get("approval_role")
        if p.get("party_type") == "APPROVER"
        else p.get("party_type")
    )
    if e.get("actor_role") != role:
        return False
    allowed = parts(p.get("allowed_event_types"))
    # Pack permissions use generic GOODS vocabulary across all workflow types.
    permission = {
        "REQUIREMENTS_APPROVED": "ACCEPTANCE",
        "UAT_PASSED": "ACCEPTANCE",
        "KICKOFF_CONFIRMED": "CONTRACT_SIGNED",
        "GO_LIVE": "DELIVERY",
        "SERVICE_ACTIVATED": "DELIVERY",
        "SERVICE_COMPLETED": "DELIVERY",
        "DISPUTE_OPENED": "DISPUTE",
        "DISPUTE_RESOLVED": "DISPUTE",
    }.get(t, t)
    if t not in allowed and permission not in allowed:
        return False
    if t in ("APPROVAL", "REJECTION"):
        return (
            p.get("party_type") == "APPROVER"
            and p.get("parent_party_id") == c["buyer_id"]
            and yes(p.get("can_approve_payment"))
            and (t == "REJECTION" or role in parts(m.get("required_approval_roles")))
            and Decimal(p.get("approval_limit_vnd_mn") or "0")
            >= Decimal(m["planned_payment_vnd_mn"])
        )
    if t in ("DISPUTE_OPENED", "DISPUTE_RESOLVED"):
        return yes(p.get("can_raise_dispute")) and (
            p.get("party_id") in (c["buyer_id"], c["supplier_id"])
            or p.get("parent_party_id") == c["buyer_id"]
        )
    expected = (
        "BUYER"
        if t
        in (
            "CONTRACT_SIGNED",
            "ACCEPTANCE",
            "REQUIREMENTS_APPROVED",
            "UAT_PASSED",
            "KICKOFF_CONFIRMED",
        )
        else "SUPPLIER"
    )
    return (
        p.get("party_type") == expected
        and p.get("party_id") == c["buyer_id" if expected == "BUYER" else "supplier_id"]
    )


def evaluate(c, m, events, parties, rules, as_of=None):
    start = perf_counter()
    now = as_of or datetime.now(timezone.utc)
    result = dict(
        predicted_decision="HOLD",
        predicted_milestone_status="ON_HOLD",
        conditions_met="",
        missing_conditions="",
        payable_amount_vnd_mn="0.00",
        penalty_amount_vnd_mn="0.00",
        reason_code="UNRESOLVED_CONDITION",
        decision_reason="Dữ liệu hoặc rule chưa rõ; cần kiểm tra thủ công.",
        evidence_event_ids="",
        rule_id_applied="",
        confidence_score=0.5,
        evaluated_at=now.isoformat(),
        engine_name=ENGINE,
        audit_trace_id=str(uuid4()),
        notes="Mô phỏng; không thực hiện thanh toán.",
    )
    trace = []
    try:
        if m.get("contract_id") != c.get("contract_id"):
            raise ValueError("Milestone does not belong to contract")
        required = parts(m["required_event_types"])
        planned = money(m["planned_payment_vnd_mn"])
        if not required or m.get("condition_logic") != "ALL" or planned < 0:
            raise ValueError("Invalid milestone configuration")
        supported_terms = {"EVENT:" + t for t in required} | {"NO_OPEN_DISPUTE"}
        if yes(m["approval_required"]):
            supported_terms.add("APPROVAL_QUORUM:" + m["required_approval_quorum"])
        if set(m.get("required_condition", "").split(" AND ")) != supported_terms:
            raise ValueError("Unknown or conflicting required_condition")
        relevant = [
            e
            for e in events
            if e["contract_id"] == c["contract_id"]
            and (
                e.get("milestone_id") == m["milestone_id"] or not e.get("milestone_id")
            )
            and (as_of is None or stamp(e["event_timestamp"]) <= as_of)
        ]
        relevant.sort(key=lambda e: (stamp(e["event_timestamp"]), e["event_id"]))
        unique, duplicate, aliases = [], [], {}
        for e in relevant:
            match = next(
                (
                    a
                    for a in unique
                    if e.get("document_id")
                    and all(
                        a.get(k) == e.get(k)
                        for k in (
                            "event_type",
                            "document_id",
                            "actor_party_id",
                            "event_status",
                            "reference_event_id",
                        )
                    )
                    and abs(
                        (
                            stamp(e["event_timestamp"]) - stamp(a["event_timestamp"])
                        ).total_seconds()
                    )
                    <= 300
                ),
                None,
            )
            (duplicate if match else unique).append(e)
            if match:
                aliases[e["event_id"]] = match["event_id"]
        invalid_timing = [
            e
            for e in unique
            if e["event_type"] in required and not event_within_contract(e, c)
        ]
        valid = [
            e
            for e in unique
            if e not in invalid_timing
            and authorized(e, c, m, parties)
            and e["event_status"]
            == {
                "APPROVAL": "APPROVED",
                "REJECTION": "REJECTED",
                "DISPUTE_OPENED": "OPEN",
                "DISPUTE_RESOLVED": "RESOLVED",
            }.get(e["event_type"], "CONFIRMED")
        ]
        opened = [e for e in valid if e["event_type"] == "DISPUTE_OPENED"]
        unresolved = [
            e
            for e in opened
            if not any(
                r["event_type"] == "DISPUTE_RESOLVED"
                and aliases.get(
                    r.get("reference_event_id"), r.get("reference_event_id")
                )
                == e["event_id"]
                and stamp(r["event_timestamp"]) >= stamp(e["event_timestamp"])
                for r in valid
            )
        ]
        rejected = [e for e in valid if e["event_type"] == "REJECTION"]
        unauthorized = [
            e
            for e in relevant
            if e["event_type"] in required
            and (
                not authorized(e, c, m, parties)
                or e in invalid_timing
            )
        ]
        evidence = [
            next(e for e in valid if e["event_type"] == t)
            for t in required
            if any(e["event_type"] == t for e in valid)
        ]
        missing = [
            t for t in required if not any(e["event_type"] == t for e in evidence)
        ]
        invalid_sequence = len(evidence) == len(required) and not required_sequence_is_valid(
            evidence, required
        )
        if invalid_sequence:
            missing = list(required)
            evidence = []
        approvals = {}
        for e in valid:
            if e["event_type"] == "APPROVAL":
                approvals.setdefault(e["actor_party_id"], e)
        quorum = int(m["required_approval_quorum"])
        roles = {e["actor_role"] for e in approvals.values()}
        approval_ok = not yes(m["approval_required"]) or (
            len(approvals) >= quorum
            and set(parts(m["required_approval_roles"])).issubset(roles)
        )
        if yes(m["approval_required"]) and quorum < 1:
            raise ValueError("Invalid approval quorum")
        completion = max(
            (
                stamp(e["event_timestamp"]).date()
                for e in evidence
                + (list(approvals.values()) if yes(m["approval_required"]) else [])
            ),
            default=None,
        )
        deadline = datetime.fromisoformat(m["due_date"]).date() + timedelta(
            days=int(m["grace_period_days"])
        )
        late = max(0, (completion - deadline).days) if completion else 0
        all_ok = (
            not missing
            and approval_ok
            and not unauthorized
            and not unresolved
            and not rejected
        )
        checks = {
            "OPEN_DISPUTE": bool(unresolved),
            "REJECTION_PRESENT": bool(rejected),
            "UNAUTHORIZED_EVENT": bool(unauthorized),
            "MISSING_REQUIRED_EVENT": bool(missing),
            "APPROVAL_QUORUM_NOT_MET": not approval_ok,
            "LATE_COMPLETION": all_ok
            and yes(m["late_penalty_applicable"])
            and late > 0,
            "ALL_CONDITIONS_MET": all_ok,
            "UNRESOLVED_CONDITION": True,
        }
        active = sorted(
            [
                r
                for r in rules
                if r["release_rule_code"] == m["release_rule_code"]
                and r["rule_set_id"] == c["rule_set_id"]
                and yes(r["active"])
            ],
            key=lambda r: int(r["priority"]),
        )
        # A complete policy is required; a partial or unknown ruleset cannot release.
        expected = list(checks)
        if [r["reason_code"] for r in active] != expected:
            raise ValueError("Incomplete or unsupported ruleset")
        if any(
            r.get("condition_expression") != EXPRESSIONS[r["reason_code"]]
            or not yes(r.get("stop_processing"))
            for r in active
        ):
            raise ValueError("Unknown rule expression or processing policy")
        decision_by_reason = [
            "HOLD",
            "REJECT",
            "HOLD",
            "HOLD",
            "HOLD",
            "PENALTY",
            "RELEASE",
            "HOLD",
        ]
        if [r["decision"] for r in active] != decision_by_reason:
            raise ValueError("Unsupported rule decisions")
        for r in active:
            hit = checks[r["reason_code"]]
            trace.append(
                {
                    "rule_id": r["rule_id"],
                    "priority": int(r["priority"]),
                    "reason_code": r["reason_code"],
                    "matched": hit,
                }
            )
            if hit:
                decision = r["decision"]
                rate, cap = (
                    Decimal(m["penalty_rate_pct_per_day"]),
                    Decimal(m["max_penalty_pct"]),
                )
                if min(rate, cap) < 0 or cap > 100:
                    raise ValueError("Invalid penalty parameters")
                penalty = (
                    money(
                        min(Decimal(late) * rate * planned / 100, cap * planned / 100)
                    )
                    if decision == "PENALTY"
                    else money(0)
                )
                payable = (
                    max(money(0), planned - penalty)
                    if decision in ("RELEASE", "PENALTY")
                    else money(0)
                )
                result.update(
                    predicted_decision=decision,
                    predicted_milestone_status={
                        "RELEASE": "RELEASED",
                        "PENALTY": "RELEASED_WITH_PENALTY",
                        "REJECT": "REJECTED",
                        "HOLD": "ON_HOLD",
                    }[decision],
                    reason_code=r["reason_code"],
                    decision_reason=r["rule_description_vi"],
                    rule_id_applied=r["rule_id"],
                    penalty_amount_vnd_mn=str(penalty),
                    payable_amount_vnd_mn=str(payable),
                    confidence_score=1.0,
                )
                break
        blockers = (
            missing
            + ([] if approval_ok else ["APPROVAL_QUORUM"])
            + (["OPEN_DISPUTE"] if unresolved else [])
            + (["UNAUTHORIZED_EVENT"] if unauthorized else [])
            + (["REJECTION"] if rejected else [])
        )
        used = (
            evidence
            + list(approvals.values())
            + rejected
            + unresolved
            + [e for e in valid if e["event_type"] == "DISPUTE_RESOLVED"]
            + unauthorized
        )
        result.update(
            conditions_met=";".join(e["event_type"] for e in evidence),
            missing_conditions=";".join(blockers),
            evidence_event_ids=";".join(dict.fromkeys(e["event_id"] for e in used)),
            notes=f"Mô phỏng. mode={'AS_OF' if as_of else 'DATA_PACK_REPLAY'}; duplicate_count={len(duplicate)}; days_late_after_grace={late}",
            diagnostics={
                "duplicate_event_ids": [e["event_id"] for e in duplicate],
                "valid_approval_count": len(approvals),
                "days_late_after_grace": late,
                "invalid_timing_event_ids": [
                    e["event_id"] for e in invalid_timing
                ],
                "invalid_sequence": invalid_sequence,
            },
        )
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        result.update(
            predicted_decision="HOLD",
            predicted_milestone_status="ON_HOLD",
            payable_amount_vnd_mn="0.00",
            penalty_amount_vnd_mn="0.00",
            reason_code="UNRESOLVED_CONDITION",
            decision_reason=f"Cần kiểm tra dữ liệu: {exc}",
            confidence_score=0.0,
        )
    result["trace"] = trace
    result["processing_time_ms"] = round((perf_counter() - start) * 1000, 3)
    return result
