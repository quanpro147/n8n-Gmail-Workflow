# Design decisions

Record new decisions here with **why**, newest last. Keep entries short.

- **Gemini via `google-genai`** (2026-10-14). The user chose it; it has a free tier for a student project. The model is configurable (`GEMINI_MODEL`, default `gemini-2.5-flash`).
- **Two-phase agent (tools, then JSON schema)**. Gemini doesn't reliably mix function calling with `response_schema`. It costs one extra call per email, which is acceptable at a 12h batch cadence.
- **History tool takes no model arguments**. It is bound to the current sender, so a prompt-injected email can't ask for another sender's history.
- **Sync SQLAlchemy 2.0 + psycopg 3**. Simpler than async. FastAPI runs sync endpoints in a threadpool, and the load is tiny.
- **Idempotency in the DB**. `INSERT ... ON CONFLICT DO NOTHING` plus `SELECT ... FOR UPDATE`. The claim commits *before* the LLM call so no lock is held during the slow call. A stale `PROCESSING` record (crashed worker) is reclaimed after `STALE_PROCESSING_MINUTES`.
- **The notification callback endpoint** (`/notification`) is not in the SDP's endpoint list. It was added because the SDP requires "do not mark delivered before Telegram confirms" and "no repeated notifications". The backend can only know about delivery if n8n reports it.
- **Action enum is `notify|ignore|review` only**. The SDP mentioned `retry`, but transient failures are expressed as HTTP 502 with `retryable: true`, so a `retry` action would be redundant.
- **Promotion rule comes before high-priority**. Marketing emails often look urgent, so promotions never notify.
- **Invalid LLM output → COMPLETED with `review`**, not FAILED. An uncertain analysis is a result. Only infrastructure errors mark FAILED.
- **Postgres host port 5433**. Another Postgres container on the dev machine already holds 5432.
- **Tests use a real Postgres** (`email_automation_test`), not SQLite, because the claim logic uses Postgres-specific `ON CONFLICT` and row locks.
- **Fencing token on writes** (from code review). `attempt_count` at claim time is the token. `complete`/`fail` only write if the row is still `PROCESSING` with that token. Otherwise the slow worker's result is discarded: it returns the newer COMPLETED result, or 409 IN_PROGRESS. **Why:** without it, a worker whose LLM call outlived the stale timeout would overwrite the worker that reclaimed the row.
- **Stale `PROCESSING` at max attempts → `FAILED` (`STALE_MAX_ATTEMPTS`)**, so `/retry` can recover it. Before this it was stuck forever.
- **Locked selects use `populate_existing`**. Otherwise `FOR UPDATE` returns stale identity-map state when the row was already loaded in the session.
- **`find_history` returns frozen `HistoryEntry` snapshots and ends its transaction**. This avoids sitting idle in a transaction during the next LLM call (rollback expires ORM objects, hence the snapshots).
- **History tool output has no `subject`**. Subjects are attacker-controlled text that would reach the model outside the `<email>` delimiters (second-order injection).
- **Structured phase gets tool results as plain JSON text**, not function_call/function_response parts, because that request declares no tools. Not yet verified against the live Gemini API.
- **`EmailIn` is lenient where rejecting would silently drop mail.** It ignores extra fields, maps `null` subject/body to "", truncates them instead of rejecting, and accepts any non-whitespace `message_id`. A 422 is non-retryable, so an email rejected that way would never be analyzed. `received_at` must still be timezone-aware.
- **Privacy (open item)**. Email content goes to Google Gemini, and the free tier may use prompts for training. Use a paid key for real mailboxes.
- **Accepted:** a daily-quota 429 is retried like a transient error. That costs a few seconds and one processing attempt, which is fine at this volume.
- **Default model `gemini-flash-lite-latest`** (2026-10-16). `gemini-2.5-flash` free tier allows only 20 requests/day (~10 emails), and `gemini-2.5-flash-lite` now returns 404. The `-latest` alias avoids hard-coding a model that gets retired; the trade-off is that the model can change underneath us.
- **Fallback LLM via an OpenAI-compatible API** (2026-10-16). The user has Groq and OpenRouter keys; both speak the OpenAI API, so one `openai`-SDK analyzer covers either (`FALLBACK_LLM_BASE_URL`). Claude API was considered, but a Claude Pro subscription doesn't include API access. The fallback runs on *any* Gemini `AnalysisError`, including rejected requests (e.g. a retired model). When both fail, the transient error wins so n8n retries. Retries are left to the `openai` client (`max_retries`). The fallback also sends email content to a third party (same privacy caveat as Gemini).
- **Fallback model `openai/gpt-oss-120b` on Groq** (2026-10-16). Groq retired `llama-3.3-70b-versatile` (404 `model_not_found`); `gpt-oss-120b` supports tool calling and passed a live smoke test (tool phase + structured JSON). Check `GET /models` on the provider if the fallback starts returning 404.
