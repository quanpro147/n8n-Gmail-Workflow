import json
import logging
from itertools import count

from app.services.agent_service import AnalysisError, AnalysisOutcome, LlmUsage
from eval import run_benchmark as runner
from eval.dataset import load_dataset
from tests.conftest import make_analysis

META = {
    "provider_mode": "gemini",
    "gemini_model": "g",
    "fallback_model": "f",
    "min_confidence": 0.6,
    "dataset": "dataset.jsonl",
    "started_at": "t0",
    "finished_at": "t1",
}


class ScriptedAnalyzer:
    """Returns (or raises) one scripted item per email and records the history it saw."""

    def __init__(self, *items) -> None:
        self._items = list(items)
        self.history_seen: list[dict] = []

    def analyze(self, email, history_lookup):
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        if item.tool_calls:
            self.history_seen.append(history_lookup())
        return item


def outcome(*, tools: tuple[str, ...] = (), invalid: bool = False) -> AnalysisOutcome:
    return AnalysisOutcome(
        analysis=None if invalid else make_analysis(),
        tool_calls=tools,
        usage=LlmUsage(calls=2, prompt_tokens=300, completion_tokens=50, total_tokens=350),
        provider="gemini",
        model="gemini-test",
    )


def fake_clock():
    ticks = count(step=0.25)
    return lambda: next(ticks)


def followup_case():
    return next(c for c in load_dataset() if c.expects_tool and c.history)


def test_run_case_scores_prediction_and_feeds_case_history_to_the_tool():
    case = followup_case()
    analyzer = ScriptedAnalyzer(outcome(tools=("lookup_email_history",)))

    result = runner.run_case(case, analyzer, min_confidence=0.6, clock=fake_clock())

    assert analyzer.history_seen == [case.history_result()]
    assert result.predicted_action == "notify"  # make_analysis() is career/high
    assert result.decision_rule == "high_priority"
    assert result.latency_ms == 250.0
    assert (result.llm_calls, result.total_tokens) == (2, 350)
    assert result.tool_calls == ("lookup_email_history",)
    assert result.error_code is None


def test_run_case_records_llm_error_without_raising():
    analyzer = ScriptedAnalyzer(AnalysisError("LLM_UNAVAILABLE", "down", transient=True))

    result = runner.run_case(followup_case(), analyzer, min_confidence=0.6)

    assert result.error_code == "LLM_UNAVAILABLE"
    assert result.predicted_action is None
    assert result.total_tokens == 0


def test_run_case_counts_invalid_output_and_fallback_events():
    class NoisyAnalyzer:
        def analyze(self, email, history_lookup):
            log = logging.getLogger("app.services.agent_service")
            log.warning("bad json", extra={"outcome": "invalid_output"})
            log.warning(runner.FALLBACK_LOG_MESSAGE, extra={"error_code": "LLM_REQUEST_REJECTED"})
            return outcome(invalid=True)

    result = runner.run_case(followup_case(), NoisyAnalyzer(), min_confidence=0.6)

    assert result.invalid_output_events == 1
    assert result.fallback_used is True
    assert result.primary_error_code == "LLM_REQUEST_REJECTED"
    assert result.predicted_action == "review"
    assert result.decision_rule == "invalid_model_output"


def test_run_benchmark_waits_between_emails_and_reports_progress():
    cases = load_dataset()[:3]
    sleeps: list[float] = []
    lines: list[str] = []

    results = runner.run_benchmark(
        cases,
        ScriptedAnalyzer(outcome(), outcome(), outcome()),
        min_confidence=0.6,
        delay_s=2.0,
        sleep=sleeps.append,
        progress=lines.append,
    )

    assert len(results) == 3
    assert sleeps == [2.0, 2.0]
    assert len(lines) == 3
    assert lines[0].startswith("[ 1/3]")


def test_write_outputs_contains_no_email_text(tmp_path):
    cases = load_dataset()[:2]
    results = runner.run_benchmark(
        cases, ScriptedAnalyzer(outcome(), outcome()), min_confidence=0.6, progress=lambda _: None
    )

    summary = runner.write_outputs(tmp_path, results, META)

    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    assert summary["cases"] == 2
    assert {p.name for p in tmp_path.iterdir()} == {"results.jsonl", "summary.json", "report.md"}
    assert "# Benchmark report" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["meta"] == META
    for case in cases:
        assert case.email.body not in written
        assert case.email.subject not in written


def test_report_lists_mismatches_and_errors(tmp_path):
    cases = load_dataset()[:2]
    analyzer = ScriptedAnalyzer(
        outcome(), AnalysisError("LLM_REQUEST_REJECTED", "no", transient=False)
    )
    results = runner.run_benchmark(cases, analyzer, min_confidence=0.6, progress=lambda _: None)

    runner.write_outputs(tmp_path, results, META)

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "error: LLM_REQUEST_REJECTED" in report
    assert "## Mismatches" in report


def test_main_runs_selected_provider_and_writes_a_run_directory(tmp_path, monkeypatch):
    monkeypatch.setitem(
        runner.ANALYZER_BUILDERS, "gemini", lambda settings: ScriptedAnalyzer(outcome(), outcome())
    )
    monkeypatch.setattr(runner, "configure_logging", lambda level: None)

    out_dir = runner.main(
        ["--provider", "gemini", "--limit", "2", "--delay", "0", "--out", str(tmp_path)]
    )

    assert out_dir.parent == tmp_path
    assert out_dir.name.endswith("-gemini")
    assert (out_dir / "report.md").exists()
    assert (out_dir / "warnings.jsonl").exists()
