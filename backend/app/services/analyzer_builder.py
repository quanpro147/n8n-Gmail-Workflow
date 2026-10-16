"""Builds the analyzer from settings: Gemini first, an OpenAI-compatible model as fallback."""

import logging

import openai

from app.core.config import Settings
from app.schemas.email import EmailIn
from app.services.agent_service import (
    AnalysisError,
    AnalysisOutcome,
    Analyzer,
    HistoryLookup,
    build_gemini_analyzer,
)
from app.services.openai_compatible_analyzer import OpenAICompatibleAnalyzer

logger = logging.getLogger(__name__)


class FallbackAnalyzer:
    """Runs `fallback` when `primary` raises any AnalysisError (quota, outage, rejected model).

    Invalid model output is not a failure: the primary's `analysis=None` result (→ review)
    is returned as is, without asking the fallback.
    """

    def __init__(self, primary: Analyzer, fallback: Analyzer) -> None:
        self._primary = primary
        self._fallback = fallback

    def analyze(self, email: EmailIn, history_lookup: HistoryLookup) -> AnalysisOutcome:
        try:
            return self._primary.analyze(email, history_lookup)
        except AnalysisError as primary_error:
            logger.warning(
                "Primary LLM failed, using fallback",
                extra={"message_id": email.message_id, "error_code": primary_error.code},
            )
            try:
                return self._fallback.analyze(email, history_lookup)
            except AnalysisError as fallback_error:
                # Prefer a transient error so n8n retries: one provider may recover.
                if fallback_error.transient or not primary_error.transient:
                    raise fallback_error from primary_error
                raise primary_error from fallback_error


def build_fallback_analyzer(settings: Settings) -> OpenAICompatibleAnalyzer:
    if settings.fallback_llm_api_key is None:
        raise AnalysisError("LLM_NOT_CONFIGURED", "FALLBACK_LLM_API_KEY is not set", transient=False)
    client = openai.OpenAI(
        api_key=settings.fallback_llm_api_key.get_secret_value(),
        base_url=settings.fallback_llm_base_url,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries - 1,
    )
    return OpenAICompatibleAnalyzer(
        client, model=settings.fallback_llm_model, max_body_chars=settings.max_body_chars
    )


def build_analyzer(settings: Settings) -> Analyzer:
    has_gemini = settings.gemini_api_key is not None
    has_fallback = settings.fallback_llm_api_key is not None
    if has_gemini and has_fallback:
        return FallbackAnalyzer(build_gemini_analyzer(settings), build_fallback_analyzer(settings))
    if has_fallback:
        return build_fallback_analyzer(settings)
    if has_gemini:
        return build_gemini_analyzer(settings)
    raise AnalysisError(
        "LLM_NOT_CONFIGURED", "Neither GEMINI_API_KEY nor FALLBACK_LLM_API_KEY is set", transient=False
    )
