"""Labeled evaluation emails (`dataset.jsonl`, one case per line)."""

from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from app.schemas.email import Action, Category, EmailIn, Priority

DEFAULT_DATASET = Path(__file__).with_name("dataset.jsonl")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvalEmail(_Strict):
    subject: str
    body: str
    sender: str
    received_at: AwareDatetime


class HistoryEntry(_Strict):
    """A previous result for the same sender, as `lookup_email_history` would return it."""

    category: Category
    priority: Priority
    action: Action
    received_at: AwareDatetime


class Expected(_Strict):
    category: Category
    priority: Priority
    action: Action


class EvalCase(_Strict):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    lang: Literal["en", "vi"]
    email: EvalEmail
    history: tuple[HistoryEntry, ...] = ()
    expected: Expected
    expects_tool: bool = False
    tags: tuple[str, ...] = ()

    def to_email_in(self) -> EmailIn:
        return EmailIn(message_id=f"eval-{self.id}", **self.email.model_dump())

    def history_result(self) -> dict:
        """The tool result the agent sees for this case: same shape as `lookup_email_history`."""
        email = self.to_email_in()
        return {
            "sender": email.sender_address,
            "count": len(self.history),
            "records": [
                {
                    "category": entry.category.value,
                    "priority": entry.priority.value,
                    "action": entry.action.value,
                    "received_at": entry.received_at.isoformat(),
                }
                for entry in self.history
            ],
        }


class DatasetError(ValueError):
    pass


def load_dataset(path: Path = DEFAULT_DATASET) -> tuple[EvalCase, ...]:
    """Parse and validate every line; fail fast with the line number on bad data."""
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = EvalCase.model_validate_json(line)
            case.to_email_in()  # the email must also be a valid /process payload
        except ValidationError as exc:
            raise DatasetError(f"{path.name}:{line_no}: {exc}") from exc
        if case.id in seen:
            raise DatasetError(f"{path.name}:{line_no}: duplicate id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise DatasetError(f"{path.name}: no cases")
    return tuple(cases)
