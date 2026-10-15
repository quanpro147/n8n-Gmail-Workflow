"""create email_records

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(998), nullable=False),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("sender_address", sa.String(320), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(32)),
        sa.Column("priority", sa.String(16)),
        sa.Column("summary", sa.Text()),
        sa.Column("action", sa.String(16)),
        sa.Column("reason", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("decision_rule", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(500)),
        sa.Column("notification_status", sa.String(16), nullable=False),
        sa.Column("notification_error", sa.String(500)),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", name="uq_email_records_message_id"),
        sa.CheckConstraint(
            "status IN ('PROCESSING', 'COMPLETED', 'FAILED')", name="ck_email_records_status"
        ),
        sa.CheckConstraint(
            "notification_status IN ('NOT_REQUIRED', 'PENDING', 'SENT', 'FAILED')",
            name="ck_email_records_notification_status",
        ),
    )
    op.create_index("ix_email_records_sender_address", "email_records", ["sender_address"])


def downgrade() -> None:
    op.drop_index("ix_email_records_sender_address", table_name="email_records")
    op.drop_table("email_records")
