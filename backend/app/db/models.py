from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EmailRecord(Base):
    """One row per Gmail message. The email body is intentionally never stored."""

    __tablename__ = "email_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROCESSING', 'COMPLETED', 'FAILED')", name="ck_email_records_status"
        ),
        CheckConstraint(
            "notification_status IN ('NOT_REQUIRED', 'PENDING', 'SENT', 'FAILED')",
            name="ck_email_records_notification_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    category: Mapped[str | None] = mapped_column(String(32))
    priority: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    decision_rule: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    processing_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))

    notification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="NOT_REQUIRED"
    )
    notification_error: Mapped[str | None] = mapped_column(String(500))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
