import pytest

from tests.conftest import AUTH, email_payload

PROCESS_URL = "/api/v1/emails/process"


def test_health_reports_database_up(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "up"}


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong-key-wrong-key"}])
def test_process_rejects_missing_or_wrong_api_key(client, headers):
    response = client.post(PROCESS_URL, json=email_payload(), headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    "payload",
    [
        email_payload(message_id=""),
        email_payload(message_id="bad id with spaces"),
        email_payload(sender="not-an-email"),
        email_payload(received_at="2026-10-14T09:00:00"),  # naive datetime
        email_payload(received_at="yesterday"),
        {k: v for k, v in email_payload().items() if k != "message_id"},
    ],
)
def test_process_rejects_malformed_payload(client, fake_analyzer, payload):
    response = client.post(PROCESS_URL, json=payload, headers=AUTH)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert all("input" not in detail for detail in error["details"])
    assert fake_analyzer.calls == 0


def test_payload_is_lenient_where_rejection_would_drop_mail(client, fake_analyzer):
    payload = email_payload(
        message_id="<CA+abc$!/~{x}@mail.gmail.com>",
        subject=None,
        body="x" * 300_000,
        threadId="t-1",  # extra n8n fields are ignored
    )

    response = client.post(PROCESS_URL, json=payload, headers=AUTH)

    assert response.status_code == 200
    assert fake_analyzer.calls == 1


def test_validation_error_does_not_echo_email_body(client):
    secret_body = "SECRET-BODY-CONTENT"
    response = client.post(
        PROCESS_URL, json=email_payload(body=secret_body, sender="nope"), headers=AUTH
    )

    assert response.status_code == 422
    assert secret_body not in response.text


def test_process_new_email_returns_completed_result(client, fake_analyzer):
    response = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "message_id": "msg-001",
        "subject": "Interview invitation",
        "sender": "Recruiter <recruiter@example.com>",
        "status": "COMPLETED",
        "category": "career",
        "priority": "high",
        "summary": "Invitation to an AI Engineer interview.",
        "action": "notify",
        "reason": "Requires the recipient to respond.",
        "notification_status": "PENDING",
        "already_processed": False,
    }
    assert fake_analyzer.calls == 1


def test_duplicate_returns_existing_result_without_llm_call(client, fake_analyzer):
    first = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)
    second = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    assert second.status_code == 200
    assert second.json() == {**first.json(), "already_processed": True}
    assert fake_analyzer.calls == 1


def test_get_email_record(client):
    client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    response = client.get("/api/v1/emails/msg-001", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "Interview invitation"
    assert body["attempt_count"] == 1
    assert "body" not in body


def test_get_unknown_email_returns_404(client):
    response = client.get("/api/v1/emails/does-not-exist", headers=AUTH)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
