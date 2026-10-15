from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ApiError
from app.db.models import EmailRecord
from app.schemas.email import Category, EmailIn, Priority
from app.services.agent_service import AnalysisError
from tests.conftest import AUTH, FakeAnalyzer, email_payload, make_analysis

PROCESS_URL = "/api/v1/emails/process"


def make_email(**overrides) -> EmailIn:
    return EmailIn(**email_payload(**overrides))


def test_transient_llm_failure_marks_failed_and_returns_502(client, fake_analyzer, session):
    fake_analyzer.error = AnalysisError("LLM_UNAVAILABLE", "down", transient=True)

    response = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": "LLM_UNAVAILABLE", "message": "down", "retryable": True, "message_id": "msg-001",
    }
    record = session.query(EmailRecord).one()
    assert record.status == "FAILED"
    assert record.error_code == "LLM_UNAVAILABLE"


def test_permanent_llm_failure_is_not_retryable(client, fake_analyzer):
    fake_analyzer.error = AnalysisError("LLM_REQUEST_REJECTED", "bad key", transient=False)

    response = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    assert response.status_code == 500
    assert response.json()["error"]["retryable"] is False


def test_failed_email_can_be_retried_and_succeeds(client, fake_analyzer):
    fake_analyzer.error = AnalysisError("LLM_UNAVAILABLE", "down", transient=True)
    client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    fake_analyzer.error = None
    response = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["already_processed"] is False
    record = client.get("/api/v1/emails/msg-001", headers=AUTH).json()
    assert record["attempt_count"] == 2
    assert record["error_code"] is None


def test_max_attempts_blocks_processing_until_retry_endpoint(client, fake_analyzer):
    fake_analyzer.error = AnalysisError("LLM_UNAVAILABLE", "down", transient=True)
    for _ in range(3):  # MAX_PROCESS_ATTEMPTS default
        client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    blocked = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "MAX_ATTEMPTS_EXCEEDED"
    assert fake_analyzer.calls == 3

    reset = client.post("/api/v1/emails/msg-001/retry", headers=AUTH)
    assert reset.status_code == 200

    fake_analyzer.error = None
    response = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"


def test_retry_endpoint_rejects_non_failed_records(client):
    client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    response = client.post("/api/v1/emails/msg-001/retry", headers=AUTH)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOT_RETRYABLE"


def test_retry_endpoint_unknown_message_returns_404(client):
    response = client.post("/api/v1/emails/unknown/retry", headers=AUTH)

    assert response.status_code == 404


def test_invalid_model_output_results_in_review(client, fake_analyzer):
    fake_analyzer.return_none = True

    response = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "COMPLETED"
    assert body["action"] == "review"
    assert body["notification_status"] == "NOT_REQUIRED"
    assert body["category"] is None


def test_low_priority_promotion_is_ignored(client, fake_analyzer):
    fake_analyzer.analysis = make_analysis(category=Category.PROMOTION, priority=Priority.LOW)

    response = client.post(PROCESS_URL, json=email_payload(), headers=AUTH)

    assert response.json()["action"] == "ignore"
    assert response.json()["notification_status"] == "NOT_REQUIRED"


