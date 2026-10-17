"""Read-only agent tool: previous processing results for the current email's sender.

The tool takes no arguments from the model. It is bound to the sender of the email
being analyzed, so a prompt-injected email cannot make the agent query other senders.
Only classification metadata is returned: no bodies, and no subjects either, because
subjects are attacker-controlled text that would bypass the <email> delimiters.
"""

from typing import Protocol

from app.repositories.email_repository import EmailRepository

HISTORY_TOOL_NAME = "lookup_email_history"


class HistorySource(Protocol):
    """Where the tool's data comes from: the DB directly, or the MCP server (`mcp_history`)."""

    def __call__(self, *, sender_address: str, exclude_message_id: str, limit: int) -> dict: ...
HISTORY_TOOL_DESCRIPTION = (
    "Return previous classification results for emails from the SAME sender as the "
    "email being analyzed (category, priority, action, date). Call this only "
    "if knowing how earlier emails from this sender were handled would change your analysis."
)


def lookup_email_history(
    repository: EmailRepository, *, sender_address: str, exclude_message_id: str, limit: int
) -> dict:
    records = repository.find_history(
        sender_address, exclude_message_id=exclude_message_id, limit=limit
    )
    return {
        "sender": sender_address,
        "count": len(records),
        "records": [
            {
                "category": record.category,
                "priority": record.priority,
                "action": record.action,
                "received_at": record.received_at.isoformat(),
            }
            for record in records
        ],
    }
