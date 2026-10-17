from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import EmailRecord
from app.schemas.email import AgentAnalysis, EmailIn, NotificationStatus, ProcessingStatus
from app.services.notification_rules import Decision

ERROR_MESSAGE_MAX_CHARS = 500


class ClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    ALREADY_COMPLETED = "already_completed"
    IN_PROGRESS = "in_progress"
    MAX_ATTEMPTS = "max_attempts"


@dataclass(frozen=True)
class ClaimResult:
    outcome: ClaimOutcome
    record: EmailRecord

    @property
    def fencing_token(self) -> int:
        """Identifies this claim; writes from an older, superseded claim are rejected."""
        return self.record.attempt_count


@dataclass(frozen=True)
class WriteResult:
    """`applied` is False when another worker reclaimed the record (stale fencing token)."""

    record: EmailRecord
    applied: bool


@dataclass(frozen=True)
class HistoryEntry:
    """Detached snapshot of a past result; safe to use after the session ends its transaction."""

    category: str | None
    priority: str | None
    action: str | None
    received_at: datetime


@dataclass(frozen=True)
class EmailSearchFilters:
    """Optional filters for `EmailRepository.search`; `None` means "any"."""

    sender_address: str | None = None
    category: str | None = None
    priority: str | None = None
    action: str | None = None
    status: str | None = None
    received_after: datetime | None = None
    received_before: datetime | None = None


class NotificationUpdateError(Exception):
    """Raised when a notification result cannot be recorded for a record."""


