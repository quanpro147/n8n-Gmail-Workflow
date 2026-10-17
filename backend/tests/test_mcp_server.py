from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from sqlalchemy import text
from sqlalchemy.exc import InternalError

from app.core.config import get_settings
from app.db.models import EmailRecord
from app.db.session import get_sessionmaker
from mcp_server.app import build_app, read_only_engine
from mcp_server.server import build_server

MAX_RESULTS = 5
TOKEN = "mcp-test-token-0123456789"
BASE_TIME = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def server(db):
    return build_server(get_sessionmaker(), max_results=MAX_RESULTS)


@pytest.fixture
def add_record(session):
    def add(message_id: str, **overrides) -> EmailRecord:
        values = {
            "message_id": message_id,
            "subject": "Interview invitation",
            "sender": "Recruiter <recruiter@example.com>",
            "sender_address": "recruiter@example.com",
            "received_at": BASE_TIME,
            "category": "career",
            "priority": "high",
            "summary": "Invitation to an interview.",
            "action": "notify",
            "reason": "Needs a reply.",
            "confidence": 0.9,
            "decision_rule": "high_priority",
            "status": "COMPLETED",
            "attempt_count": 1,
            "processing_started_at": BASE_TIME,
            "notification_status": "PENDING",
        }
        record = EmailRecord(**{**values, **overrides})
        session.add(record)
        session.commit()
        return record

    return add


async def call(server, tool: str, arguments: dict):
    async with Client(server) as client:
        return await client.call_tool(tool, arguments)


def error_text(result) -> str:
    assert result.is_error
    return " ".join(block.text for block in result.content)


# --- get_email_details -------------------------------------------------------


@pytest.mark.anyio
async def test_get_email_details_returns_stored_record(server, add_record):
    add_record("msg-1")

    result = await call(server, "get_email_details", {"message_id": "msg-1"})

    assert not result.is_error
    details = result.structured_content
    assert details["message_id"] == "msg-1"
    assert details["category"] == "career"
    assert details["action"] == "notify"
    assert details["notification_status"] == "PENDING"
    assert "body" not in details


@pytest.mark.anyio
async def test_get_email_details_strips_whitespace_around_message_id(server, add_record):
    add_record("msg-1")

    result = await call(server, "get_email_details", {"message_id": "  msg-1  "})

    assert result.structured_content["message_id"] == "msg-1"


@pytest.mark.anyio
async def test_get_email_details_unknown_id_is_a_tool_error(server):
    result = await call(server, "get_email_details", {"message_id": "missing"})

    assert "No processed email has message_id 'missing'" in error_text(result)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [{}, {"message_id": ""}, {"message_id": "   "}, {"message_id": "x" * 256}, {"message_id": 7}],
)
async def test_get_email_details_rejects_invalid_message_id(server, arguments):
    result = await call(server, "get_email_details", arguments)

    assert result.is_error
    assert result.structured_content is None


# --- search_email_history ----------------------------------------------------


@pytest.mark.anyio
async def test_search_returns_empty_result_when_nothing_matches(server, add_record):
    add_record("msg-1")

    result = await call(server, "search_email_history", {"sender": "nobody@example.com"})

    assert not result.is_error
    assert result.structured_content == {"count": 0, "limit": 5, "has_more": False, "records": []}


@pytest.mark.anyio
async def test_search_on_empty_table_returns_no_records(server):
    result = await call(server, "search_email_history", {})

    assert result.structured_content["records"] == []


@pytest.mark.anyio
async def test_search_filters_by_sender_newest_first(server, add_record):
    add_record("old", received_at=BASE_TIME)
    add_record("new", received_at=BASE_TIME + timedelta(days=1))
    add_record("other", sender_address="someone@else.com")

    result = await call(
        server, "search_email_history", {"sender": "Recruiter <RECRUITER@example.com>"}
    )

    records = result.structured_content["records"]
    assert [r["message_id"] for r in records] == ["new", "old"]
    assert "subject" not in records[0] and "summary" not in records[0]


@pytest.mark.anyio
async def test_search_combines_enum_and_date_filters(server, add_record):
    add_record("match", category="finance", received_at=BASE_TIME + timedelta(hours=1))
    add_record("wrong-category", category="career", received_at=BASE_TIME + timedelta(hours=1))
    add_record("too-late", category="finance", received_at=BASE_TIME + timedelta(days=2))

    result = await call(
        server,
        "search_email_history",
        {
            "category": "finance",
            "received_after": BASE_TIME.isoformat(),
            "received_before": (BASE_TIME + timedelta(days=1)).isoformat(),
        },
    )

    assert [r["message_id"] for r in result.structured_content["records"]] == ["match"]


