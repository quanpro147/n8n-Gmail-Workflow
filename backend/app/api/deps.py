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


@lru_cache
def _cached_analyzer() -> Analyzer:
    return build_analyzer(get_settings())


def get_analyzer_factory() -> Callable[[], Analyzer]:
    """Analyzer is built lazily so a missing LLM key fails the request, not startup."""
    return _cached_analyzer


def get_repository(session: Annotated[Session, Depends(get_session)]) -> EmailRepository:
    return EmailRepository(session)


def get_processor(
    repository: Annotated[EmailRepository, Depends(get_repository)],
    analyzer_factory: Annotated[Callable[[], Analyzer], Depends(get_analyzer_factory)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EmailProcessor:
    return EmailProcessor(repository, analyzer_factory, settings)
