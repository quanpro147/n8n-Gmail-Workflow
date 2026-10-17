import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.logging import correlation_id
from app.db.models import EmailRecord
from app.repositories.email_repository import ClaimOutcome, EmailRepository
from app.schemas.email import EmailIn, ProcessingStatus, ProcessResponse
from app.services.agent_service import AnalysisError, Analyzer
from app.services.notification_rules import decide, review_decision
from app.tools.email_history import HistorySource, lookup_email_history

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_response(record: EmailRecord, *, already_processed: bool) -> ProcessResponse:
    return ProcessResponse(
        message_id=record.message_id,
        subject=record.subject,
        sender=record.sender,
        status=record.status,
        category=record.category,
        priority=record.priority,
        summary=record.summary,
        action=record.action,
        reason=record.reason,
        notification_status=record.notification_status,
        already_processed=already_processed,
    )


class EmailProcessor:
    """Validate -> dedupe/claim -> analyze -> decide -> persist, for a single email."""

    def __init__(
        self,
        repository: EmailRepository,
        analyzer_factory: Callable[[], Analyzer],
        settings: Settings,
        clock: Clock = utc_now,
        history_source: HistorySource | None = None,
    ) -> None:
        self._repository = repository
        self._analyzer_factory = analyzer_factory
        self._settings = settings
        self._clock = clock
        self._history_source = history_source or partial(lookup_email_history, repository)

    def process(self, email: EmailIn) -> ProcessResponse:
        # Tags this email's log lines, and is forwarded to the MCP server (X-Correlation-ID).
        token = correlation_id.set(email.message_id)
        try:
            return self._process(email)
        finally:
            correlation_id.reset(token)

    def _process(self, email: EmailIn) -> ProcessResponse:
        claim = self._repository.claim(
            email,
            max_attempts=self._settings.max_process_attempts,
            stale_after=timedelta(minutes=self._settings.stale_processing_minutes),
            now=self._clock(),
        )
        log_extra = {"message_id": email.message_id, "outcome": claim.outcome}
        logger.info("Claim evaluated", extra=log_extra)

        if claim.outcome == ClaimOutcome.ALREADY_COMPLETED:
            return to_response(claim.record, already_processed=True)
        if claim.outcome == ClaimOutcome.IN_PROGRESS:
            raise ApiError(
                409, "IN_PROGRESS", "Email is currently being processed",
                retryable=True, message_id=email.message_id,
            )
        if claim.outcome == ClaimOutcome.MAX_ATTEMPTS:
            raise ApiError(
                409, "MAX_ATTEMPTS_EXCEEDED",
                "Processing failed too many times; use the retry endpoint after investigating",
                message_id=email.message_id,
            )

        return self._analyze_and_complete(email, claim.record.id, claim.fencing_token)

    def _analyze_and_complete(
        self, email: EmailIn, record_id: int, fencing_token: int
    ) -> ProcessResponse:
        try:
            analyzer = self._analyzer_factory()
            started = time.perf_counter()
            outcome = analyzer.analyze(email, history_lookup=self._history_lookup_for(email))
            analysis_ms = round((time.perf_counter() - started) * 1000, 1)
        except AnalysisError as exc:
            self._safe_fail(email, record_id, fencing_token, exc.code, str(exc))
            raise ApiError(
                502 if exc.transient else 500, exc.code, str(exc),
                retryable=exc.transient, message_id=email.message_id,
            ) from exc
        except Exception:
            self._safe_fail(
                email, record_id, fencing_token, "INTERNAL_ERROR", "Unexpected error during analysis"
            )
            raise

        decision = (
            decide(outcome.analysis, min_confidence=self._settings.min_confidence)
            if outcome.analysis is not None
            else review_decision()
        )
        result = self._repository.complete(record_id, fencing_token, outcome.analysis, decision)
        if not result.applied:
            return self._superseded_response(email, result.record)
        completed = result.record
        logger.info(
            "Email processed",
            extra={
                "message_id": email.message_id,
                "category": completed.category,
                "priority": completed.priority,
                "action": completed.action,
                "tool_calls": list(outcome.tool_calls),
                "provider": outcome.provider,
                "model": outcome.model,
                "llm_calls": outcome.usage.calls,
                "llm_retries": outcome.usage.retries,
                "prompt_tokens": outcome.usage.prompt_tokens,
                "completion_tokens": outcome.usage.completion_tokens,
                "total_tokens": outcome.usage.total_tokens,
                "analysis_ms": analysis_ms,
            },
        )
        return to_response(completed, already_processed=False)

    @staticmethod
    def _superseded_response(email: EmailIn, current: EmailRecord) -> ProcessResponse:
        """Our claim went stale and another worker took over; never overwrite its result."""
        logger.warning(
            "Result discarded: claim superseded by another worker",
            extra={"message_id": email.message_id, "status": current.status},
        )
        if current.status == ProcessingStatus.COMPLETED:
            return to_response(current, already_processed=True)
        raise ApiError(
            409, "IN_PROGRESS", "Email is currently being processed",
            retryable=True, message_id=email.message_id,
        )

    def _safe_fail(
        self, email: EmailIn, record_id: int, fencing_token: int, code: str, message: str
    ) -> None:
        """Record the failure without letting a DB error mask the original exception."""
        logger.warning("Analysis failed", extra={"message_id": email.message_id, "error_code": code})
        try:
            self._repository.fail(record_id, fencing_token, code, message)
        except Exception:
            logger.exception(
                "Could not record failure", extra={"message_id": email.message_id, "error_code": code}
            )

    def _history_lookup_for(self, email: EmailIn) -> Callable[[], dict]:
        def lookup() -> dict:
            return self._history_source(
                sender_address=email.sender_address,
                exclude_message_id=email.message_id,
                limit=self._settings.history_lookup_limit,
            )

        return lookup
