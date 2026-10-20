import os, tempfile
from pathlib import Path
from uuid import uuid4
import csv, io
import pytest

temp = Path(tempfile.gettempdir()) / f"contractflow-test-{uuid4().hex}.db"
os.environ["DATABASE_URL"] = "sqlite:///" + str(temp)
os.environ["JWT_SECRET"] = "test-only-secret-that-is-longer-than-32-characters"
os.environ["DEMO_PASSWORD"] = "test-password"
from app.db import Base, engine, Session, Audit
from app.seed import seed
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import select, func


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    seed()
    with TestClient(app) as c:
        yield c
    engine.dispose()
    temp.unlink(missing_ok=True)


def headers(c, username):
    r = c.post("/auth/login", json={"username": username, "password": "test-password"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_auth_and_scope(client):
    assert client.get("/contracts").status_code in (401, 403)
    assert (
        client.post(
            "/auth/login", json={"username": "admin", "password": "wrong"}
        ).status_code
        == 401
    )
    h = headers(client, "buyer")
    cs = client.get("/contracts", headers=h).json()
    assert cs and all(c["buyer_id"] == "B009" for c in cs)
    assert client.get("/milestones/MS0001", headers=h).status_code == 200
    assert client.get("/milestones/MS0017", headers=h).status_code == 403


def test_auditor_is_read_only(client):
    h = headers(client, "auditor")
    assert client.post("/evaluate-all", headers=h).status_code == 403
    assert client.post("/milestones/MS0001/evaluate", headers=h).status_code == 403
    assert (
        client.post(
            "/milestones/MS0001/events",
            headers=h,
            json={
                "event_type": "APPROVAL",
                "document_id": "a",
                "idempotency_key": "abcdefgh",
            },
        ).status_code
        == 403
    )


def test_evaluation_idempotency(client):
    h = headers(client, "admin")
    a = client.post("/milestones/MS0003/evaluate", headers=h).json()
    b = client.post("/milestones/MS0003/evaluate", headers=h).json()
    assert a["audit_trace_id"] == b["audit_trace_id"]
    with Session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.milestone_id == "MS0003")
            )
            == 1
        )


def test_event_auth_retry_and_conflict(client):
    h = headers(client, "supplier")
    bad = {
        "event_type": "ACCEPTANCE",
        "document_id": "DOC-TEST",
        "idempotency_key": "test-key-123",
    }
    assert (
        client.post("/milestones/MS0002/events", headers=h, json=bad).status_code == 403
    )
    good = dict(bad, event_type="DELIVERY")
    a = client.post("/milestones/MS0002/events", headers=h, json=good)
    b = client.post("/milestones/MS0002/events", headers=h, json=good)
    assert a.status_code == b.status_code == 201
    assert a.json()["event_id"] == b.json()["event_id"]
    assert (
        client.post(
            "/milestones/MS0002/events", headers=h, json=dict(good, document_id="OTHER")
        ).status_code
        == 409
    )


def test_export_preserves_template(client):
    h = headers(client, "admin")
    response = client.get("/export/submission.csv", headers=h)
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    from app.db import ROOT

    with (
        ROOT / "data" / "04_output_templates" / "submission_output_template.csv"
    ).open(encoding="utf-8-sig", newline="") as f:
        template = list(csv.DictReader(f))
    assert len(rows) == 405
    assert [(r["contract_id"], r["milestone_id"]) for r in rows] == [
        (r["contract_id"], r["milestone_id"]) for r in template
    ]
    assert all(r["audit_trace_id"] and r["predicted_decision"] for r in rows)
