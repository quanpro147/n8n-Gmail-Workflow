---
name: run-backend
description: Start, test, migrate and smoke-test the FastAPI email backend (Docker Compose + Postgres on host port 5433). Use when asked to run the app, run tests or a single test, apply migrations, or check that a change works end to end.
---

# Run the email backend

All paths are relative to the repo root. On Windows use the Bash tool (Git Bash) or adapt the commands.

## Start the stack
```bash
test -f backend/.env || cp backend/.env.example backend/.env   # then set API_KEY (>=16 chars), GEMINI_API_KEY
docker compose up -d --build
curl -s localhost:8000/health        # {"status":"ok","db":"up"}
docker compose logs -f backend       # JSON logs
```
- Postgres is published on host port **5433**, because another Postgres on this machine uses 5432. Override with `POSTGRES_HOST_PORT`.
- The backend container runs `alembic upgrade head` on start.
- The `email_automation_test` DB is created by `docker/postgres-init/01-create-test-db.sql`. This only happens when the volume is first initialized. If it's missing, run `docker compose exec db createdb -U email email_automation_test`.

## Tests (host venv)
```bash
cd backend
.venv/Scripts/python -m pytest --cov=app --cov-report=term-missing
.venv/Scripts/python -m pytest tests/test_processing.py::test_failed_email_can_be_retried_and_succeeds
```
- `tests/conftest.py` points `DATABASE_URL` at `TEST_DATABASE_URL` (default `localhost:5433/email_automation_test`) before importing the app.
- Gemini is never called in tests. Use `FakeAnalyzer` (processor/API tests) or `FakeModels` (agent tests).
- Inside Docker: `docker compose exec backend pytest`.

## Smoke test
```bash
KEY=$(grep ^API_KEY backend/.env | cut -d= -f2)
curl -s -X POST localhost:8000/api/v1/emails/process -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"message_id":"smoke-1","subject":"Interview","body":"Please confirm Tuesday 10:00","sender":"hr@example.com","received_at":"2026-10-14T09:00:00Z"}'
```
- Without a valid `GEMINI_API_KEY` this returns 500 `LLM_NOT_CONFIGURED` or `LLM_REQUEST_REJECTED`, and the record is marked FAILED. That's expected.
- Repeat the request to see the dedupe path.

## Migrations
The model lives in `backend/app/db/models.py`. When you change it, add a hand-written revision in `backend/alembic/versions/` (`NNNN_description.py`) and keep the two in sync. Tests use `Base.metadata.create_all`, so a missing migration won't fail tests. Check it with `alembic upgrade head` against the dev DB.
