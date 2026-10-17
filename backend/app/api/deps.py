from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.repositories.email_repository import EmailRepository
from app.services.agent_service import Analyzer
from app.services.analyzer_builder import build_analyzer
from app.services.email_processor import EmailProcessor
from app.tools.email_history import HistorySource
from app.tools.mcp_history import McpHistoryLookup, http_connector


@lru_cache
def _cached_analyzer() -> Analyzer:
    return build_analyzer(get_settings())


def get_analyzer_factory() -> Callable[[], Analyzer]:
    """Analyzer is built lazily so a missing LLM key fails the request, not startup."""
    return _cached_analyzer


@lru_cache
def _cached_mcp_history() -> McpHistoryLookup | None:
    settings = get_settings()
    if settings.mcp_server_url is None or settings.mcp_api_key is None:
        return None
    return McpHistoryLookup(
        http_connector(
            settings.mcp_server_url,
            token=settings.mcp_api_key.get_secret_value(),
            timeout_s=settings.mcp_timeout_s,
        )
    )


def get_history_source() -> HistorySource | None:
    """The MCP server when `MCP_SERVER_URL` is set; None means the processor reads the DB."""
    return _cached_mcp_history()


def get_repository(session: Annotated[Session, Depends(get_session)]) -> EmailRepository:
    return EmailRepository(session)


def get_processor(
    repository: Annotated[EmailRepository, Depends(get_repository)],
    analyzer_factory: Annotated[Callable[[], Analyzer], Depends(get_analyzer_factory)],
    settings: Annotated[Settings, Depends(get_settings)],
    history_source: Annotated[HistorySource | None, Depends(get_history_source)],
) -> EmailProcessor:
    return EmailProcessor(repository, analyzer_factory, settings, history_source=history_source)
