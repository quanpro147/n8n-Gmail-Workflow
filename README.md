# AI Email Automation (n8n + FastAPI + Gemini + PostgreSQL)

n8n fetches Gmail every 12 hours and sends each email to a FastAPI backend. The backend:
- dedupes the email,
- asks a Gemini agent to classify it,
- applies deterministic Python business rules,
- stores the result in PostgreSQL,
- tells n8n whether to send a Telegram notification.

```
n8n (Gmail, schedule) ──POST /process──▶ FastAPI ──▶ Gemini agent (+ lookup_email_history tool)
        ▲                                  │                    │ MCP client
        │◀──── JSON {action, ...} ─────────┤                    ▼
        └─▶ Telegram ──POST /notification──┘        MCP server (:8001, search_email_history,
                                           │                     get_email_details)
                                           ▼                    │ read-only
                                PostgreSQL (email_records) ◀────┘
                                    (other MCP clients, e.g. Claude Code, also connect to :8001)
```

Full spec: [SDP.md](SDP.md). n8n contract: [n8n/README.md](n8n/README.md).

## Quick start

```bash
cp backend/.env.example backend/.env      # set API_KEY, GEMINI_API_KEY and MCP_API_KEY
docker compose up -d --build              # postgres (host port 5433) + backend (:8000, runs migrations) + MCP (:8001)
curl localhost:8000/health
```

If 5433 is also taken, set `POSTGRES_HOST_PORT` in your shell or in a root `.env`.

### Try it
```bash
curl -s -X POST localhost:8000/api/v1/emails/process \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"message_id":"demo-1","subject":"Interview invitation","body":"Please confirm your interview slot for Tuesday 10:00.","sender":"Recruiter <hr@example.com>","received_at":"2026-10-14T09:00:00Z"}'
```
Sending the same request again returns `"already_processed": true` and makes no LLM call.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + DB check (no auth) |
| POST | `/api/v1/emails/process` | validate, dedupe, analyze, persist, return decision |
| GET | `/api/v1/emails/{message_id}` | fetch a stored record |
| POST | `/api/v1/emails/{message_id}/retry` | allow a FAILED record (attempt limit reached) to be processed again |
| POST | `/api/v1/emails/{message_id}/notification` | n8n reports Telegram delivery `{"delivered": bool, "error"?}` |

All `/api/v1` routes need the `X-API-Key` header. Interactive docs are at `http://localhost:8000/docs`.

Process response:
```json
{
  "message_id": "demo-1", "status": "COMPLETED",
  "category": "career", "priority": "high",
  "summary": "...", "action": "notify", "reason": "...",
  "notification_status": "PENDING", "already_processed": false
}
```
`action` is one of `notify | ignore | review`. Errors use `{"error": {"code", "message", "retryable", "message_id"?}}`.

## How processing works
1. **Claim**: an `INSERT ... ON CONFLICT` plus a row lock decides the case:
   - New email → `PROCESSING`.
   - Already `COMPLETED` → return the stored result.
   - `PROCESSING` → `409 IN_PROGRESS`, unless the record is stale (older than 15 min), in which case it is reclaimed.
   - `FAILED` → retried up to `MAX_PROCESS_ATTEMPTS`.
2. **Agent**: Gemini may call `lookup_email_history` (read-only, limited to the same sender, at most 2 rounds; served through the MCP server when `MCP_SERVER_URL` is set). It then returns JSON that is validated against `AgentAnalysis`. If the output is still invalid after one re-ask, the action becomes `review`.
3. **Rules**: [notification_rules.py](backend/app/services/notification_rules.py) runs an ordered rule list where the first match wins: low confidence → review, promotion → ignore, high priority → notify, career/finance needing a response → notify, low → ignore.
4. **Persist + respond.** Email bodies are never stored or logged.

