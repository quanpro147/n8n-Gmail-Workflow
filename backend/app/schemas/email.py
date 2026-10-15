from datetime import datetime
from email.utils import parseaddr
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

SUMMARY_MAX_CHARS = 300
REASON_MAX_CHARS = 500


class Category(StrEnum):
    CAREER = "career"
    EDUCATION = "education"
    FINANCE = "finance"
    PERSONAL = "personal"
    PROMOTION = "promotion"
    OTHER = "other"


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Action(StrEnum):
    NOTIFY = "notify"
    IGNORE = "ignore"
    REVIEW = "review"


class ProcessingStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class NotificationStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


SUBJECT_MAX_CHARS = 998
BODY_MAX_CHARS = 200_000


class EmailIn(BaseModel):
    """Payload n8n sends for one Gmail message.

    Lenient where rejection would silently drop mail (extra fields, null/oversized
    subject or body); strict where it matters (message_id, sender, timezone-aware date).
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    message_id: str = Field(min_length=1, max_length=255, pattern=r"^\S+$")
    subject: str = ""
    body: str = ""
    sender: str = Field(min_length=3, max_length=320)
    received_at: AwareDatetime

    @field_validator("subject", "body", mode="before")
    @classmethod
    def none_to_empty(cls, value: object) -> object:
        return "" if value is None else value

    @field_validator("subject")
    @classmethod
    def truncate_subject(cls, value: str) -> str:
        return value[:SUBJECT_MAX_CHARS]

    @field_validator("body")
    @classmethod
    def truncate_body(cls, value: str) -> str:
        return value[:BODY_MAX_CHARS]

    @field_validator("sender")
    @classmethod
    def sender_must_contain_address(cls, value: str) -> str:
        if "@" not in parseaddr(value)[1]:
            raise ValueError("sender must contain an email address")
        return value

    @property
    def sender_address(self) -> str:
        return parseaddr(self.sender)[1].lower()


class AgentAnalysis(BaseModel):
    """Structured output the LLM must produce. Also used as Gemini's response schema."""

    category: Category
    priority: Priority
    summary: str
    needs_response: bool
    confidence: float = Field(ge=0, le=1)
    reason: str

    @field_validator("summary")
    @classmethod
    def truncate_summary(cls, value: str) -> str:
        return value.strip()[:SUMMARY_MAX_CHARS]

    @field_validator("reason")
    @classmethod
    def truncate_reason(cls, value: str) -> str:
        return value.strip()[:REASON_MAX_CHARS]


class ProcessResponse(BaseModel):
    message_id: str
    subject: str
    sender: str
    status: ProcessingStatus
    category: Category | None
    priority: Priority | None
    summary: str | None
    action: Action | None
    reason: str | None
    notification_status: NotificationStatus
    already_processed: bool


class EmailRecordOut(ProcessResponse):
    received_at: datetime
    attempt_count: int
    error_code: str | None
    notified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NotificationReport(BaseModel):
    """n8n reports the Telegram delivery result with this payload."""

    model_config = ConfigDict(extra="forbid")

    delivered: bool
    error: str | None = Field(default=None, max_length=500)
