# Glossary

| Term | Meaning |
|---|---|
| `message_id` | Gmail message ID; unique key for idempotency |
| `PROCESSING` | claimed by a request; LLM call in flight (or worker crashed; becomes stale after `STALE_PROCESSING_MINUTES`) |
| `COMPLETED` | analysis + decision stored; re-sends return it with `already_processed: true` |
| `FAILED` | infrastructure/LLM error; retried on next `/process` until `MAX_PROCESS_ATTEMPTS`, then needs `/retry` |
| claim | the atomic step deciding whether a request may process an email (`EmailRepository.claim`) |
| `action` | backend decision: `notify` (send Telegram), `ignore`, `review` (human should look) |
| `notification_status` | `NOT_REQUIRED` (action ≠ notify), `PENDING` (should be sent), `SENT` (n8n confirmed), `FAILED` (n8n reported failure; resend allowed) |
| `decision_rule` | name of the business rule that produced `action` (audit trail) |
| `retryable` | in error responses: whether repeating the same request may succeed |
| tool phase / structured phase | the two Gemini calls per email (see decisions.md) |
