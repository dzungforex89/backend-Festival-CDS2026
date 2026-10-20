"""Explicit, idempotent demo import; never overwrites events or user changes."""

import csv, os
from pathlib import Path
from pwdlib import PasswordHash
from .db import Session, Record, User


def seed():
    root = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    with Session() as db:
        for kind, filename, key in [
            ("contracts", "contracts.csv", "contract_id"),
            ("milestones", "milestones.csv", "milestone_id"),
            ("events", "events.csv", "event_id"),
            ("parties", "parties.csv", "party_id"),
            ("rules", "payment_rules.csv", "rule_id"),
        ]:
            with (root / "01_data" / filename).open(
                encoding="utf-8-sig", newline=""
            ) as f:
                for row in csv.DictReader(f):
                    if not db.get(Record, (kind, row[key])):
                        db.add(Record(kind=kind, id=row[key], data=row))
        password = os.getenv("DEMO_PASSWORD")
        if not password:
            raise RuntimeError("Set DEMO_PASSWORD before seeding demo accounts")
        for name, role, party in [
            ("admin", "ADMIN", None),
            ("auditor", "AUDITOR", None),
            ("buyer", "BUYER", "B009"),
            ("supplier", "SUPPLIER", "S021"),
            ("approver", "APPROVER", "A025"),
        ]:
            if not db.get(User, name):
                db.add(
                    User(
                        username=name,
                        password_hash=PasswordHash.recommended().hash(password),
                        role=role,
                        party_id=party,
                    )
                )
        db.commit()


if __name__ == "__main__":
    seed()
