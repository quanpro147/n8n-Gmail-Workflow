# Conventions

## Code layout (backend/app)
- `api/`: thin routes plus `deps.py` (DI wiring). No business logic.
- `services/`:
  - `email_processor` orchestrates.
  - `agent_service` talks to Gemini.
  - `notification_rules` holds pure decision logic.
- `repositories/`: every SQL statement lives here. Each public method commits or rolls back itself.
- `tools/`: agent tools (read-only).
- `schemas/`: Pydantic I/O models and the enums. The enums are the single source for status, action and category strings.

## Errors
- Raise `app.core.errors.ApiError(status, CODE, message, retryable=?, message_id=?)` for anything n8n should see.
- Envelope: `{"error": {"code", "message", "retryable", "message_id"?}}`.
- Validation errors drop the `input` field so email content isn't echoed back.
- Error codes are UPPER_SNAKE and part of the API contract.

## Logging
- JSON to stdout via `app.core.logging`.
- Only keys in `ALLOWED_EXTRA_KEYS` are emitted from `extra=`. Add a key there deliberately, and never log body, credentials or full prompts.

## Testing
- Use pytest with AAA style and descriptive names.
- Use `FakeAnalyzer` for processor/API tests, `FakeModels` plus `text_response`/`tool_call_response` for agent tests, and `make_processor(clock=...)` for time-dependent cases.
- Helpers `email_payload(**overrides)` and `make_analysis(**overrides)` live in `tests/conftest.py`.
- Keep coverage ≥80% (currently ~98%).

## Config
- Every setting lives in `Settings` (`app/core/config.py`) and must also be listed in `backend/.env.example`.
- Blank env values count as unset (`env_ignore_empty`).
