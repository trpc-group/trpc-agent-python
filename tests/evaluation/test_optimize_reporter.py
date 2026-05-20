# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for OptimizeReporter progress sinks (Null / Rich / ASCII)."""

from __future__ import annotations

import io
import logging
from typing import Any

import pytest

from trpc_agent_sdk.evaluation._optimize_reporter import (
    RoundView,
    RunHeader,
    create_reporter,
    _AsciiReporter,
    _NullReporter,
    _SilentGepaLogger,
)


def _header(**overrides: Any) -> RunHeader:
    defaults = dict(
        algorithm="gepa_reflective",
        target_fields=[("instruction", "agent/prompts/system.md")],
        train_size=5,
        val_size=3,
        metric_names=["final_response_avg_score"],
        output_dir="runs/2026-05-17T16-30-00",
    )
    defaults.update(overrides)
    return RunHeader(**defaults)


def _round_view(**overrides: Any) -> RoundView:
    defaults = dict(
        round=1,
        kind="reflective",
        train_minibatch_size=2,
        train_size=5,
        train_subsample_parent_score=0.0,
        train_subsample_candidate_score=1.0,
        val_pass_rate=1.0,
        accepted=True,
        skip_reason=None,
        error_message=None,
        duration_seconds=28.4,
        budget_used=12,
        budget_total=None,  # "auto"
    )
    defaults.update(overrides)
    return RoundView(**defaults)


