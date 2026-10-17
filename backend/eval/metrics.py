"""Per-case benchmark results and the pure functions that summarize them."""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

ERROR_LABEL = "error"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    lang: str
    tags: tuple[str, ...]
    expects_tool: bool
    expected_category: str
    expected_priority: str
    expected_action: str
    predicted_category: str | None
    predicted_priority: str | None
    predicted_action: str | None
    decision_rule: str | None
    confidence: float | None
    latency_ms: float
    provider: str
    model: str
    llm_calls: int
    llm_retries: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tool_calls: tuple[str, ...]
    error_code: str | None
    invalid_output_events: int
    fallback_used: bool
    primary_error_code: str | None = None  # why the primary LLM failed when the fallback ran

    @property
    def succeeded(self) -> bool:
        return self.error_code is None

    @property
    def called_tool(self) -> bool:
        return bool(self.tool_calls)


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation); 0.0 for an empty sequence."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def accuracy(results: Sequence[CaseResult], field: str) -> float:
    """Share of all cases whose prediction matches the label; failed cases count as wrong."""
    if not results:
        return 0.0
    hits = sum(getattr(r, f"predicted_{field}") == getattr(r, f"expected_{field}") for r in results)
    return hits / len(results)


def confusion_matrix(results: Sequence[CaseResult]) -> dict[str, dict[str, int]]:
    """expected action -> predicted action (or "error") -> count."""
    matrix: dict[str, Counter] = {}
    for r in results:
        predicted = r.predicted_action or ERROR_LABEL
        matrix.setdefault(r.expected_action, Counter())[predicted] += 1
    return {expected: dict(counts) for expected, counts in sorted(matrix.items())}


def latency_stats(results: Sequence[CaseResult]) -> dict[str, float]:
    values = [r.latency_ms for r in results if r.succeeded]
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "mean": round(fmean(values), 1),
        "p50": round(percentile(values, 50), 1),
        "p95": round(percentile(values, 95), 1),
        "max": round(max(values), 1),
    }


def token_stats(results: Sequence[CaseResult]) -> dict[str, float]:
    succeeded = [r for r in results if r.succeeded]
    count = len(succeeded) or 1
    return {
        "prompt_total": sum(r.prompt_tokens for r in succeeded),
        "completion_total": sum(r.completion_tokens for r in succeeded),
        "total": sum(r.total_tokens for r in succeeded),
        "mean_per_email": round(sum(r.total_tokens for r in succeeded) / count, 1),
        "mean_llm_calls_per_email": round(sum(r.llm_calls for r in succeeded) / count, 2),
    }


def llm_error_stats(results: Sequence[CaseResult]) -> dict:
    return {
        "failed_emails": sum(not r.succeeded for r in results),
        "errors_by_code": dict(Counter(r.error_code for r in results if r.error_code)),
        "retries": sum(r.llm_retries for r in results),
        "invalid_output_events": sum(r.invalid_output_events for r in results),
        "reviewed_for_invalid_output": sum(
            r.decision_rule == "invalid_model_output" for r in results
        ),
        "fallback_used": sum(r.fallback_used for r in results),
        "fallback_reasons": dict(
            Counter(r.primary_error_code for r in results if r.primary_error_code)
        ),
    }


def tool_stats(results: Sequence[CaseResult]) -> dict[str, float]:
    """Did the agent call the history tool when (and only when) the label says it should?"""
    succeeded = [r for r in results if r.succeeded]
    called = [r for r in succeeded if r.called_tool]
    expected = [r for r in succeeded if r.expects_tool]
    true_positive = sum(r.expects_tool for r in called)
    return {
        "call_rate": round(len(called) / len(succeeded), 3) if succeeded else 0.0,
        "precision": round(true_positive / len(called), 3) if called else 0.0,
        "recall": round(true_positive / len(expected), 3) if expected else 0.0,
        "emails_with_tool_call": len(called),
        "emails_expecting_tool": len(expected),
    }


def accuracy_by(results: Sequence[CaseResult], key: str) -> dict[str, float]:
    """Action accuracy per language or per tag."""
    groups: dict[str, list[CaseResult]] = {}
    for r in results:
        values = r.tags if key == "tag" else (getattr(r, key),)
        for value in values:
            groups.setdefault(value, []).append(r)
    return {name: round(accuracy(group, "action"), 3) for name, group in sorted(groups.items())}


def summarize(results: Sequence[CaseResult]) -> dict:
    return {
        "cases": len(results),
        "accuracy": {
            field: round(accuracy(results, field), 3) for field in ("category", "priority", "action")
        },
        "action_accuracy_by_lang": accuracy_by(results, "lang"),
        "action_accuracy_by_tag": accuracy_by(results, "tag"),
        "action_confusion": confusion_matrix(results),
        "latency_ms": latency_stats(results),
        "tokens": token_stats(results),
        "llm": llm_error_stats(results),
        "tool": tool_stats(results),
        "providers": dict(Counter(f"{r.provider}:{r.model}" for r in results if r.succeeded)),
    }
