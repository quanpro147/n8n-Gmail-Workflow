# AI Email Automation (n8n + FastAPI + Gemini + PostgreSQL)

n8n fetches Gmail every 12 hours and sends each email to a FastAPI backend. The backend:
- dedupes the email,
- asks a Gemini agent to classify it,
- applies deterministic Python business rules,
- stores the result in PostgreSQL,
- tells n8n whether to send a Telegram notification.

```
n8n (Gmail, schedule) ──POST /process──▶ FastAPI ──▶ Gemini agent (+ lookup_email_history tool)
        ▲                                  │
        │◀──── JSON {action, ...} ─────────┤──▶ PostgreSQL (email_records)
        └─▶ Telegram ──POST /notification──┘
```

Full spec: [SDP.md](SDP.md). n8n contract: [n8n/README.md](n8n/README.md).

## Quick start

```bash
cp backend/.env.example backend/.env      # set API_KEY and GEMINI_API_KEY
docker compose up -d --build              # postgres (host port 5433) + backend (:8000); runs migrations
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
2. **Agent**: Gemini may call `lookup_email_history` (read-only, limited to the same sender, at most 2 rounds). It then returns JSON that is validated against `AgentAnalysis`. If the output is still invalid after one re-ask, the action becomes `review`.
3. **Rules**: [notification_rules.py](backend/app/services/notification_rules.py) runs an ordered rule list where the first match wins: low confidence → review, promotion → ignore, high priority → notify, career/finance needing a response → notify, low → ignore.
4. **Persist + respond.** Email bodies are never stored or logged.

## Configuration
See [backend/.env.example](backend/.env.example).

| Variable | Purpose |
|---|---|
| `API_KEY` | required, ≥16 chars |
| `GEMINI_API_KEY` | Gemini API key |
| `GEMINI_MODEL` | default `gemini-2.5-flash` |
| `MAX_PROCESS_ATTEMPTS` | how many times a failing email is retried before blocking |
| `STALE_PROCESSING_MINUTES` | when a stuck `PROCESSING` record can be reclaimed |
| `MIN_CONFIDENCE` | below this, the action is `review` |
| `MAX_BODY_CHARS` | how much of the email body is sent to the LLM |

## Development

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # Windows; use bin/activate on macOS/Linux
pip install -r requirements-dev.txt
docker compose up -d db                             # tests need the email_automation_test DB
pytest --cov=app                                    # all tests
pytest tests/test_processing.py::test_stale_processing_record_is_reclaimed   # one test
```
Inside Docker: `docker compose exec backend pytest`.

Tests use a real Postgres test database. Gemini is always faked, so no real credentials are needed.

Migrations: `alembic upgrade head` (runs automatically on container start). For a new revision, run `alembic revision -m "..."` and write the migration by hand.
