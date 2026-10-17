"""Log fields that tie a processed email to its LLM usage and its MCP tool calls."""

import json
import logging

import pytest

from app.core.logging import JsonFormatter, correlation_id
from app.schemas.email import EmailIn
from app.services.agent_service import AnalysisOutcome, LlmUsage
from tests.conftest import captured_json_logs, email_payload, make_analysis


def make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", None, None)
    record.__dict__.update(extra)
    return record


def test_formatter_adds_correlation_id_only_while_set():
    token = correlation_id.set("msg-42")
    try:
        with_id = json.loads(JsonFormatter().format(make_record()))
    finally:
        correlation_id.reset(token)
    without_id = json.loads(JsonFormatter().format(make_record()))

    assert with_id["correlation_id"] == "msg-42"
    assert "correlation_id" not in without_id


def test_formatter_emits_usage_keys_and_still_drops_unknown_ones():
    record = make_record(total_tokens=321, tool_name="lookup_email_history", body="secret")

    payload = json.loads(JsonFormatter().format(record))

    assert payload["total_tokens"] == 321
    assert payload["tool_name"] == "lookup_email_history"
    assert "body" not in payload


class _UsageAnalyzer:
    def __init__(self) -> None:
        self.seen_correlation_id: str | None = None

    def analyze(self, email, history_lookup) -> AnalysisOutcome:
        self.seen_correlation_id = correlation_id.get()
        return AnalysisOutcome(
            analysis=make_analysis(),
            tool_calls=("lookup_email_history",),
            usage=LlmUsage(calls=3, retries=1, prompt_tokens=400, completion_tokens=90, total_tokens=490),
            provider="gemini",
            model="gemini-test",
        )


def test_processed_log_carries_usage_latency_and_correlation_id(make_processor):
    analyzer = _UsageAnalyzer()
    processor = make_processor(analyzer=analyzer)

    with captured_json_logs("app.services.email_processor") as lines:
        processor.process(EmailIn(**email_payload(message_id="obs-1")))

    processed = next(line for line in lines if line["msg"] == "Email processed")
    assert processed["correlation_id"] == "obs-1"
    assert processed["provider"] == "gemini"
    assert processed["model"] == "gemini-test"
    assert (processed["llm_calls"], processed["llm_retries"]) == (3, 1)
    assert (processed["prompt_tokens"], processed["completion_tokens"]) == (400, 90)
    assert processed["total_tokens"] == 490
    assert processed["analysis_ms"] >= 0
    assert processed["tool_calls"] == ["lookup_email_history"]
    assert analyzer.seen_correlation_id == "obs-1"
    assert correlation_id.get() is None  # reset after processing


def test_correlation_id_is_reset_when_processing_fails(make_processor):
    class Boom:
        def analyze(self, email, history_lookup):
            raise RuntimeError("unexpected")

    processor = make_processor(analyzer=Boom())

    with pytest.raises(RuntimeError):
        processor.process(EmailIn(**email_payload(message_id="obs-2")))

    assert correlation_id.get() is None
