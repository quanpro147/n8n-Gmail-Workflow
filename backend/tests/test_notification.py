from app.schemas.email import Category, Priority
from tests.conftest import AUTH, email_payload, make_analysis

PROCESS_URL = "/api/v1/emails/process"
NOTIFY_URL = "/api/v1/emails/msg-001/notification"


def test_delivery_confirmation_marks_sent(client):
    client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    response = client.post(NOTIFY_URL, json={"delivered": True}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["notification_status"] == "SENT"
    assert response.json()["notified_at"] is not None


def test_telegram_failure_is_recorded_separately_from_processing(client):
    client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    response = client.post(
        NOTIFY_URL, json={"delivered": False, "error": "Telegram 429"}, headers=AUTH
    )

    body = response.json()
    assert body["notification_status"] == "FAILED"
    assert body["status"] == "COMPLETED"
    assert body["notified_at"] is None


def test_failed_notification_can_be_confirmed_later(client):
    client.post(PROCESS_URL, json=email_payload(), headers=AUTH)
    client.post(NOTIFY_URL, json={"delivered": False, "error": "timeout"}, headers=AUTH)

    response = client.post(NOTIFY_URL, json={"delivered": True}, headers=AUTH)

    assert response.json()["notification_status"] == "SENT"


def test_sent_notification_is_never_downgraded(client):
    client.post(PROCESS_URL, json=email_payload(), headers=AUTH)
    client.post(NOTIFY_URL, json={"delivered": True}, headers=AUTH)

    response = client.post(NOTIFY_URL, json={"delivered": False, "error": "late"}, headers=AUTH)

    assert response.json()["notification_status"] == "SENT"


def test_reprocessing_after_sent_tells_n8n_not_to_notify_again(client, fake_analyzer):
    client.post(PROCESS_URL, json=email_payload(), headers=AUTH)
    client.post(NOTIFY_URL, json={"delivered": True}, headers=AUTH)

    response = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    body = response.json()
    assert body["already_processed"] is True
    assert body["action"] == "notify"
    assert body["notification_status"] == "SENT"
    assert fake_analyzer.calls == 1


def test_notification_report_rejected_when_not_required(client, fake_analyzer):
    fake_analyzer.analysis = make_analysis(category=Category.PROMOTION, priority=Priority.LOW)
    client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    response = client.post(NOTIFY_URL, json={"delivered": True}, headers=AUTH)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOTIFICATION_NOT_REQUIRED"


def test_notification_report_for_unknown_message_returns_404(client):
    response = client.post(
        "/api/v1/emails/unknown/notification", json={"delivered": True}, headers=AUTH
    )

    assert response.status_code == 404