def test_unexpected_error_marks_record_failed(make_processor, session):
    processor = make_processor(analyzer=FakeAnalyzer(error=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        processor.process(make_email())

    record = session.query(EmailRecord).one()
    assert record.status == "FAILED"
    assert record.error_code == "INTERNAL_ERROR"


def test_fresh_processing_record_returns_in_progress(make_processor, session):
    now = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    stuck = FakeAnalyzer(error=SystemExit())  # simulate a worker dying mid-analysis
    with pytest.raises(SystemExit):
        make_processor(clock=lambda: now, analyzer=stuck).process(make_email())

    with pytest.raises(ApiError) as exc_info:
        make_processor(clock=lambda: now + timedelta(minutes=1)).process(make_email())

    assert exc_info.value.code == "IN_PROGRESS"
    assert exc_info.value.retryable is True


def test_stale_processing_record_is_reclaimed(make_processor, fake_analyzer):
    now = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    stuck = FakeAnalyzer(error=SystemExit())
    with pytest.raises(SystemExit):
        make_processor(clock=lambda: now, analyzer=stuck).process(make_email())

    later = now + timedelta(minutes=16)  # STALE_PROCESSING_MINUTES default is 15
    response = make_processor(clock=lambda: later).process(make_email())

    assert response.status.value == "COMPLETED"
    assert fake_analyzer.calls == 1


def test_history_tool_returns_only_same_sender_metadata(make_processor, fake_analyzer):
    make_processor().process(make_email(message_id="old-1", subject="First interview"))
    make_processor().process(
        make_email(message_id="other", sender="someone@else.com", subject="Unrelated")
    )
    fake_analyzer.use_history = True

    make_processor().process(make_email(message_id="new-1", sender="RECRUITER@example.com"))

    history = fake_analyzer.history_results[-1]
    assert history["count"] == 1
    assert history["records"][0]["category"] == "career"
    # Subjects are attacker-controlled text, so they are deliberately not returned.
    assert set(history["records"][0]) == {"category", "priority", "action", "received_at"}


class TakeoverAnalyzer(FakeAnalyzer):
    """While 'thinking', lets another worker reclaim and finish the same email."""

    def __init__(self, on_analyze, **kwargs) -> None:
        super().__init__(**kwargs)
        self._on_analyze = on_analyze

    def analyze(self, email, history_lookup):
        self._on_analyze()
        return super().analyze(email, history_lookup)


def test_superseded_worker_does_not_overwrite_newer_result(make_processor, session):
    now = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    later = now + timedelta(minutes=16)
    worker_b = make_processor(
        clock=lambda: later,
        analyzer=FakeAnalyzer(make_analysis(category=Category.PROMOTION, priority=Priority.LOW)),
    )
    slow_a = TakeoverAnalyzer(
        lambda: worker_b.process(make_email()),
        analysis=make_analysis(category=Category.CAREER, priority=Priority.HIGH),
    )

    response = make_processor(clock=lambda: now, analyzer=slow_a).process(make_email())

    assert response.already_processed is True
    assert response.action.value == "ignore"  # worker B's result survives
    record = session.query(EmailRecord).one()
    assert record.attempt_count == 2
    assert record.action == "ignore"


def test_superseded_worker_failure_does_not_mark_record_failed(make_processor, session):
    now = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    worker_b = make_processor(clock=lambda: now + timedelta(minutes=16))
    slow_a = TakeoverAnalyzer(
        lambda: worker_b.process(make_email()),
        error=AnalysisError("LLM_UNAVAILABLE", "down", transient=True),
    )

    with pytest.raises(ApiError):
        make_processor(clock=lambda: now, analyzer=slow_a).process(make_email())

    assert session.query(EmailRecord).one().status == "COMPLETED"


def test_stale_record_at_max_attempts_becomes_failed_and_retryable(client, make_processor):
    now = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    failing = FakeAnalyzer(error=AnalysisError("LLM_UNAVAILABLE", "down", transient=True))
    for _ in range(2):
        with pytest.raises(ApiError):
            make_processor(clock=lambda: now, analyzer=failing).process(make_email())
    with pytest.raises(SystemExit):  # third (last) attempt crashes mid-flight
        make_processor(clock=lambda: now, analyzer=FakeAnalyzer(error=SystemExit())).process(
            make_email()
        )

    with pytest.raises(ApiError) as exc_info:
        make_processor(clock=lambda: now + timedelta(minutes=16)).process(make_email())

    assert exc_info.value.code == "MAX_ATTEMPTS_EXCEEDED"
    record = client.get("/api/v1/emails/msg-001", headers=AUTH).json()
    assert record["status"] == "FAILED"
    assert record["error_code"] == "STALE_MAX_ATTEMPTS"
    assert client.post("/api/v1/emails/msg-001/retry", headers=AUTH).status_code == 200
