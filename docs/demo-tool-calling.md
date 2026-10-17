# Demo: real tool calling over MCP

This shows, from logs alone, that tools were really called:

- **A. The email agent (Gemini) as an MCP client.** On its own, the model decides to call `lookup_email_history`. The backend serves that call through the MCP server's `search_email_history`.
- **B. An external MCP client (Claude Code).** It answers a natural-language question by calling the MCP server's tools.

Both runs below are from 2026-10-17 against the Docker Compose stack, with synthetic emails only.

## Setup

```bash
# backend/.env: API_KEY, GEMINI_API_KEY (+ optional FALLBACK_LLM_API_KEY), MCP_API_KEY
docker compose up -d --build        # db + backend (:8000) + mcp (:8001)

# Claude Code as an MCP client (one-time)
claude mcp add --transport http email-records http://localhost:8001/mcp \
  --header "Authorization: Bearer $MCP_API_KEY"
```

You can use [MCP Inspector](https://github.com/modelcontextprotocol/inspector) instead of Claude Code. Run `npx @modelcontextprotocol/inspector` and connect with:

- transport: Streamable HTTP
- URL: `http://localhost:8001/mcp`
- header: `Authorization: Bearer <MCP_API_KEY>`

### What makes the logs joinable

| Log line | Service | Proves |
|---|---|---|
| `Agent tool call` (`tool_name`, `round`, `provider`) | backend | the **model** requested the tool (logged before it runs) |
| `mcp search_email_history` (`tool_name`, `filters`, `duration_ms`) | mcp | the MCP server executed the tool |
| `MCP history lookup` (`outcome: N records`) | backend | the result came back to the agent |
| `Email processed` (`tool_calls`, tokens, `analysis_ms`) | backend | the final analysis used the tool |

While it processes an email, the backend sets `correlation_id` to the email's `message_id` and sends it to the MCP server as `X-Correlation-ID`.

- Every line above carries the same `correlation_id`, in both containers.
- `filters` lists filter *names* only. Sender addresses are not logged.

## A. The email agent calls the tool through MCP

```bash
cd backend && python -m scripts.demo_tool_calling
```

The script:
1. Sends three emails from one recruiter (interview invite, schedule, offer) through `/process`, so that sender has history.
2. Sends a vague follow-up from the same sender. Whether that follow-up matters depends on the earlier emails.

```
Seeding history for Recruiter <talent@demo-corp.example>:
  demo-20261017153007-1: career/high -> action=notify (PENDING)
  demo-20261017153007-2: career/high -> action=notify (PENDING)
  demo-20261017153007-3: career/high -> action=notify (PENDING)
Follow-up (should trigger lookup_email_history):
  demo-20261017153007-followup: career/medium -> action=notify (PENDING)

Correlated logs (backend agent -> MCP client -> MCP server):
  docker compose logs backend mcp | grep "demo-20261017153007-followup"
```

These are the matching log lines from `docker compose logs --no-log-prefix backend mcp | grep demo-20261017153007-followup`. They are in time order, and the uvicorn/httpx transport lines are omitted:

```json
{"ts": "2026-10-17T15:29:50.162005+00:00", "logger": "app.services.email_processor", "msg": "Claim evaluated", "correlation_id": "demo-20261017153007-followup", "message_id": "demo-20261017153007-followup", "outcome": "claimed"}
{"ts": "2026-10-17T15:29:51.147546+00:00", "logger": "app.services.agent_service", "msg": "Agent tool call", "correlation_id": "demo-20261017153007-followup", "message_id": "demo-20261017153007-followup", "tool_name": "lookup_email_history", "round": 1, "provider": "gemini"}
{"ts": "2026-10-17T15:29:51.287195+00:00", "logger": "mcp_server.server", "msg": "mcp search_email_history", "correlation_id": "demo-20261017153007-followup", "tool_name": "search_email_history", "outcome": "3 records", "duration_ms": 84.4, "filters": ["sender_address", "status"]}
{"ts": "2026-10-17T15:29:51.387660+00:00", "logger": "app.tools.mcp_history", "msg": "MCP history lookup", "correlation_id": "demo-20261017153007-followup", "outcome": "3 records"}
{"ts": "2026-10-17T15:29:53.554598+00:00", "logger": "app.services.email_processor", "msg": "Email processed", "correlation_id": "demo-20261017153007-followup", "message_id": "demo-20261017153007-followup", "category": "career", "priority": "medium", "action": "notify", "tool_calls": ["lookup_email_history"], "provider": "gemini", "model": "gemini-flash-lite-latest", "llm_calls": 3, "llm_retries": 0, "prompt_tokens": 1538, "completion_tokens": 174, "total_tokens": 1712, "analysis_ms": 3384.5}
```

What the log lines show:
- **Round 1 of the tool phase.** Gemini answered with a `lookup_email_history` function call instead of `READY`.
- **What the backend sent.** It called the MCP server with `sender_address` and `status` only. It chose the sender itself, so the model can't pick another sender.
- **What came back.** The MCP server returned the 3 seeded records in 84 ms.
- **How the history was used.** The analysis cost 3 LLM calls: tool call, `READY`, then the structured JSON. On their own, the follow-up's words ("could you get back to me") don't say what the email is about. With the history, the agent classified it `career/medium` with `needs_response`, and rule `important_needs_response` → `notify`.

The model **did not** call the tool for the three seed emails (`tool_calls: []`, about 830 tokens and 2 LLM calls each). Those emails are clear on their own. So the tool is used selectively, not on every email, which is what the system prompt asks for.

## B. An external MCP client: Claude Code

The question asked in Claude Code: *"Which emails from talent@demo-corp.example were notified, and what was the latest one about?"*

Claude Code made two tool calls.

1. `search_email_history(sender="talent@demo-corp.example", action="notify", limit=10)` returned 4 records, all `career`, newest first: `demo-…-followup` (medium) and `demo-…-3/-2/-1` (high), all `notification_status: PENDING`.
2. `get_email_details(message_id="demo-20261017153007-followup")` returned the following (abridged):
   ```json
   {"subject": "Following up", "category": "career", "priority": "medium", "action": "notify",
    "summary": "A recruiter is following up on previous messages and asking for a reply.",
    "confidence": 0.95, "decision_rule": "important_needs_response", "notification_status": "PENDING"}
   ```

Claude Code's answer: *four emails from that recruiter were marked notify: an interview invitation, a schedule confirmation, an offer, and the latest one, a follow-up asking for a reply (medium priority). None has been confirmed as sent to Telegram yet (`PENDING`).*

The MCP server logged each call (`docker compose logs --no-log-prefix mcp`):

```json
{"ts": "2026-10-17T15:30:17.716161+00:00", "logger": "mcp_server.server", "msg": "mcp search_email_history", "tool_name": "search_email_history", "outcome": "4 records", "duration_ms": 21.2, "filters": ["sender_address", "action"]}
{"ts": "2026-10-17T15:30:21.317282+00:00", "logger": "mcp_server.server", "msg": "mcp get_email_details", "tool_name": "get_email_details", "outcome": "found", "duration_ms": 4.3}
```

These lines have no `correlation_id`, because Claude Code doesn't send `X-Correlation-ID`. Two things tell them apart from the backend agent's calls:
- The filters: the agent always uses `sender_address` + `status`.
- The tools: the agent never calls `get_email_details`.

## Reproduce

1. Run `docker compose up -d --build`.
2. Run `cd backend && python -m scripts.demo_tool_calling`. Each run uses new `demo-<timestamp>-*` IDs, so dedupe never short-circuits it.
3. Run `docker compose logs --no-log-prefix backend mcp | grep <follow-up id>`.
4. In Claude Code (or MCP Inspector), ask about `talent@demo-corp.example`, then run `docker compose logs --no-log-prefix mcp | grep mcp_server.server`.

Tool use is the model's choice. The benchmark ([evaluation.md](evaluation.md)) measures how often it calls the tool when history matters. If a run shows `tool_calls: []` for the follow-up, the model judged history unnecessary that time, and the logs say so just as clearly.