## MCP server
A separate, read-only service exposes the processing records to MCP clients (Claude Desktop, n8n's MCP Client node, MCP Inspector). Code: [backend/mcp_server/](backend/mcp_server/).

```bash
# set MCP_API_KEY (≥16 chars) in backend/.env first
docker compose up -d --build mcp      # Streamable HTTP at http://localhost:8001/mcp
```
The port is published on `127.0.0.1` only; set `MCP_HOST_PORT` to change it. To run it outside Docker: `cd backend && python -m mcp_server`, which uses `MCP_HOST`/`MCP_PORT` and the host DB on 5433.

Clients send `Authorization: Bearer <MCP_API_KEY>`. A missing or wrong token gets `401 UNAUTHORIZED`. Requests with a Host header other than `localhost`/`127.0.0.1` are rejected (DNS-rebinding protection). The transport is stateless Streamable HTTP with JSON responses, so there are no sessions to keep alive.

Connect a client, for example Claude Code:
```bash
claude mcp add --transport http email-records http://localhost:8001/mcp \
  --header "Authorization: Bearer $MCP_API_KEY"
```
For MCP Inspector or n8n's MCP Client node, use the same URL with transport "Streamable HTTP" and the same header.

| Tool | Arguments | Returns |
|---|---|---|
| `get_email_details` | `message_id` (1–255 chars) | the stored result: subject, category, priority, summary, action, reason, confidence, rule, status, notification state. Unknown ID → tool error. |
| `search_email_history` | all optional: `sender`, `category`, `priority`, `action`, `status`, `received_after` (inclusive), `received_before` (exclusive), `limit` (1–`MCP_MAX_RESULTS`, default 10) | `{count, limit, has_more, records[]}`, newest first. Records hold metadata only (no subject/summary). No match → `count: 0`, not an error. |

Invalid arguments (unknown enum value, timestamp without timezone, inverted date range, non-address `sender`, limit out of range) come back as tool errors (`isError: true`) that the client can read and correct. Every DB transaction is opened `READ ONLY`. Email bodies are never stored, so no tool can return them. Subjects and summaries come from email content, so clients should treat them as untrusted data.

### The email agent as an MCP client
When `MCP_SERVER_URL` is set (docker-compose sets `http://mcp:8001/mcp`), the backend agent's history tool goes through this server:

```
Gemini/fallback ──calls lookup_email_history()──▶ backend (MCP client)
    ──tools/call search_email_history {sender: <current sender>, status: COMPLETED, limit}──▶ MCP server ──▶ Postgres (READ ONLY)
```

The model still sees the argument-free `lookup_email_history`. The backend fills in the MCP arguments itself, so `sender` is always the sender of the email being analyzed. `search_email_history` accepts any sender, so letting the model choose it would let a prompt-injected email read other senders' history. The result has the same metadata-only shape as before, so prompts and both analyzers are unchanged.

If the MCP server is unreachable or answers with an error, the tool returns `{"error": "History unavailable"}`, a warning is logged (`outcome: mcp_unavailable`), and the email is still classified. If `MCP_SERVER_URL` is blank, the tool reads the DB directly.

## Configuration
See [backend/.env.example](backend/.env.example).

| Variable | Purpose |
|---|---|
| `API_KEY` | required, ≥16 chars |
| `GEMINI_API_KEY` | Gemini API key |
| `GEMINI_MODEL` | default `gemini-flash-lite-latest` |
| `FALLBACK_LLM_API_KEY` / `_BASE_URL` / `_MODEL` | OpenAI-compatible fallback when Gemini fails (default Groq, `openai/gpt-oss-120b`); blank key = Gemini only |
| `MAX_PROCESS_ATTEMPTS` | how many times a failing email is retried before blocking |
| `STALE_PROCESSING_MINUTES` | when a stuck `PROCESSING` record can be reclaimed |
| `MIN_CONFIDENCE` | below this, the action is `review` |
| `MAX_BODY_CHARS` | how much of the email body is sent to the LLM |
| `MCP_API_KEY` | bearer token for the MCP server, ≥16 chars (required to start it) |
| `MCP_HOST` / `MCP_PORT` | bind address for `python -m mcp_server` (default `127.0.0.1:8001`; compose sets `0.0.0.0` inside the container) |
| `MCP_ALLOWED_HOSTS` | extra Host headers the MCP server accepts besides localhost, comma-separated (compose sets `mcp:*`) |
| `MCP_SERVER_URL` | MCP endpoint the backend agent's history tool calls; blank = read the DB directly (compose sets `http://mcp:8001/mcp`) |
| `MCP_TIMEOUT_S` | timeout for the backend's MCP calls (default 10) |
| `MCP_MAX_RESULTS` | upper bound for `search_email_history`'s `limit` (default 50) |

## Development

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # Windows; use bin/activate on macOS/Linux
pip install -r requirements-dev.txt
docker compose up -d db                             # tests need the email_automation_test DB
pytest --cov=app                                    # all tests
pytest tests/test_processing.py::test_stale_processing_record_is_reclaimed   # one test
pytest tests/test_mcp_server.py tests/test_mcp_history.py   # MCP server + backend MCP client
```
Inside Docker: `docker compose exec backend pytest`.

Tests use a real Postgres test database. Gemini is always faked, so no real credentials are needed.

Migrations: `alembic upgrade head` (runs automatically on container start). For a new revision, run `alembic revision -m "..."` and write the migration by hand.
