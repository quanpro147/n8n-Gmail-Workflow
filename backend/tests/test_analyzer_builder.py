import pytest

from app.core.config import Settings
from app.schemas.email import EmailIn
from app.services.agent_service import AnalysisError, AnalysisOutcome, GeminiAnalyzer
from app.services.analyzer_builder import FallbackAnalyzer, build_analyzer
from app.services.openai_compatible_analyzer import OpenAICompatibleAnalyzer
from tests.conftest import email_payload, make_analysis


class StubAnalyzer:
    def __init__(self, result: AnalysisOutcome | AnalysisError) -> None:
        self._result = result
        self.calls = 0

    def analyze(self, email: EmailIn, history_lookup) -> AnalysisOutcome:
        self.calls += 1
        if isinstance(self._result, AnalysisError):
            raise self._result
        return self._result


def outcome(summary: str) -> AnalysisOutcome:
    return AnalysisOutcome(analysis=make_analysis(summary=summary), tool_calls=())


def transient() -> AnalysisError:
    return AnalysisError("LLM_UNAVAILABLE", "LLM temporarily unavailable", transient=True)


def rejected() -> AnalysisError:
    return AnalysisError("LLM_REQUEST_REJECTED", "LLM rejected the request (HTTP 401)", transient=False)


def email() -> EmailIn:
    return EmailIn(**email_payload())


def settings(**overrides) -> Settings:
    values = {
        "api_key": "x" * 16,
        "gemini_api_key": None,
        "fallback_llm_api_key": None,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_fallback_is_not_called_when_primary_succeeds():
    primary, fallback = StubAnalyzer(outcome("primary")), StubAnalyzer(outcome("fallback"))

    result = FallbackAnalyzer(primary, fallback).analyze(email(), dict)

    assert result.analysis.summary == "primary"
    assert fallback.calls == 0


@pytest.mark.parametrize("primary_error", [transient(), rejected()])
def test_fallback_is_used_when_primary_fails(primary_error):
    primary, fallback = StubAnalyzer(primary_error), StubAnalyzer(outcome("fallback"))

    result = FallbackAnalyzer(primary, fallback).analyze(email(), dict)

    assert result.analysis.summary == "fallback"
    assert primary.calls == 1 and fallback.calls == 1


@pytest.mark.parametrize(
    ("primary_error", "fallback_error", "expected_transient"),
    [
        (transient(), rejected(), True),
        (rejected(), transient(), True),
        (rejected(), rejected(), False),
    ],
)
def test_when_both_fail_a_transient_error_wins(primary_error, fallback_error, expected_transient):
    analyzer = FallbackAnalyzer(StubAnalyzer(primary_error), StubAnalyzer(fallback_error))

    with pytest.raises(AnalysisError) as exc_info:
        analyzer.analyze(email(), dict)

    assert exc_info.value.transient is expected_transient
    assert isinstance(exc_info.value.__cause__, AnalysisError)


def test_build_configures_fallback_client_from_settings(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.analyzer_builder.openai.OpenAI", lambda **kwargs: captured.update(kwargs)
    )

    build_analyzer(
        settings(
            fallback_llm_api_key="f" * 20,
            fallback_llm_base_url="https://openrouter.ai/api/v1",
            llm_timeout_s=12,
            llm_max_retries=3,
        )
    )

    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["timeout"] == 12
    assert captured["max_retries"] == 2  # the SDK counts retries, settings count attempts
    assert captured["api_key"] == "f" * 20


def test_build_uses_gemini_only_when_no_fallback_key():
    analyzer = build_analyzer(settings(gemini_api_key="g" * 20))

    assert isinstance(analyzer, GeminiAnalyzer)


def test_build_uses_fallback_only_when_no_gemini_key():
    analyzer = build_analyzer(settings(fallback_llm_api_key="f" * 20))

    assert isinstance(analyzer, OpenAICompatibleAnalyzer)


def test_build_chains_gemini_then_fallback_when_both_keys_set():
    analyzer = build_analyzer(settings(gemini_api_key="g" * 20, fallback_llm_api_key="f" * 20))

    assert isinstance(analyzer, FallbackAnalyzer)


def test_build_without_any_key_raises_not_configured():
    with pytest.raises(AnalysisError) as exc_info:
        build_analyzer(settings())

    assert exc_info.value.code == "LLM_NOT_CONFIGURED"
    assert exc_info.value.transient is False
