"""Versioned initial schema; JSONB for PostgreSQL, JSON for SQLite demo."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    payload = sa.JSON().with_variant(JSONB, "postgresql")
    op.create_table(
        "records",
        sa.Column("kind", sa.String(30), primary_key=True),
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("data", payload, nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("username", sa.String(100), primary_key=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("party_id", sa.String(50)),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("milestone_id", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("fingerprint", sa.String(64), unique=True, nullable=False),
        sa.Column("data", payload, nullable=False),
    )
    op.create_index("ix_audit_logs_milestone_id", "audit_logs", ["milestone_id"])


def downgrade():
    op.drop_table("audit_logs")
    op.drop_table("users")
    op.drop_table("records")
