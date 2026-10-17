"""Tool outputs. Email bodies are never stored, so they can never be returned."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EmailDetails(BaseModel):
    """Everything stored about one processed email."""

    model_config = ConfigDict(from_attributes=True)

    message_id: str
    # Attacker-controlled text (like `summary`, which the LLM derived from the email).
    subject: str
    sender_address: str
    received_at: datetime
    status: str
    category: str | None
    priority: str | None
    action: str | None
    summary: str | None
    reason: str | None
    confidence: float | None
    decision_rule: str | None
    error_code: str | None
    attempt_count: int
    notification_status: str
    notified_at: datetime | None
    updated_at: datetime


class EmailHistoryItem(BaseModel):
    """One search hit: classification metadata only, no free text (subject, summary)."""

    model_config = ConfigDict(from_attributes=True)

    message_id: str
    sender_address: str
    received_at: datetime
    status: str
    category: str | None
    priority: str | None
    action: str | None
    notification_status: str


class EmailSearchResult(BaseModel):
    count: int
    limit: int
    # True when more records match than `limit` allowed to return.
    has_more: bool
    records: list[EmailHistoryItem]
