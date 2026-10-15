from concurrent.futures import ThreadPoolExecutor

from app.core.errors import ApiError
from app.db.models import EmailRecord
from app.schemas.email import EmailIn
from tests.conftest import email_payload


def test_concurrent_requests_for_same_message_call_llm_once(make_processor, fake_analyzer, session):
    fake_analyzer.delay_s = 0.5
    processors = [make_processor() for _ in range(4)]
    email = EmailIn(**email_payload())

    def run(processor):
        try:
            return processor.process(email)
        except ApiError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, processors))

    # Late threads may start after the winner finished: they get the stored result, not 409.
    responses = [r for r in results if not isinstance(r, ApiError)]
    in_progress = [r for r in results if isinstance(r, ApiError)]
    assert fake_analyzer.calls == 1
    assert sum(not r.already_processed for r in responses) == 1
    assert all(err.code == "IN_PROGRESS" for err in in_progress)
    assert session.query(EmailRecord).count() == 1
