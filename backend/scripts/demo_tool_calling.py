"""Live demo: the agent decides to call `lookup_email_history`, served by the MCP server.

    docker compose up -d --build
    cd backend && python -m scripts.demo_tool_calling

1. Seeds history: three emails from one (synthetic) recruiter go through the real
   `/process` pipeline and are stored as COMPLETED.
2. Sends a vague follow-up from the same sender. Its priority depends on how earlier
   emails were handled, so the model should call the history tool, which the backend
   serves through the MCP server's `search_email_history`.
3. Prints the command that shows the correlated backend + MCP log lines.
"""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import get_settings

SENDER = "Recruiter <talent@demo-corp.example>"
SEED_EMAILS = (
    (
        "Interview invitation: AI Engineer Intern",
        "Hi, we'd like to invite you to a technical interview for the AI Engineer Intern role. "
        "Please reply with two time slots that work for you this week.",
    ),
    (
        "Interview scheduled: Thursday 10:00",
        "Thanks for your reply. Your interview is confirmed for Thursday 10:00 (online). "
        "Please confirm you can attend.",
    ),
    (
        "Offer details and next steps",
        "Congratulations! We'd like to make you an offer. Please review the attached terms and "
        "let us know your decision by Monday.",
    ),
)
FOLLOW_UP = (
    "Following up",
    "Hi again, just following up on my previous messages. Could you get back to me when you can?",
)


def post_email(
    client: httpx.Client, message_id: str, subject: str, body: str, received_at: datetime
) -> dict:
    payload = {
        "message_id": message_id,
        "subject": subject,
        "body": body,
        "sender": SENDER,
        "received_at": received_at.isoformat(),
    }
    response = client.post("/api/v1/emails/process", json=payload)
    data = response.json()
    if response.status_code != 200:
        raise SystemExit(f"{message_id}: HTTP {response.status_code} {data}")
    print(
        f"  {message_id}: {data['category']}/{data['priority']} -> action={data['action']} "
        f"({data['notification_status']})"
    )
    return data


def main(argv: Sequence[str] | None = None) -> str:
    parser = argparse.ArgumentParser(description="Tool-calling demo against a running backend.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args(argv)

    api_key = get_settings().api_key.get_secret_value()
    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    first_received = datetime.now(UTC) - timedelta(days=3)
    with httpx.Client(base_url=args.base_url, headers={"X-API-Key": api_key}, timeout=180) as client:
        print(f"Seeding history for {SENDER}:")
        for index, (subject, body) in enumerate(SEED_EMAILS, start=1):
            received = first_received + timedelta(hours=index)
            post_email(client, f"demo-{run_id}-{index}", subject, body, received)

        follow_up_id = f"demo-{run_id}-followup"
        print("Follow-up (should trigger lookup_email_history):")
        post_email(client, follow_up_id, *FOLLOW_UP, datetime.now(UTC))

    print(
        "\nCorrelated logs (backend agent -> MCP client -> MCP server):\n"
        f'  docker compose logs backend mcp | grep "{follow_up_id}"'
    )
    return follow_up_id


if __name__ == "__main__":
    main()