class TestFactory:
    def test_verbose_zero_returns_null_reporter(self):
        reporter = create_reporter(verbose=0)
        assert isinstance(reporter, _NullReporter)

    def test_null_reporter_emits_nothing(self, capsys):
        reporter = create_reporter(verbose=0)
        reporter.run_started(_header())
        reporter.baseline_evaluated(0.0, {})
        reporter.round_completed(_round_view())
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_verbose_one_picks_a_real_reporter(self):
        reporter = create_reporter(verbose=1, stream=io.StringIO())
        assert not isinstance(reporter, _NullReporter)

    def test_falls_back_to_ascii_reporter_when_rich_is_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """``rich`` is an optional extra of the ``optimize`` install group;
        when missing, the factory must degrade gracefully to the ASCII
        backend so AgentOptimizer still produces a readable timeline."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "rich" or name.startswith("rich."):
                raise ImportError("simulated missing rich dependency")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        reporter = create_reporter(verbose=1, stream=io.StringIO())
        assert isinstance(reporter, _AsciiReporter)


class TestAsciiReporterRoundRendering:
    """Use the ASCII backend directly so assertions don't depend on rich's
    rendering quirks. Rich backend is exercised separately in TestRichReporter.
    """

    def _new(self) -> tuple[_AsciiReporter, io.StringIO]:
        buf = io.StringIO()
        return _AsciiReporter(stream=buf, verbose=1), buf

    def test_round_accepted_renders_one_line_with_semantic_segments(self):
        reporter, buf = self._new()
        reporter.round_completed(_round_view())
        output = buf.getvalue()
        assert "round 1" in output
        assert "accepted" in output
        assert "train sample 2/5" in output
        assert "sample score 0.00" in output
        assert "1.00" in output
        assert "valset pass_rate 1.0000" in output
        assert "evaluations 12/auto" in output
        # Single line per round.
        assert output.count("round 1") == 1

    def test_round_skipped_subsample_perfect_uses_skip_marker(self):
        reporter, buf = self._new()
        reporter.round_completed(
            _round_view(
                round=2,
                train_subsample_parent_score=1.0,
                train_subsample_candidate_score=None,
                val_pass_rate=None,
                accepted=False,
                skip_reason="minibatch already perfect (skip_perfect_score on)",
                duration_seconds=3.1,
            )
        )
        output = buf.getvalue()
        assert "round 2" in output
        assert "skipped" in output
        assert "minibatch already perfect" in output
        # No valset segment when skipped pre-val.
        assert "valset pass_rate" not in output

    def test_round_skipped_no_proposal_omits_train_segment_when_no_minibatch(self):
        reporter, buf = self._new()
        reporter.round_completed(
            _round_view(
                round=4,
                train_minibatch_size=0,
                train_subsample_parent_score=None,
                train_subsample_candidate_score=None,
                val_pass_rate=None,
                accepted=False,
                skip_reason="reflect-LM produced no usable new prompt",
                duration_seconds=1.2,
            )
        )
        output = buf.getvalue()
        assert "round 4" in output
        assert "skipped" in output
        assert "reflect-LM produced no usable new prompt" in output
        assert "train sample" not in output

    def test_round_error_uses_error_marker(self):
        reporter, buf = self._new()
        reporter.round_completed(
            _round_view(
                round=3,
                train_subsample_candidate_score=None,
                val_pass_rate=None,
                accepted=False,
                skip_reason=None,
                error_message="evaluator timeout",
                duration_seconds=15.0,
            )
        )
        output = buf.getvalue()
        assert "round 3" in output
        assert "error" in output.lower()
        assert "message: evaluator timeout" in output

    def test_round_explored_when_evaluated_but_not_accepted(self):
        reporter, buf = self._new()
        reporter.round_completed(
            _round_view(
                round=6,
                accepted=False,
                val_pass_rate=0.42,
                train_subsample_parent_score=0.3,
                train_subsample_candidate_score=0.4,
            )
        )
        output = buf.getvalue()
        assert "round 6" in output
        assert "explored" in output
        assert "valset pass_rate 0.4200" in output

    def test_merge_round_renders_with_merge_marker(self):
        reporter, buf = self._new()
        reporter.round_completed(_round_view(round=7, kind="merge"))
        output = buf.getvalue()
        assert "round 7" in output
        assert "merged" in output.lower() or "merge" in output.lower()


class TestAsciiReporterHeaderAndBaseline:
    def _new(self) -> tuple[_AsciiReporter, io.StringIO]:
        buf = io.StringIO()
        return _AsciiReporter(stream=buf, verbose=1), buf

    def test_header_single_target_field_shows_basename_only(self):
        reporter, buf = self._new()
        reporter.run_started(_header())
        out = buf.getvalue()
        assert "gepa_reflective" in out
        assert "instruction" in out
        # Header collapses file-backed sources to basename so deep paths
        # don't dominate the panel; full paths remain in config.snapshot.json.
        assert "system.md" in out
        assert "agent/prompts/system.md" not in out
        assert "train/val" in out or "train" in out.lower()
        assert "5" in out and "3" in out
        assert "runs/2026-05-17T16-30-00" in out
        # Legend is printed once after the header so users can decode subsequent
        # per-round lines without scrolling back to documentation.
        assert "Round line legend" in out
        assert "valset pass_rate" in out
        assert "evaluations used/total" in out

    def test_header_multiple_target_fields_keeps_callback_sentinel(self):
        reporter, buf = self._new()
        reporter.run_started(
            _header(
                target_fields=[
                    ("system_prompt", "prompts/system.md"),
                    ("user_template", "prompts/user.md"),
                    ("rubric", "<callback>"),
                ],
            )
        )
        out = buf.getvalue()
        assert "system_prompt" in out
        assert "user_template" in out
        assert "rubric" in out
        # File-backed sources collapse to basenames in the panel.
        assert "system.md" in out
        assert "user.md" in out
        # Callback sources keep the explicit <callback> sentinel.
        assert "<callback>" in out

    def test_header_multiple_metrics_shows_count(self):
        reporter, buf = self._new()
        reporter.run_started(
            _header(
                metric_names=["final_response_avg_score", "llm_rubric_response"]
            )
        )
        out = buf.getvalue()
        # B2: metric count visible
        assert "2" in out
        assert "final_response_avg_score" in out
        assert "llm_rubric_response" in out

    def test_header_long_field_name_is_truncated(self):
        reporter, buf = self._new()
        long_name = "this_is_a_very_long_field_name_that_must_be_truncated_for_display"
        reporter.run_started(
            _header(target_fields=[(long_name, "prompts/x.md")])
        )
        out = buf.getvalue()
        # A4: never echo a line longer than the truncation cap
        for line in out.splitlines():
            assert len(line) <= 200  # generous cap on header line width

    def test_baseline_renders_pass_rate_and_breakdown(self):
        reporter, buf = self._new()
        reporter.baseline_evaluated(
            0.42, {"final_response_avg_score": 0.42}
        )
        out = buf.getvalue()
        assert "baseline" in out.lower()
        assert "0.4200" in out

    def test_baseline_shows_thresholds_and_pass_fail_status(self):
        reporter, buf = self._new()
        reporter.baseline_evaluated(
            0.5,
            {"final_response_avg_score": 0.42, "response_match_score": 0.80},
            metric_thresholds={
                "final_response_avg_score": 0.5,
                "response_match_score": 0.3,
            },
        )
        out = buf.getvalue()
        # Threshold column present.
        assert "threshold 0.5000" in out
        assert "threshold 0.3000" in out
        # PASS / FAIL status reflects evaluator semantics (score >= threshold).
        assert "FAIL" in out  # 0.42 < 0.5
        assert "PASS" in out  # 0.80 >= 0.3


class TestAsciiReporterRunFinished:
    def _new(self) -> tuple[_AsciiReporter, io.StringIO]:
        buf = io.StringIO()
        return _AsciiReporter(stream=buf, verbose=1), buf

    def _make_result(self, **overrides: Any) -> Any:
        from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult, RoundRecord
        defaults = dict(
            algorithm="gepa_reflective",
            status="SUCCEEDED",
            finish_reason="completed",
            baseline_pass_rate=0.0,
            best_pass_rate=1.0,
            pass_rate_improvement=1.0,
            baseline_metric_breakdown={},
            best_metric_breakdown={},
            metric_thresholds={},
            baseline_prompts={"instruction": "old"},
            best_prompts={"instruction": "new"},
            total_rounds=2,
            rounds=[],
            total_reflection_lm_calls=2,
            total_judge_model_calls=0,
            total_llm_cost=0.0,
            total_token_usage={"prompt": 0, "completion": 0, "total": 0},
            duration_seconds=142.86,
            started_at="2026-05-17T16:30:00+00:00",
            finished_at="2026-05-17T16:32:22+00:00",
            extras={},
        )
        defaults.update(overrides)
        return OptimizeResult(**defaults)

    def test_summary_panel_shows_improvement_arrow(self):
        reporter, buf = self._new()
        result = self._make_result()
        reporter.run_finished(
            result, output_dir="runs/2026-05-17T16-30-00", update_source=False,
        )
        out = buf.getvalue()
        assert "SUCCEEDED" in out
        assert "0.0000" in out and "1.0000" in out
        assert "+1.0000" in out or "+1.00" in out
        assert "improved" in out
        assert "142.86" in out
        assert "runs/2026-05-17T16-30-00" in out

    def test_summary_panel_shows_no_improvement_when_flat(self):
        reporter, buf = self._new()
        result = self._make_result(
            best_pass_rate=0.5,
            baseline_pass_rate=0.5,
            pass_rate_improvement=0.0,
            finish_reason="no_improvement",
        )
        reporter.run_finished(
            result, output_dir="runs/x", update_source=False,
        )
        out = buf.getvalue()
        assert "no improvement" in out.lower() or "no_improvement" in out

    def test_summary_panel_marks_failed_status(self):
        reporter, buf = self._new()
        result = self._make_result(
            status="FAILED", finish_reason="error",
            error_message="dataset load failed: missing file",
        )
        reporter.run_finished(
            result, output_dir="runs/x", update_source=False,
        )
        out = buf.getvalue()
        assert "FAILED" in out
        assert "dataset load failed" in out

    def test_summary_shows_update_source_when_true(self):
        reporter, buf = self._new()
        result = self._make_result()
        reporter.run_finished(
            result, output_dir="runs/x", update_source=True,
        )
        out = buf.getvalue()
        # G1: update_source visible
        assert "update_source" in out
        # Mentions the source was written back.
        assert "written" in out.lower() or "true" in out.lower()

    def test_summary_shows_stopped_by_required_metrics_passing(self):
        reporter, buf = self._new()
        result = self._make_result(stop_reason="required_metrics_passing")
        reporter.run_finished(result, output_dir="runs/x", update_source=False)
        out = buf.getvalue()
        assert "stopped by" in out
        assert "required metrics met thresholds" in out

    def test_summary_shows_stopped_by_budget_exhausted(self):
        reporter, buf = self._new()
        result = self._make_result(stop_reason="budget_exhausted")
        reporter.run_finished(result, output_dir="runs/x", update_source=False)
        out = buf.getvalue()
        assert "stopped by" in out
        assert "budget exhausted" in out
        # Disambiguates from the legacy catch-all label so users can tell the
        # MaxMetricCallsStopper triggered specifically.
        assert "max_metric_calls" in out

    def test_summary_shows_stopped_by_no_improvement(self):
        reporter, buf = self._new()
        result = self._make_result(stop_reason="no_improvement")
        reporter.run_finished(result, output_dir="runs/x", update_source=False)
        out = buf.getvalue()
        assert "stopped by" in out
        assert "no improvement" in out

    def test_summary_shows_stopped_by_timeout(self):
        reporter, buf = self._new()
        result = self._make_result(stop_reason="timeout")
        reporter.run_finished(result, output_dir="runs/x", update_source=False)
        out = buf.getvalue()
        assert "stopped by" in out
        assert "timeout" in out

    def test_summary_shows_stopped_by_score_threshold(self):
        reporter, buf = self._new()
        result = self._make_result(stop_reason="score_threshold")
        reporter.run_finished(result, output_dir="runs/x", update_source=False)
        out = buf.getvalue()
        assert "stopped by" in out
        assert "score threshold" in out

    def test_summary_shows_stopped_by_completed_when_no_stopper_fired(self):
        reporter, buf = self._new()
        result = self._make_result(stop_reason="completed")
        reporter.run_finished(result, output_dir="runs/x", update_source=False)
        out = buf.getvalue()
        assert "stopped by" in out
        assert "completed" in out
        assert "no stopper triggered" in out

    def test_summary_shows_stopped_by_user_requested_stop(self) -> None:
        from trpc_agent_sdk.evaluation._optimize_reporter import _format_stop_reason_text

        assert _format_stop_reason_text("user_requested_stop") == (
            "user requested stop (optimize.stop touched)"
        )

    def test_summary_omits_stopped_by_when_stop_reason_none(self):
        reporter, buf = self._new()
        result = self._make_result(stop_reason=None)
        reporter.run_finished(result, output_dir="runs/x", update_source=False)
        out = buf.getvalue()
        assert "stopped by" not in out

    def test_summary_per_metric_table_includes_threshold_and_status(self):
        reporter, buf = self._new()
        result = self._make_result(
            baseline_metric_breakdown={
                "final_response_avg_score": 0.42,
                "response_match_score": 0.10,
            },
            best_metric_breakdown={
                "final_response_avg_score": 1.0,
                "response_match_score": 0.20,
            },
            metric_thresholds={
                "final_response_avg_score": 0.5,
                "response_match_score": 0.3,
            },
        )
        reporter.run_finished(
            result, output_dir="runs/x", update_source=False,
        )
        out = buf.getvalue()
        assert "threshold | baseline -> best" in out
        assert "threshold 0.5000" in out
        assert "threshold 0.3000" in out
        # final_response_avg_score 1.0 >= 0.5 → PASS
        # response_match_score    0.2 < 0.3 → FAIL
        assert "PASS" in out
        assert "FAIL" in out


class _CapturingHandler(logging.Handler):
    """Test helper: collects every record emitted on the attached logger.

    Attached directly to the target logger (rather than relying on root /
    caplog) because the ``trpc_agent_sdk`` parent logger sets
    ``propagate=False`` once initialised, which would prevent caplog from
    seeing child events.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def gepa_log_capture() -> tuple[logging.Logger, list[logging.LogRecord]]:
    target = logging.getLogger("trpc_agent_sdk.optimizer.gepa")
    handler = _CapturingHandler()
    target.addHandler(handler)
    previous_level = target.level
    target.setLevel(logging.INFO)
    try:
        yield target, handler.records
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)


