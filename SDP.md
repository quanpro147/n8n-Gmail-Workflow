# Project: AI Email Automation Workflow with n8n, FastAPI, PostgreSQL, and AI Agent

## 1. Project Objective

Build an automated email-processing system that retrieves emails from Gmail, uses an AI Agent/LLM to analyze and classify them, stores processing results, and sends Telegram notifications when necessary.

The project should demonstrate practical skills relevant to an AI Automation Intern role:
- n8n workflow automation and scheduled execution.
- Python and FastAPI backend development.
- REST API integration and JSON data exchange.
- LLM integration and AI Agent tool usage.
- PostgreSQL persistence and idempotent processing.
- Error handling, logging, retries, and workflow reliability.

## 2. System Architecture

**n8n → FastAPI Backend → AI Agent/LLM → PostgreSQL → FastAPI Response → n8n → Telegram**

Responsibilities should be separated clearly.

### n8n — Workflow Orchestration
- Retrieve emails from Gmail.
- Run automatically every 12 hours.
- Filter emails according to configurable criteria.
- Send email data to the backend using an HTTP Request node.
- Interpret the backend response.
- Send Telegram notifications only when the backend indicates that notification is necessary.
- Handle workflow-level errors and route failures to an appropriate error-handling path.

### FastAPI — Business Logic and API Layer
- Expose REST endpoints for health checks and email processing.
- Validate incoming requests with Pydantic.
- Prevent duplicate processing using Gmail message IDs.
- Coordinate AI Agent/LLM execution.
- Apply business rules to determine email category, priority, and recommended action.
- Persist processing results and statuses in PostgreSQL.
- Return structured JSON responses to n8n.
- Provide appropriate error responses and logging.

### PostgreSQL — Persistent Storage
Store email processing records, including:
- Gmail message ID, with a unique constraint.
- Email subject and relevant metadata.
- Category, priority, summary, and recommended action.
- Processing status.
- Error details when processing fails.
- Created and updated timestamps.

Do not store full email bodies unless necessary. Avoid storing sensitive information unnecessarily.

## 3. End-to-End Workflow

### Step 1 — Retrieve Emails
n8n retrieves emails from Gmail on a 12-hour schedule.

- Support processing multiple emails in one execution.
- Filter emails according to configurable rules, such as unread emails or emails received after the last successful run.
- Use Gmail message IDs as stable identifiers.
- Avoid relying exclusively on the most recent email.
- Ensure that a failed execution does not permanently skip unprocessed emails.

### Step 2 — Send Email Data to FastAPI
For each eligible email, n8n sends a POST request to:

`POST /api/v1/emails/process`

Example request:

```json
{
  "message_id": "gmail-message-id",
  "subject": "Interview invitation",
  "body": "Email content here",
  "sender": "recruiter@example.com",
  "received_at": "2026-10-14T09:00:00Z"
}
```

The backend should validate the payload and reject malformed requests.

Protect the API using an environment-configured API key or bearer token. Do not hardcode credentials in source code or workflow exports.

### Step 3 — Deduplicate and Check Processing Status
Before calling the LLM, FastAPI checks whether the message has already been processed.

- If successfully processed, return the existing result instead of processing it again.
- If new, create a processing record and continue.
- If a previous attempt failed, allow a controlled retry.
- Use a database unique constraint and appropriate transaction handling to prevent concurrent duplicate processing.

The implementation should distinguish between `PROCESSING`, `COMPLETED`, and `FAILED` states.

### Step 4 — Analyze Email with an AI Agent
The backend invokes an LLM or AI Agent to analyze the email.

The model should return structured data, preferably validated against a Pydantic schema:

```json
{
  "category": "career",
  "priority": "high",
  "summary": "Invitation to an AI Engineer interview.",
  "recommended_action": "notify",
  "reason": "The email requires the recipient to respond or take action."
}
```

Suggested categories:
- `career`
- `education`
- `finance`
- `personal`
- `promotion`
- `other`

Suggested priorities:
- `high`
- `medium`
- `low`

Requirements:
- Require valid structured output.
- Handle invalid JSON or unexpected model responses.
- Apply deterministic business rules after model inference.
- Treat email content as untrusted input; do not allow instructions embedded in an email to override system rules or trigger unauthorized actions.
- Keep the AI Agent focused on analysis and controlled tool usage rather than giving it unrestricted access to external systems.

### Step 5 — Add a Meaningful Agent Tool
Implement at least one useful tool to demonstrate agentic behavior.

Possible first tool:
- `lookup_email_history`: query PostgreSQL for relevant previous processing records or related emails.

Alternative:
- `lookup_reference_data`: retrieve approved information from a configured API to help interpret an email.

