from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import get_processor, get_repository
from app.core.errors import ApiError
from app.core.security import require_api_key
from app.db.models import EmailRecord
from app.repositories.email_repository import EmailRepository, NotificationUpdateError
from app.schemas.email import EmailIn, EmailRecordOut, NotificationReport, ProcessResponse
from app.services.email_processor import EmailProcessor, to_response, utc_now

router = APIRouter(
    prefix="/api/v1/emails", tags=["emails"], dependencies=[Depends(require_api_key)]
)

MessageId = Annotated[str, Path(min_length=1, max_length=255)]
Repository = Annotated[EmailRepository, Depends(get_repository)]


def _not_found(message_id: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", "No record for this message_id", message_id=message_id)


def _to_record_out(record: EmailRecord) -> EmailRecordOut:
    return EmailRecordOut(
        **to_response(record, already_processed=False).model_dump(exclude={"already_processed"}),
        already_processed=record.status == "COMPLETED",
        received_at=record.received_at,
        attempt_count=record.attempt_count,
        error_code=record.error_code,
        notified_at=record.notified_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.post("/process", response_model=ProcessResponse)
def process_email(
    email: EmailIn, processor: Annotated[EmailProcessor, Depends(get_processor)]
) -> ProcessResponse:
    return processor.process(email)


@router.get("/{message_id}", response_model=EmailRecordOut)
def get_email(message_id: MessageId, repository: Repository) -> EmailRecordOut:
    record = repository.get_by_message_id(message_id)
    if record is None:
        raise _not_found(message_id)
    return _to_record_out(record)


@router.post("/{message_id}/retry", response_model=EmailRecordOut)
def allow_retry(message_id: MessageId, repository: Repository) -> EmailRecordOut:
    """Reset the attempt counter of a FAILED record; n8n then re-POSTs /process."""
    if repository.get_by_message_id(message_id) is None:
        raise _not_found(message_id)
    record = repository.reset_attempts(message_id)
    if record is None:
        raise ApiError(409, "NOT_RETRYABLE", "Only FAILED records can be retried", message_id=message_id)
    return _to_record_out(record)


@router.post("/{message_id}/notification", response_model=EmailRecordOut)
def report_notification(
    message_id: MessageId, report: NotificationReport, repository: Repository
) -> EmailRecordOut:
    try:
        record = repository.record_notification(
            message_id, delivered=report.delivered, error=report.error, now=utc_now()
        )
    except NotificationUpdateError as exc:
        raise ApiError(409, "NOTIFICATION_NOT_REQUIRED", str(exc), message_id=message_id) from exc
    if record is None:
        raise _not_found(message_id)
    return _to_record_out(record)
