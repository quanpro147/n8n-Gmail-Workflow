---
name: agent-tool
description: Add or modify a tool the Gemini email agent can call (like lookup_email_history), or change the agent's prompt, phases or retry behavior. Use for any change to backend/app/services/agent_service.py or backend/app/tools/.
---

# Gemini agent tools

The agent is in `backend/app/services/agent_service.py` (`GeminiAnalyzer`) and makes two calls per email:
1. **Tool phase**: function declarations enabled, `automatic_function_calling` disabled, at most `MAX_TOOL_ROUNDS` (2) rounds. Tools run in our code.
2. **Structured phase**: no tools; `response_mime_type=application/json` with `response_schema=AgentAnalysis`. Invalid JSON gets one re-ask, then the analysis is `None` and the action becomes `review`.

Gemini doesn't reliably combine tools and a JSON schema in one request, which is why there are two phases.

## Adding a tool, safely
1. Write the function in `backend/app/tools/<name>.py`. It must be **read-only** and return a small `dict` of metadata, never email bodies or secrets.
2. **Bind its arguments in our code, not the model's.** For example, `lookup_email_history` takes no model arguments and is closed over the current sender in `EmailProcessor._history_lookup_for`. If the model must supply arguments, validate them with a Pydantic model and clamp limits.
3. Register a `types.FunctionDeclaration` in `GeminiAnalyzer.__init__` (`_tool_config`) and dispatch it in `_execute_tool`. Unknown names must keep returning `{"error": "Tool not permitted"}` without executing anything.
4. Pass the callable in through `analyze(...)` the way `history_lookup` is passed (dependency injection keeps it testable).
5. Tell the model when the tool is worth calling in its description. Don't force tool use.
6. Tests in `backend/tests/test_agent_service.py` use `FakeModels` and `tool_call_response(...)`. Cover: it executes, the result goes back to the model, the round cap holds, and a disallowed name is rejected.

## Prompt-injection rules (keep them)
- The email is wrapped in `<email>…</email>` and `render_email` neutralizes embedded delimiters.
- `SYSTEM_INSTRUCTION` says the email content is untrusted data.
- No tool may write data, send messages, or reach other users' data.

## Retries
`_generate` uses tenacity: retry only `is_transient` errors (httpx timeout/transport, `ServerError`, `ClientError` 408/429) up to `LLM_MAX_RETRIES`, with exponential backoff.
- Exhausted transient retries → `AnalysisError(LLM_UNAVAILABLE, transient=True)` → HTTP 502.
- Other API errors → `LLM_REQUEST_REJECTED` → HTTP 500, not retryable.