class EmailRepository:
    """All persistence for email processing records. Each public method commits."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_message_id(self, message_id: str) -> EmailRecord | None:
        return self._session.scalar(select(EmailRecord).where(EmailRecord.message_id == message_id))

    def claim(
        self, email: EmailIn, *, max_attempts: int, stale_after: timedelta, now: datetime
    ) -> ClaimResult:
        """Atomically decide whether this request may process the email.

        The INSERT ... ON CONFLICT plus the row lock make this safe under concurrent
        requests for the same message_id: exactly one caller gets CLAIMED.
        """
        inserted_id = self._session.scalar(
            pg_insert(EmailRecord)
            .values(
                message_id=email.message_id,
                subject=email.subject,
                sender=email.sender,
                sender_address=email.sender_address,
                received_at=email.received_at,
                status=ProcessingStatus.PROCESSING,
                attempt_count=1,
                processing_started_at=now,
                notification_status=NotificationStatus.NOT_REQUIRED,
            )
            .on_conflict_do_nothing(index_elements=[EmailRecord.message_id])
            .returning(EmailRecord.id)
        )
        if inserted_id is not None:
            self._session.commit()
            return ClaimResult(ClaimOutcome.CLAIMED, self._session.get(EmailRecord, inserted_id))

        record = self._locked(EmailRecord.message_id == email.message_id)
        outcome = self._existing_record_outcome(record, max_attempts, stale_after, now)
        if outcome == ClaimOutcome.CLAIMED:
            record.status = ProcessingStatus.PROCESSING
            record.attempt_count += 1
            record.processing_started_at = now
            record.error_code = None
            record.error_message = None
        elif outcome == ClaimOutcome.MAX_ATTEMPTS and record.status == ProcessingStatus.PROCESSING:
            # Last attempt crashed mid-flight: surface it as FAILED so /retry can recover it.
            record.status = ProcessingStatus.FAILED
            record.error_code = "STALE_MAX_ATTEMPTS"
            record.error_message = "Worker abandoned the final attempt"
        self._session.commit()
        return ClaimResult(outcome, record)

    def _locked(self, condition) -> EmailRecord | None:
        """SELECT ... FOR UPDATE that also refreshes any copy already in the identity map."""
        return self._session.scalar(
            select(EmailRecord)
            .where(condition)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _locked_if_owned(self, record_id: int, fencing_token: int) -> tuple[EmailRecord, bool]:
        record = self._locked(EmailRecord.id == record_id)
        owned = (
            record.status == ProcessingStatus.PROCESSING and record.attempt_count == fencing_token
        )
        return record, owned

    @staticmethod
    def _existing_record_outcome(
        record: EmailRecord, max_attempts: int, stale_after: timedelta, now: datetime
    ) -> ClaimOutcome:
        if record.status == ProcessingStatus.COMPLETED:
            return ClaimOutcome.ALREADY_COMPLETED
        is_stale = record.processing_started_at <= now - stale_after
        if record.status == ProcessingStatus.PROCESSING and not is_stale:
            return ClaimOutcome.IN_PROGRESS
        # FAILED, or PROCESSING abandoned by a crashed worker.
        if record.attempt_count >= max_attempts:
            return ClaimOutcome.MAX_ATTEMPTS
        return ClaimOutcome.CLAIMED

    def complete(
        self,
        record_id: int,
        fencing_token: int,
        analysis: AgentAnalysis | None,
        decision: Decision,
    ) -> WriteResult:
        record, owned = self._locked_if_owned(record_id, fencing_token)
        if not owned:
            self._session.rollback()
            return WriteResult(record, applied=False)
        if analysis is not None:
            record.category = analysis.category
            record.priority = analysis.priority
            record.summary = analysis.summary
            record.reason = analysis.reason
            record.confidence = analysis.confidence
        else:
            record.reason = "Model output could not be validated; manual review required."
        record.action = decision.action
        record.decision_rule = decision.rule
        record.notification_status = decision.notification_status
        record.status = ProcessingStatus.COMPLETED
        record.error_code = None
        record.error_message = None
        self._session.commit()
        return WriteResult(record, applied=True)

    def fail(
        self, record_id: int, fencing_token: int, error_code: str, error_message: str
    ) -> WriteResult:
        self._session.rollback()
        record, owned = self._locked_if_owned(record_id, fencing_token)
        if not owned:
            self._session.rollback()
            return WriteResult(record, applied=False)
        record.status = ProcessingStatus.FAILED
        record.error_code = error_code
        record.error_message = error_message[:ERROR_MESSAGE_MAX_CHARS]
        self._session.commit()
        return WriteResult(record, applied=True)

    def reset_attempts(self, message_id: str) -> EmailRecord | None:
        """Allow a FAILED record to be processed again. Returns None if not FAILED."""
        record = self._locked(EmailRecord.message_id == message_id)
        if record is None or record.status != ProcessingStatus.FAILED:
            self._session.rollback()
            return None
        record.attempt_count = 0
        self._session.commit()
        return record

    def record_notification(
        self, message_id: str, *, delivered: bool, error: str | None, now: datetime
    ) -> EmailRecord | None:
        record = self._locked(EmailRecord.message_id == message_id)
        if record is None:
            self._session.rollback()
            return None
        if record.notification_status == NotificationStatus.NOT_REQUIRED:
            self._session.rollback()
            raise NotificationUpdateError("This email does not require a notification")
        if record.notification_status == NotificationStatus.SENT:
            # Idempotent: a confirmed delivery is never downgraded by a late/duplicate report.
            self._session.rollback()
            return record
        if delivered:
            record.notification_status = NotificationStatus.SENT
            record.notified_at = now
            record.notification_error = None
        else:
            record.notification_status = NotificationStatus.FAILED
            record.notification_error = (error or "unknown error")[:ERROR_MESSAGE_MAX_CHARS]
        self._session.commit()
        return record

    def find_history(
        self, sender_address: str, *, exclude_message_id: str, limit: int
    ) -> list[HistoryEntry]:
        records = [
            HistoryEntry(r.category, r.priority, r.action, r.received_at)
            for r in self._session.scalars(
                select(EmailRecord)
                .where(
                    EmailRecord.sender_address == sender_address,
                    EmailRecord.message_id != exclude_message_id,
                    EmailRecord.status == ProcessingStatus.COMPLETED,
                )
                .order_by(EmailRecord.received_at.desc())
                .limit(limit)
            )
        ]
        # End the read transaction: this runs mid-analysis, and we must not sit
        # "idle in transaction" while waiting on the next LLM call.
        self._session.rollback()
        return records

    def search(self, filters: EmailSearchFilters, *, limit: int) -> list[EmailRecord]:
        """Newest-first records matching every given filter, at most `limit` rows."""
        column_filters = {
            EmailRecord.sender_address: filters.sender_address,
            EmailRecord.category: filters.category,
            EmailRecord.priority: filters.priority,
            EmailRecord.action: filters.action,
            EmailRecord.status: filters.status,
        }
        conditions = [column == value for column, value in column_filters.items() if value]
        if filters.received_after is not None:
            conditions.append(EmailRecord.received_at >= filters.received_after)
        if filters.received_before is not None:
            conditions.append(EmailRecord.received_at < filters.received_before)
        return list(
            self._session.scalars(
                select(EmailRecord)
                .where(*conditions)
                .order_by(EmailRecord.received_at.desc(), EmailRecord.id.desc())
                .limit(limit)
            )
        )