class TestSilentGepaLogger:
    """`_SilentGepaLogger` replaces gepa's default StdOutLogger.

    verbose=1: drop every message (no stdout pollution).
    verbose=2: forward to logging.getLogger("trpc_agent_sdk.optimizer.gepa")
               at INFO level so users can route via logging config.
    """

    def test_verbose_one_drops_message(self, capsys, gepa_log_capture):
        _, records = gepa_log_capture
        logger = _SilentGepaLogger(verbose=1)
        logger.log("Iteration 3: Best valset aggregate score so far: 1.0")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
        assert records == []

    def test_verbose_two_forwards_to_logging(self, gepa_log_capture):
        _, records = gepa_log_capture
        logger = _SilentGepaLogger(verbose=2)
        logger.log("Iteration 3: Best valset aggregate score so far: 1.0")
        assert any(
            "Best valset aggregate" in rec.getMessage()
            for rec in records
            if rec.name == "trpc_agent_sdk.optimizer.gepa"
        )


class TestRichBackendFallback:
    """When rich is unavailable, factory must fall back to ASCII silently."""

    def test_create_reporter_falls_back_when_rich_missing(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "rich" or name.startswith("rich."):
                raise ImportError(f"forced: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        reporter = create_reporter(verbose=1, stream=io.StringIO())
        assert isinstance(reporter, _AsciiReporter)


class TestRichProgressNotAutoRefreshFlood:
    """Regression: the Rich Progress region must not flood the timeline with
    duplicate "progress ..." lines when stdout does not honour rich's
    cursor-up escapes (typical for embedded IDE terminals).

    The bug appeared as dozens of stacked ``progress ...`` rows during a
    single GEPA baseline evaluation because rich's default ``auto_refresh``
    fires at 10 Hz; without functioning cursor-up the previous frame was
    never erased and every refresh tick became a fresh log line.

    The fix is to build ``Progress`` with ``auto_refresh=False`` and refresh
    explicitly on each round event. This test asserts a hard upper bound on
    the number of progress lines emitted across a realistic run.
    """

    def test_progress_line_count_is_bounded_by_round_count(self):
        from trpc_agent_sdk.evaluation._optimize_reporter import _RichReporter

        buf = io.StringIO()
        reporter = _RichReporter(stream=buf, verbose=1)
        reporter.run_started(_header(budget_total=60))
        reporter.baseline_evaluated(
            0.0,
            {"final_response_avg_score": 0.0, "llm_rubric_response": 1.0},
            metric_thresholds={
                "final_response_avg_score": 1.0,
                "llm_rubric_response": 0.66,
            },
        )
        for round_no in range(1, 7):
            reporter.round_completed(
                _round_view(
                    round=round_no,
                    accepted=(round_no == 1),
                    skip_reason=None if round_no == 1 else "all_scores_perfect",
                    train_subsample_parent_score=1.0,
                    train_subsample_candidate_score=None,
                    val_pass_rate=0.6667 if round_no == 1 else None,
                    budget_used=10 + (round_no - 1) * 2,
                    budget_total=60,
                )
            )
        reporter._stop_progress()
        progress_lines = [
            line
            for line in buf.getvalue().splitlines()
            if line.lstrip().startswith("progress")
        ]
        # A well-behaved Live region produces at most one progress line per
        # discrete event (start + 6 rounds = 7). A regression that re-enables
        # auto_refresh at 10 Hz over a multi-minute baseline trivially exceeds
        # this bound by an order of magnitude (we saw 30+ in the wild).
        assert len(progress_lines) <= 8, (
            f"too many progress lines: {len(progress_lines)} — "
            f"auto_refresh may have been re-enabled"
        )
