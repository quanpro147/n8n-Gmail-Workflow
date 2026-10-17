"""Agent history tool backed by the MCP server's `search_email_history`.

The model still sees the argument-free `lookup_email_history`; the backend fills in the
MCP arguments itself. The sender stays bound to the email being analyzed, so a
prompt-injected email can't use MCP to read another sender's history either. The result
has the same shape (and the same metadata-only fields) as the in-process tool.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ValidationError

from app.schemas.email import ProcessingStatus

logger = logging.getLogger(__name__)

SEARCH_TOOL_NAME = "search_email_history"
UNAVAILABLE = {"error": "History unavailable"}

ClientConnector = Callable[[], AbstractAsyncContextManager[Client]]


class McpToolError(Exception):
    """The MCP server answered the call with a tool error or an unexpected result."""


class _HistoryRecord(BaseModel):
    message_id: str
    received_at: datetime
    category: str | None
    priority: str | None
    action: str | None


class _SearchResult(BaseModel):
    records: list[_HistoryRecord]


def http_connector(url: str, *, token: str, timeout_s: float) -> ClientConnector:
    """Connect over Streamable HTTP with the shared bearer token, one connection per call."""

    @asynccontextmanager
    async def connect() -> AsyncIterator[Client]:
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(timeout_s)) as http:
            transport = streamable_http_client(url, http_client=http, terminate_on_close=False)
            async with Client(transport, read_timeout_seconds=timeout_s, cache=None) as client:
                yield client

    return connect


class McpHistoryLookup:
    """Same keyword interface as `lookup_email_history`, minus the repository."""

    def __init__(self, connect: ClientConnector) -> None:
        self._connect = connect

    def __call__(self, *, sender_address: str, exclude_message_id: str, limit: int) -> dict:
        try:
            records = anyio.run(self._search, sender_address, limit + 1)
        except Exception as exc:  # History is optional context: never fail the email over it.
            logger.warning(
                "MCP history lookup failed",
                extra={"outcome": "mcp_unavailable", "error_code": type(exc).__name__},
            )
            return dict(UNAVAILABLE)
        # One extra row covers the email being analyzed, which is excluded here.
        kept = [record for record in records if record.message_id != exclude_message_id][:limit]
        logger.info("MCP history lookup", extra={"outcome": f"{len(kept)} records"})
        return {
            "sender": sender_address,
            "count": len(kept),
            "records": [
                {
                    "category": record.category,
                    "priority": record.priority,
                    "action": record.action,
                    "received_at": record.received_at.isoformat(),
                }
                for record in kept
            ],
        }

    async def _search(self, sender_address: str, limit: int) -> list[_HistoryRecord]:
        arguments = {
            "sender": sender_address,
            "status": ProcessingStatus.COMPLETED.value,
            "limit": limit,
        }
        async with self._connect() as client:
            result = await client.call_tool(SEARCH_TOOL_NAME, arguments)
        if result.is_error:
            raise McpToolError("search_email_history returned a tool error")
        try:
            return _SearchResult.model_validate(result.structured_content).records
        except ValidationError as exc:
            raise McpToolError("search_email_history returned an unexpected result") from exc
