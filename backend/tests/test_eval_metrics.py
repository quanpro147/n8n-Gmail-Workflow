from dataclasses import replace

from eval.metrics import (
    CaseResult,
    accuracy,
    confusion_matrix,
    latency_stats,
    percentile,
    summarize,
    tool_stats,
)


def result(**overrides) -> CaseResult:
    values = {
        "case_id": "c",
        "lang": "en",
        "tags": (),
        "expects_tool": False,
        "expected_category": "career",
        "expected_priority": "high",
        "expected_action": "notify",
        "predicted_category": "career",
        "predicted_priority": "high",
        "predicted_action": "notify",
        "decision_rule": "high_priority",
        "confidence": 0.9,
        "latency_ms": 100.0,
        "provider": "gemini",
        "model": "m",
        "llm_calls": 2,
        "llm_retries": 0,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "tool_calls": (),
        "error_code": None,
        "invalid_output_events": 0,
        "fallback_used": False,
    }
    return CaseResult(**{**values, **overrides})


def failed(**overrides) -> CaseResult:
    values = {
        "predicted_category": None,
        "predicted_priority": None,
        "predicted_action": None,
        "decision_rule": None,
        "confidence": None,
        "provider": "",
        "model": "",
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "error_code": "LLM_UNAVAILABLE",
    }
    return result(**{**values, **overrides})


def test_percentile_uses_nearest_rank():
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 100
    assert percentile([7], 95) == 7
    assert percentile([], 50) == 0.0


def test_accuracy_counts_failed_cases_as_wrong():
    results = [result(), result(predicted_action="ignore"), failed()]

    assert accuracy(results, "action") == 1 / 3
    assert accuracy(results, "category") == 2 / 3
    assert accuracy([], "action") == 0.0


def test_confusion_matrix_maps_expected_to_predicted_including_errors():
    results = [
        result(),
        result(expected_action="ignore", predicted_action="notify"),
        failed(expected_action="ignore"),
    ]

    assert confusion_matrix(results) == {
        "ignore": {"notify": 1, "error": 1},
        "notify": {"notify": 1},
    }


def test_latency_ignores_failed_cases():
    results = [result(latency_ms=100), result(latency_ms=300), failed(latency_ms=30_000)]

    assert latency_stats(results) == {"mean": 200.0, "p50": 100.0, "p95": 300.0, "max": 300.0}
    assert latency_stats([failed()]) == {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}


def test_tool_precision_and_recall_against_labels():
    results = [
        result(expects_tool=True, tool_calls=("lookup_email_history",)),  # true positive
        result(expects_tool=True),  # missed
        result(tool_calls=("lookup_email_history",)),  # unnecessary call
        result(),
    ]

    stats = tool_stats(results)

    assert stats["call_rate"] == 0.5
    assert stats["precision"] == 0.5
    assert stats["recall"] == 0.5


def test_tool_stats_without_any_success_are_zero():
    assert tool_stats([failed()])["call_rate"] == 0.0


def test_summary_reports_tokens_errors_and_groups():
    base = result(lang="vi", tags=("injection",))
    reviewed = replace(
        base,
        llm_retries=2,
        invalid_output_events=1,
        decision_rule="invalid_model_output",
        predicted_action="review",
    )
    results = [
        base,
        reviewed,
        failed(lang="en", fallback_used=True, primary_error_code="LLM_UNAVAILABLE"),
    ]

    summary = summarize(results)

    assert summary["cases"] == 3
    assert summary["tokens"]["total"] == 240
    assert summary["tokens"]["mean_per_email"] == 120.0
    assert summary["llm"] == {
        "failed_emails": 1,
        "errors_by_code": {"LLM_UNAVAILABLE": 1},
        "retries": 2,
        "invalid_output_events": 1,
        "reviewed_for_invalid_output": 1,
        "fallback_used": 1,
        "fallback_reasons": {"LLM_UNAVAILABLE": 1},
    }
    assert summary["action_accuracy_by_lang"] == {"en": 0.0, "vi": 0.5}
    assert summary["action_accuracy_by_tag"] == {"injection": 0.5}
    assert summary["providers"] == {"gemini:m": 2}
