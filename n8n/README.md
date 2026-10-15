# n8n ↔ Backend Contract

n8n handles Gmail retrieval, scheduling and Telegram. The backend decides everything else.
Put sanitized workflow exports in this folder (strip credential IDs and tokens before committing).

## Workflow outline

1. **Schedule Trigger**: every 12 hours.
2. **Gmail → Get Many**: filter by unread and/or `after:` the last successful run. Use the Gmail message `id` as `message_id`.
3. **Loop over items**: one HTTP call per email.
4. **HTTP Request**: `POST {{BACKEND_URL}}/api/v1/emails/process`
   - Header `X-API-Key`: use an n8n credential (Header Auth), never a literal in the node.
   - Body:
     ```json
     {
       "message_id": "{{$json.id}}",
       "subject": "{{$json.subject}}",
       "body": "{{$json.text}}",
       "sender": "{{$json.from}}",
       "received_at": "{{$json.date}}"
     }
     ```
     `received_at` must be ISO-8601 **with timezone** (e.g. `{{ $json.date.toISO() }}` or a `Z`-suffixed string).
     Extra fields are ignored and `null` subject/body are accepted, so forwarding the whole Gmail item is fine.
   - Settings: "Never Error" off. Turn on Retry On Fail (3 tries, 5s) only for 5xx/409. See the table below.
5. **Switch on response**:

| Condition | Do |
|---|---|
| `action == "notify"` and `notification_status != "SENT"` | Telegram message → step 6 |
| `action == "review"` | optional Telegram "needs review" message (no callback needed) |
| `action == "ignore"` | nothing |

6. **After Telegram**: `POST /api/v1/emails/{message_id}/notification` with
   `{"delivered": true}`, or `{"delivered": false, "error": "<telegram error>"}` on failure.
   The backend marks the notification `SENT` only after this confirmation. On the next run a
   duplicate email comes back with `already_processed: true`:
   - If `notification_status` is `SENT`, skip it.
   - If it is `PENDING` or `FAILED`, send Telegram again.

## Telegram message template
`subject` and `sender` are echoed back in the `/process` response (stored at rest, so duplicates return them too).
Send as **plain text**: no `parse_mode` (Markdown/HTML). `summary` is LLM output derived from untrusted email text.
```
📧 {{subject}}
From: {{sender}}
Category: {{category}} | Priority: {{priority}}
{{summary}}
```

## Error handling

All errors use `{"error": {"code", "message", "retryable", "message_id"?}}`.

| HTTP | code | n8n should |
|---|---|---|
| 401 | UNAUTHORIZED | stop, alert: wrong API key |
| 422 | VALIDATION_ERROR | route to error branch (bad mapping), don't retry |
| 409 | IN_PROGRESS | retry later (another run is processing it) |
| 409 | MAX_ATTEMPTS_EXCEEDED | alert; investigate, then `POST /api/v1/emails/{id}/retry` and re-run |
| 502 | LLM_UNAVAILABLE | retry with backoff (`retryable: true`) |
| 500 | LLM_REQUEST_REJECTED / LLM_NOT_CONFIGURED | alert: config problem, don't retry |
| 500 | INTERNAL_ERROR | retry a few times, then alert |

Rule of thumb: retry only when `error.retryable` is `true`.

## Reliability notes
- Re-sending the same email is always safe. The backend dedupes on `message_id`, so a failed n8n run can simply be repeated.
- Don't mark Gmail messages as read until after the backend responds 200. That way a failed run never skips mail.
