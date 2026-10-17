import os

# Configure the app for tests BEFORE any app module reads settings.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://email:email@localhost:5433/email_automation_test"
)
os.environ["API_KEY"] = "test-api-key-0123456789"
os.environ["GEMINI_API_KEY"] = "fake-key-never-used"
# Blank = unset: tests read history from the DB unless a test wires an MCP client itself.
os.environ["MCP_SERVER_URL"] = ""

import threading  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.deps import get_analyzer_factory  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.repositories.email_repository import EmailRepository  # noqa: E402
from app.schemas.email import AgentAnalysis, Category, EmailIn, Priority  # noqa: E402
from app.services.agent_service import AnalysisOutcome  # noqa: E402
from app.services.email_processor import EmailProcessor  # noqa: E402

API_KEY = os.environ["API_KEY"]
AUTH = {"X-API-Key": API_KEY}


def make_analysis(**overrides) -> AgentAnalysis:
    values = {
        "category": Category.CAREER,
        "priority": Priority.HIGH,
        "summary": "Invitation to an AI Engineer interview.",
        "needs_response": True,
        "confidence": 0.95,
        "reason": "Requires the recipient to respond.",
    }
    return AgentAnalysis(**{**values, **overrides})


def email_payload(**overrides) -> dict:
    values = {
        "message_id": "msg-001",
        "subject": "Interview invitation",
        "body": "We would like to invite you to an interview next Tuesday.",
        "sender": "Recruiter <recruiter@example.com>",
        "received_at": "2026-10-14T09:00:00Z",
    }
    return {**values, **overrides}


class FakeAnalyzer:
    """Stands in for Gemini. Thread-safe call counting for concurrency tests."""

    def __init__(
        self,
        analysis: AgentAnalysis | None = None,
        *,
        error: Exception | None = None,
        delay_s: float = 0.0,
        use_history: bool = False,
    ) -> None:
        self.analysis = analysis if analysis is not None else make_analysis()
        self.return_none = False
        self.error = error
        self.delay_s = delay_s
        self.use_history = use_history
        self.history_results: list[dict] = []
        self.calls = 0
        self._lock = threading.Lock()

    def analyze(self, email: EmailIn, history_lookup: Callable[[], dict]) -> AnalysisOutcome:
        with self._lock:
            self.calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        tool_calls: tuple[str, ...] = ()
        if self.use_history:
            self.history_results.append(history_lookup())
            tool_calls = ("lookup_email_history",)
        return AnalysisOutcome(
            analysis=None if self.return_none else self.analysis, tool_calls=tool_calls
        )


@pytest.fixture(scope="session")
def engine():
    db_engine = get_engine()
    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    yield db_engine
    Base.metadata.drop_all(db_engine)


@pytest.fixture
def db(engine) -> Iterator[None]:
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE email_records RESTART IDENTITY"))


@pytest.fixture
def session(db) -> Iterator[Session]:
    with get_sessionmaker()() as db_session:
        yield db_session


@pytest.fixture
def fake_analyzer() -> FakeAnalyzer:
    return FakeAnalyzer()


@pytest.fixture
def client(db, fake_analyzer) -> Iterator[TestClient]:
    app.dependency_overrides[get_analyzer_factory] = lambda: (lambda: fake_analyzer)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def make_processor(db, fake_analyzer):
    """Build a processor with its own session and an optional fixed clock."""
    sessions: list[Session] = []

    def factory(
        clock: Callable[[], datetime] | None = None, analyzer=None, history_source=None
    ) -> EmailProcessor:
        db_session = get_sessionmaker()()
        sessions.append(db_session)
        chosen = analyzer or fake_analyzer
        return EmailProcessor(
            EmailRepository(db_session),
            lambda: chosen,
            get_settings(),
            clock or (lambda: datetime.now(UTC)),
            history_source=history_source,
        )

    yield factory
    for db_session in sessions:
        db_session.close()