@pytest.mark.anyio
async def test_search_uses_default_limit_and_reports_has_more(add_record, db):
    server = build_server(get_sessionmaker(), max_results=50)
    for i in range(12):
        add_record(f"msg-{i}", received_at=BASE_TIME + timedelta(minutes=i))

    result = await call(server, "search_email_history", {})

    content = result.structured_content
    assert (content["count"], content["limit"], content["has_more"]) == (10, 10, True)
    assert content["records"][0]["message_id"] == "msg-11"


@pytest.mark.anyio
async def test_search_has_more_is_false_when_everything_fits(server, add_record):
    for i in range(3):
        add_record(f"msg-{i}")

    result = await call(server, "search_email_history", {"limit": 3})

    assert (result.structured_content["count"], result.structured_content["has_more"]) == (3, False)


@pytest.mark.anyio
async def test_search_default_limit_never_exceeds_max_results(server, add_record):
    for i in range(MAX_RESULTS + 2):
        add_record(f"msg-{i}")

    result = await call(server, "search_email_history", {})

    assert result.structured_content["count"] == MAX_RESULTS


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [0, -1, MAX_RESULTS + 1, "ten"])
async def test_search_rejects_limit_out_of_bounds(server, limit):
    result = await call(server, "search_email_history", {"limit": limit})

    assert result.is_error


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {"category": "spam"},
        {"priority": "urgent"},
        {"action": "delete"},
        {"status": "DONE"},
        {"received_after": "yesterday"},
        {"received_after": "2026-10-01T09:00:00"},  # no timezone
        {"sender": "x" * 321},
    ],
)
async def test_search_rejects_invalid_filter_values(server, arguments):
    result = await call(server, "search_email_history", arguments)

    assert result.is_error


@pytest.mark.anyio
@pytest.mark.parametrize("sender", ["not-an-address", "@example.com", "name@"])
async def test_search_rejects_sender_without_email_address(server, sender):
    result = await call(server, "search_email_history", {"sender": sender})

    assert "sender must be an email address" in error_text(result)


@pytest.mark.anyio
async def test_search_rejects_inverted_date_range(server):
    result = await call(
        server,
        "search_email_history",
        {"received_after": "2026-10-02T00:00:00Z", "received_before": "2026-10-01T00:00:00Z"},
    )

    assert "received_after must be earlier than received_before" in error_text(result)


@pytest.mark.anyio
async def test_tools_are_declared_read_only(server):
    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == {"get_email_details", "search_email_history"}
    assert all(tool.annotations.read_only_hint for tool in tools)


# --- read-only engine and HTTP auth ------------------------------------------


def test_read_only_engine_rejects_writes(db):
    engine = read_only_engine(get_settings().database_url)

    with pytest.raises(InternalError, match="read-only transaction"):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM email_records"))
    engine.dispose()


LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def http_client(server):
    app = build_app(server, token=TOKEN)
    with TestClient(app, base_url="http://localhost:8001") as client:
        yield client


@pytest.mark.parametrize("authorization", [None, "Bearer wrong-token-0123456789", TOKEN])
def test_http_rejects_missing_or_wrong_token(http_client, authorization):
    headers = MCP_HEADERS if authorization is None else {**MCP_HEADERS, "Authorization": authorization}

    response = http_client.post("/mcp", json=LIST_TOOLS, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_http_serves_tools_with_valid_token(http_client):
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}

    response = http_client.post("/mcp", json=LIST_TOOLS, headers=headers)

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {"get_email_details", "search_email_history"}


def test_http_rejects_foreign_host_header(server):
    # The SDK's DNS-rebinding protection: only localhost/127.0.0.1 Host headers are served.
    app = build_app(server, token=TOKEN)
    with TestClient(app, base_url="http://evil.example:8001") as client:
        response = client.post(
            "/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
        )

    assert response.status_code == 421


def test_http_accepts_configured_extra_host(server):
    # docker-compose: the backend container reaches the server as `mcp:8001`.
    app = build_app(server, token=TOKEN, extra_allowed_hosts=("mcp:*",))
    with TestClient(app, base_url="http://mcp:8001") as client:
        response = client.post(
            "/mcp", json=LIST_TOOLS, headers={**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
        )

    assert response.status_code == 200
