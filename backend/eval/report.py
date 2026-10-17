"""Markdown report for one benchmark run."""

from collections.abc import Sequence

from eval.metrics import CaseResult


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _misses(results: Sequence[CaseResult]) -> str:
    rows = [
        (
            r.case_id,
            f"{r.expected_category}/{r.expected_priority}/{r.expected_action}",
            f"{r.predicted_category}/{r.predicted_priority}/{r.predicted_action}"
            if r.succeeded
            else f"error: {r.error_code}",
            r.decision_rule or "-",
            "" if r.confidence is None else f"{r.confidence:.2f}",
        )
        for r in results
        if (r.predicted_category, r.predicted_priority, r.predicted_action)
        != (r.expected_category, r.expected_priority, r.expected_action)
    ]
    if not rows:
        return "_None: every case matched on category, priority and action._"
    return _table(["case", "expected (cat/pri/action)", "predicted", "rule", "confidence"], rows)


def render_report(meta: dict, summary: dict, results: Sequence[CaseResult]) -> str:
    acc, lat, tok, llm, tool = (
        summary["accuracy"], summary["latency_ms"], summary["tokens"], summary["llm"], summary["tool"]
    )
    confusion = summary["action_confusion"]
    actions = sorted({a for row in confusion.values() for a in row} | set(confusion))
    confusion_rows = [
        (expected, *(confusion[expected].get(predicted, 0) for predicted in actions))
        for expected in sorted(confusion)
    ]
    sections = [
        f"# Benchmark report: `{meta['provider_mode']}`",
        f"{summary['cases']} cases from `{meta['dataset']}`, {meta['started_at']} → {meta['finished_at']}. "
        f"Gemini model `{meta['gemini_model']}`, fallback `{meta['fallback_model']}`, "
        f"MIN_CONFIDENCE {meta['min_confidence']}. Served by: "
        + ", ".join(f"`{name}` ({count})" for name, count in summary["providers"].items()),
        "## Accuracy",
        _table(["field", "accuracy"], [(k, _pct(v)) for k, v in acc.items()]),
        "Action accuracy by language: "
        + ", ".join(f"{k} {_pct(v)}" for k, v in summary["action_accuracy_by_lang"].items())
        + ". By tag: "
        + (", ".join(f"{k} {_pct(v)}" for k, v in summary["action_accuracy_by_tag"].items()) or "-"),
        "### Action confusion (rows = expected, columns = predicted)",
        _table(["expected \\ predicted", *actions], confusion_rows),
        "## Latency per email (successful analyses, ms)",
        _table(["mean", "p50", "p95", "max"], [(lat["mean"], lat["p50"], lat["p95"], lat["max"])]),
        "## Token usage (successful analyses)",
        _table(
            ["prompt total", "completion total", "total", "mean / email", "LLM calls / email"],
            [(tok["prompt_total"], tok["completion_total"], tok["total"], tok["mean_per_email"],
              tok["mean_llm_calls_per_email"])],
        ),
        "## LLM errors",
        _table(
            ["failed emails", "errors by code", "retries", "invalid-output events",
             "reviewed (invalid output)", "fallback used (primary error)"],
            [(llm["failed_emails"], llm["errors_by_code"] or "-", llm["retries"],
              llm["invalid_output_events"], llm["reviewed_for_invalid_output"],
              f"{llm['fallback_used']} {llm['fallback_reasons'] or ''}".strip())],
        ),
        "## Tool use (`lookup_email_history`)",
        _table(
            ["call rate", "precision", "recall", "emails with a call", "emails expecting one"],
            [(_pct(tool["call_rate"]), _pct(tool["precision"]), _pct(tool["recall"]),
              tool["emails_with_tool_call"], tool["emails_expecting_tool"])],
        ),
        "## Mismatches",
        _misses(results),
    ]
    return "\n\n".join(sections) + "\n"