The Agent should invoke tools only when needed and only through explicitly permitted operations. If a simple classification call is sufficient, do not force tool usage just to label the system an AI Agent.

### Step 6 — Apply Business Rules and Persist Results
FastAPI evaluates the Agent's output and determines the final action.

Example rules:
- High-priority career email requiring a response → `notify`.
- Low-priority promotion email → `ignore`.
- Invalid or uncertain analysis → `review` or `retry`, according to configured rules.

Save the final category, priority, summary, action, and status in PostgreSQL.

Business rules should be implemented in Python rather than relying entirely on the LLM's decision.

### Step 7 — Return a Structured Response
FastAPI returns a consistent JSON response:

```json
{
  "message_id": "gmail-message-id",
  "status": "COMPLETED",
  "category": "career",
  "priority": "high",
  "summary": "Invitation to an AI Engineer interview.",
  "action": "notify",
  "already_processed": false
}
```

For a duplicate successfully processed email, return the existing result with `already_processed: true`.

Define consistent response and error schemas so n8n can reliably interpret them.

### Step 8 — Send Telegram Notification
n8n evaluates the backend response.

- If `action == "notify"`, send a Telegram message containing the email subject, category, priority, summary, and sender.
- If `action == "ignore"`, do not send a notification.
- If `action == "review"`, optionally send a separate review notification.
- If the backend request fails, log the failure and retry safely according to a configured policy.

Do not send Telegram notifications for every email.

## 4. API Endpoints

Implement these endpoints initially:

### `GET /health`
Check whether the FastAPI application is running.

### `POST /api/v1/emails/process`
Validate, deduplicate, analyze, persist, and return the result for one email.

Optional future endpoints:
- `GET /api/v1/emails/{message_id}` — retrieve a processing record.
- `POST /api/v1/emails/{message_id}/retry` — retry a failed record under controlled conditions.

## 5. Reliability Requirements

- Use environment variables for secrets and configuration.
- Add request validation, structured logging, and meaningful HTTP status codes.
- Set timeouts for external API calls.
- Retry transient failures with bounded exponential backoff.
- Avoid retrying permanent errors indefinitely.
- Use database transactions and unique constraints for idempotency.
- Ensure retries do not cause duplicate database records or repeated notifications.
- Separate backend processing failures from Telegram delivery failures.
- Do not mark a notification as delivered before Telegram confirms success.
- Avoid logging full email bodies, credentials, or other sensitive data.

## 6. Suggested Project Structure

```text
email-automation/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   └── emails.py
│   │   ├── schemas/
│   │   │   └── email.py
│   │   ├── services/
│   │   │   ├── email_processor.py
│   │   │   ├── agent_service.py
│   │   │   └── notification_rules.py
│   │   ├── tools/
│   │   │   └── email_history.py
│   │   ├── repositories/
│   │   │   └── email_repository.py
│   │   └── core/
│   │       └── config.py
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
├── n8n/
│   └── workflow documentation or sanitized workflow export
├── docker-compose.yml
├── README.md
└── .gitignore
```

Adapt the structure to the existing repository rather than creating duplicate modules unnecessarily.

## 7. Testing Requirements

Include tests for:
- Valid and invalid API requests.
- New email processing.
- Duplicate email handling.
- LLM/API failure and recovery.
- Database persistence.
- Notification decisions for different categories and priorities.
- Telegram notification failure.
- Retry behavior and idempotency.

Use mocked LLM and Telegram responses in automated tests where possible. Keep real API credentials out of tests.

## 8. Implementation Strategy

Implement incrementally:

1. Inspect the existing n8n workflow and repository before changing anything.
2. Preserve the working Gmail-to-LLM-to-Telegram workflow.
3. Build the FastAPI health endpoint and email-processing endpoint.
4. Add PostgreSQL persistence and deduplication.
5. Integrate n8n with FastAPI using HTTP Request.
6. Move reusable analysis and business logic into the backend.
7. Add one meaningful Agent tool.
8. Add bounded retries, error handling, logging, and tests.
9. Document setup, environment variables, API contracts, workflow behavior, and demo instructions in README.md.

Do not introduce unnecessary frameworks, microservices, or infrastructure. Prefer a small, maintainable implementation that demonstrates real API integration, business logic, AI Agent usage, and reliable workflow execution.

## 9. Definition of Done

The project is complete when:
- n8n retrieves and processes multiple eligible emails on a 12-hour schedule.
- Each email is sent to FastAPI through a documented REST API.
- FastAPI validates requests and prevents duplicate processing.
- The Agent/LLM returns validated structured results.
- PostgreSQL stores processing outcomes.
- n8n sends Telegram notifications according to backend decisions.
- Failures can be diagnosed and retried safely.
- Tests cover the main success and failure paths.
- README explains how to run and demonstrate the complete system.