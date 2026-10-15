---
name: n8n-integration
description: Build or change the n8n workflow that calls this backend (Gmail schedule, HTTP Request to /api/v1/emails/process, Telegram, notification callback, error branches). Use when working on n8n nodes, workflow exports, or anything that changes the API contract n8n depends on.
---

# n8n integration

The source of truth for the contract is `n8n/README.md`. Read it first, and update it in the same change whenever the API changes.

## Contract essentials
- Request: `POST /api/v1/emails/process` with `X-API-Key` and `{message_id, subject, body, sender, received_at}`. `received_at` must include a timezone, and unknown fields are rejected (422).
- Response fields n8n branches on: `action` (`notify|ignore|review`), `notification_status` (`NOT_REQUIRED|PENDING|SENT|FAILED`), `already_processed`.
- Notify only when `action=="notify"` and `notification_status != "SENT"`. Then report back with `POST /api/v1/emails/{id}/notification {"delivered": bool, "error"?}`.
- Retry only when `error.retryable` is true (409 IN_PROGRESS, 502 LLM_UNAVAILABLE, 500 INTERNAL_ERROR).

## Treat as breaking changes
Renaming or removing response fields, adding `action` values, or changing status codes or error codes. Additive optional fields are fine.

## Workflow exports
- Commit only sanitized exports to `n8n/` (no credential IDs, tokens or chat IDs). Raw exports should be named `*.raw.json`, which is gitignored.
- The API key belongs in an n8n Header Auth credential. Never put it in a node parameter.

## Local wiring
If n8n runs in Docker on the same machine, reach the backend at `http://host.docker.internal:8000`. Alternatively, add n8n to `docker-compose.yml` and use `http://backend:8000`.
