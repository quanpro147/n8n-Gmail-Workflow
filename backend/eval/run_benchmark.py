"""Benchmark the email agent on the labeled dataset.

    cd backend && python -m eval.run_benchmark --provider auto --delay 4

Runs the real analyzer + business rules in-process. Each case supplies its own sender
history, so no DB or MCP server is needed and nothing is written to the database.
Outputs (no email text) go to eval/results/<utc-timestamp>-<provider>/: results.jsonl,
summary.json, report.md and warnings.jsonl (the run's warning log lines).
"""

import argparse
import json
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import JsonFormatter, configure_logging
from app.services.agent_service import AnalysisError, Analyzer, build_gemini_analyzer
from app.services.analyzer_builder import build_analyzer, build_fallback_analyzer
from app.services.notification_rules import decide, review_decision
from eval.dataset import DEFAULT_DATASET, EvalCase, load_dataset
from eval.metrics import CaseResult, summarize
from eval.report import render_report

DEFAULT_OUT = Path(__file__).with_name("results")
FALLBACK_LOG_MESSAGE = "Primary LLM failed, using fallback"

ANALYZER_BUILDERS: dict[str, Callable[[Settings], Analyzer]] = {
    "auto": build_analyzer,  # production setup: Gemini, then the fallback on failure
    "gemini": build_gemini_analyzer,
    "fallback": build_fallback_analyzer,
}


class _EventCounter(logging.Handler):
    """Counts the agent's existing warning events while one case runs."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.invalid_output = 0
        self.fallback = 0
        self.primary_error_code: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "outcome", None) == "invalid_output":
            self.invalid_output += 1
        if record.getMessage() == FALLBACK_LOG_MESSAGE:
            self.fallback += 1
            self.primary_error_code = getattr(record, "error_code", None)


@contextmanager
def counting_events() -> Iterator[_EventCounter]:
    counter = _EventCounter()
    root = logging.getLogger()
    root.addHandler(counter)
    try:
        yield counter
    finally:
        root.removeHandler(counter)


def run_case(
    case: EvalCase,
    analyzer: Analyzer,
    *,
    min_confidence: float,
    clock: Callable[[], float] = time.perf_counter,
) -> CaseResult:
    email = case.to_email_in()
    history = case.history_result()
    outcome = error_code = None
    with counting_events() as events:
        started = clock()
        try:
            outcome = analyzer.analyze(email, history_lookup=lambda: dict(history))
        except AnalysisError as exc:
            error_code = exc.code
        latency_ms = round((clock() - started) * 1000, 1)

    analysis = outcome.analysis if outcome else None
    decision = None
    if outcome is not None:
        decision = (
            decide(analysis, min_confidence=min_confidence) if analysis else review_decision()
        )
    usage = outcome.usage if outcome else None
    return CaseResult(
        case_id=case.id,
        lang=case.lang,
        tags=case.tags,
        expects_tool=case.expects_tool,
        expected_category=case.expected.category.value,
        expected_priority=case.expected.priority.value,
        expected_action=case.expected.action.value,
        predicted_category=analysis.category.value if analysis else None,
        predicted_priority=analysis.priority.value if analysis else None,
        predicted_action=decision.action.value if decision else None,
        decision_rule=decision.rule if decision else None,
        confidence=analysis.confidence if analysis else None,
        latency_ms=latency_ms,
        provider=outcome.provider if outcome else "",
        model=outcome.model if outcome else "",
        llm_calls=usage.calls if usage else 0,
        llm_retries=usage.retries if usage else 0,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        total_tokens=usage.total_tokens if usage else 0,
        tool_calls=outcome.tool_calls if outcome else (),
        error_code=error_code,
        invalid_output_events=events.invalid_output,
        fallback_used=events.fallback > 0,
        primary_error_code=events.primary_error_code,
    )


def run_benchmark(
    cases: Sequence[EvalCase],
    analyzer: Analyzer,
    *,
    min_confidence: float,
    delay_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] = print,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        if index > 1 and delay_s:
            sleep(delay_s)  # stay under the provider's requests-per-minute limit
        result = run_case(case, analyzer, min_confidence=min_confidence)
        results.append(result)
        verdict = "ok " if result.predicted_action == result.expected_action else "MISS"
        progress(
            f"[{index:>2}/{len(cases)}] {verdict} {case.id:<28} "
            f"action={result.predicted_action or result.error_code} "
            f"tokens={result.total_tokens} {result.latency_ms:.0f}ms"
            f"{' tool' if result.called_tool else ''}"
        )
    return results


@contextmanager
def saving_warnings(path: Path) -> Iterator[None]:
    """Keep the run's warning/error log lines (same allow-listed JSON as production)."""
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.WARNING)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()


def write_outputs(out_dir: Path, results: Sequence[CaseResult], meta: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(results)
    (out_dir / "results.jsonl").write_text(
        "".join(json.dumps(asdict(r), ensure_ascii=False) + "\n" for r in results), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps({"meta": meta, "summary": summary}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_report(meta, summary, results), encoding="utf-8")
    return summary


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=sorted(ANALYZER_BUILDERS), default="auto")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases")
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between emails")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> Path:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging("WARNING")  # keep the console to progress lines and real warnings
    cases = load_dataset(args.dataset)[: args.limit]
    analyzer = ANALYZER_BUILDERS[args.provider](settings)
    started_at = datetime.now(UTC)
    out_dir = args.out / f"{started_at:%Y%m%dT%H%M%SZ}-{args.provider}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with saving_warnings(out_dir / "warnings.jsonl"):
        results = run_benchmark(
            cases, analyzer, min_confidence=settings.min_confidence, delay_s=args.delay
        )
    meta = {
        "provider_mode": args.provider,
        "gemini_model": settings.gemini_model,
        "fallback_model": settings.fallback_llm_model,
        "min_confidence": settings.min_confidence,
        "dataset": args.dataset.name,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    summary = write_outputs(out_dir, results, meta)
    print(f"\naccuracy={summary['accuracy']} latency={summary['latency_ms']} -> {out_dir}")
    return out_dir


if __name__ == "__main__":
    main()
