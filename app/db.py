import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, String, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()
ROOT = Path(__file__).resolve().parents[1]
engine = create_engine(
    os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'contractflow.db'}"),
    **(
        {"connect_args": {"check_same_thread": False}}
        if os.getenv("DATABASE_URL", "sqlite").startswith("sqlite")
        else {}
    ),
)
Session = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))


class User(Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(100), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    party_id: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Audit(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    milestone_id: Mapped[str] = mapped_column(String(50), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    data: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"))


def records(db, kind):
    from sqlalchemy import select

    cache = db.info.setdefault("records", {})
    if kind not in cache:
        cache[kind] = [
            r.data
            for r in db.scalars(
                select(Record).where(Record.kind == kind).order_by(Record.id)
            )
        ]
    return cache[kind]
