import csv, hashlib, io, json, os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
import jwt
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .db import Session, User, Record, Audit, records
from .engine import evaluate, authorized

SECRET = os.getenv("JWT_SECRET", "")
if len(SECRET) < 32:
    raise RuntimeError(
        "JWT_SECRET must contain at least 32 characters. See .env.example"
    )

app = FastAPI(
    title="ContractFlow API",
    version="1.0.0",
    description="Synthetic contract workflow simulation; no payment execution.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:5174"
    ).split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


def database():
    with Session() as db:
        yield db


DB = Annotated[object, Depends(database)]
bearer = HTTPBearer()


def current(db: DB, token: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]):
    try:
        payload = jwt.decode(
            token.credentials,
            SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
        u = db.get(User, payload["sub"])
        if not u:
            raise ValueError()
        return u
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(401, "Phiên đăng nhập không hợp lệ")


Actor = Annotated[User, Depends(current)]


def require(u, *roles):
    if u.role not in roles:
        raise HTTPException(403, "Bạn không có quyền thực hiện thao tác này")


def accessible(u, c, parties):
    p = parties.get(u.party_id, {})
    return (
        u.role in ("ADMIN", "AUDITOR")
        or u.party_id in (c["buyer_id"], c["supplier_id"])
        or (u.role == "APPROVER" and p.get("parent_party_id") == c["buyer_id"])
    )


def context(db, u, mid):
    m = db.get(Record, ("milestones", mid))
    if not m:
        raise HTTPException(404, "Không tìm thấy milestone")
    c = db.get(Record, ("contracts", m.data["contract_id"]))
    parties = {p["party_id"]: p for p in records(db, "parties")}
    if not c or not accessible(u, c.data, parties):
        raise HTTPException(403, "Hợp đồng ngoài phạm vi truy cập")
    return c.data, m.data, parties


def run(db, u, mid, persist=False):
    c, m, parties = context(db, u, mid)
    events = [e for e in records(db, "events") if e["contract_id"] == c["contract_id"]]
    rules = records(db, "rules")
    result = evaluate(c, m, events, parties, rules)
    if persist:
        # Input fingerprint + unique DB constraint makes retries/concurrent runs idempotent.
        fingerprint = hashlib.sha256(
            json.dumps(
                [c, m, events, parties, rules, result["engine_name"]], sort_keys=True
            ).encode()
        ).hexdigest()
        old = db.scalar(select(Audit).where(Audit.fingerprint == fingerprint))
        if old:
            return old.data
        db.add(
            Audit(
                id=result["audit_trace_id"],
                milestone_id=mid,
                actor=u.username,
                fingerprint=fingerprint,
                data=result,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return db.scalar(select(Audit).where(Audit.fingerprint == fingerprint)).data
    return result


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


@app.post("/auth/login")
def login(body: Login, db: DB):
    u = db.get(User, body.username)
    if not u or not PasswordHash.recommended().verify(body.password, u.password_hash):
        raise HTTPException(401, "Sai tài khoản hoặc mật khẩu")
    return {
        "access_token": jwt.encode(
            {"sub": u.username, "exp": datetime.now(timezone.utc) + timedelta(hours=2)},
            SECRET,
            algorithm="HS256",
        ),
        "token_type": "bearer",
        "user": {"username": u.username, "role": u.role, "party_id": u.party_id},
    }


@app.get("/auth/me")
def me(u: Actor):
    return {"username": u.username, "role": u.role, "party_id": u.party_id}


@app.get("/health")
def health():
    return {"status": "ok", "simulation": True}


@app.get("/contracts")
def contracts(db: DB, u: Actor):
    parties = {p["party_id"]: p for p in records(db, "parties")}
    return [c for c in records(db, "contracts") if accessible(u, c, parties)]


@app.get("/milestones")
def milestones(db: DB, u: Actor, contract_id: str | None = None):
    allowed = {c["contract_id"] for c in contracts(db, u)}
    return [
        dict(m, decision=run(db, u, m["milestone_id"]))
        for m in records(db, "milestones")
        if m["contract_id"] in allowed
        and (not contract_id or m["contract_id"] == contract_id)
    ]


@app.get("/milestones/{mid}")
def detail(mid: str, db: DB, u: Actor):
    c, m, p = context(db, u, mid)
    events = [
        e
        for e in records(db, "events")
        if e["contract_id"] == c["contract_id"]
        and (e["milestone_id"] == mid or not e["milestone_id"])
    ]
    return {
        "contract": c,
        "milestone": m,
        "events": events,
        "decision": run(db, u, mid),
        "audit": [
            dict(a.data, actor=a.actor)
            for a in db.scalars(select(Audit).where(Audit.milestone_id == mid))
        ],
    }


@app.post("/milestones/{mid}/evaluate")
def decide(mid: str, db: DB, u: Actor):
    require(u, "ADMIN", "BUYER", "SUPPLIER", "APPROVER")
    return run(db, u, mid, True)


@app.post("/evaluate-all")
def evaluate_all(db: DB, u: Actor):
    require(u, "ADMIN")
    return {
        "evaluated": len(
            [run(db, u, m["milestone_id"], True) for m in records(db, "milestones")]
        )
    }


class EventInput(BaseModel):
    event_type: Literal[
        "CONTRACT_SIGNED",
        "DELIVERY",
        "INVOICE",
        "ACCEPTANCE",
        "APPROVAL",
        "REJECTION",
        "DISPUTE_OPENED",
        "DISPUTE_RESOLVED",
        "REQUIREMENTS_APPROVED",
        "UAT_PASSED",
        "GO_LIVE",
        "WARRANTY_COMPLETED",
        "KICKOFF_CONFIRMED",
        "SERVICE_ACTIVATED",
        "SERVICE_COMPLETED",
    ]
    document_id: str = Field(min_length=1, max_length=120)
    reference_event_id: str = Field(default="", max_length=100)
    event_note: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=100)


@app.post("/milestones/{mid}/events", status_code=201)
def add_event(mid: str, body: EventInput, db: DB, u: Actor):
    require(u, "BUYER", "SUPPLIER", "APPROVER")
    c, m, parties = context(db, u, mid)
    p = parties.get(u.party_id, {})
    event_id = (
        "EV-"
        + hashlib.sha256(f"{u.username}:{body.idempotency_key}".encode()).hexdigest()[
            :32
        ]
    )
    old = db.get(Record, ("events", event_id))
    if old:
        if old.data["milestone_id"] != mid or any(
            old.data.get(k) != getattr(body, k)
            for k in ("event_type", "document_id", "reference_event_id", "event_note")
        ):
            raise HTTPException(409, "Idempotency key đã dùng cho nội dung khác")
        return old.data
    e = dict(
        event_id=event_id,
        contract_id=c["contract_id"],
        milestone_id=mid,
        event_type=body.event_type,
        event_status={
            "APPROVAL": "APPROVED",
            "REJECTION": "REJECTED",
            "DISPUTE_OPENED": "OPEN",
            "DISPUTE_RESOLVED": "RESOLVED",
        }.get(body.event_type, "CONFIRMED"),
        actor_party_id=u.party_id,
        actor_party_type=p.get("party_type"),
        actor_role=p.get("approval_role")
        if p.get("party_type") == "APPROVER"
        else p.get("party_type"),
        event_timestamp=datetime.now(timezone.utc).isoformat(),
        document_id=body.document_id,
        reference_event_id=body.reference_event_id,
        event_note=body.event_note,
        event_source="PORTAL",
        data_is_synthetic="TRUE",
    )
    if not authorized(e, c, m, parties):
        raise HTTPException(403, "Actor không có quyền tạo loại sự kiện này")
    if body.event_type == "DISPUTE_RESOLVED":
        ref = db.get(Record, ("events", body.reference_event_id))
        if (
            not ref
            or ref.data["event_type"] != "DISPUTE_OPENED"
            or ref.data["contract_id"] != c["contract_id"]
            or ref.data["milestone_id"] not in ("", mid)
        ):
            raise HTTPException(422, "Tham chiếu dispute không hợp lệ")
    db.add(Record(kind="events", id=event_id, data=e))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return add_event(mid, body, db, u)
    db.info.clear()
    run(db, u, mid, True)
    return e


@app.get("/rules")
def rules(db: DB, u: Actor):
    return records(db, "rules")


@app.get("/parties")
def parties(db: DB, u: Actor):
    require(u, "ADMIN", "AUDITOR")
    return records(db, "parties")


@app.get("/audit")
def audit(db: DB, u: Actor):
    require(u, "ADMIN", "AUDITOR")
    return [
        dict(a.data, milestone_id=a.milestone_id, actor=a.actor)
        for a in db.scalars(select(Audit).order_by(Audit.id))
    ]


@app.get("/export/submission.csv")
def export(db: DB, u: Actor):
    require(u, "ADMIN", "AUDITOR")
    from pathlib import Path

    path = (
        Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
        / "04_output_templates"
        / "submission_output_template.csv"
    )
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        c, m, p = context(db, u, row["milestone_id"])
        if row["contract_id"] != c["contract_id"]:
            raise HTTPException(422, "Template contract/milestone mismatch")
        result = run(db, u, row["milestone_id"], True)
        row.update({k: v for k, v in result.items() if k in fields})
        writer.writerow(row)
    return Response(
        "\ufeff" + stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="submission_output.csv"'},
    )
