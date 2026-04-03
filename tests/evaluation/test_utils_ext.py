# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Extended tests for evaluation _utils: EvalResultHandler, MetricRunRecord."""

import os
from unittest.mock import patch

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import EvalCaseResult
from trpc_agent_sdk.evaluation import EvalMetricResult
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import SessionInput
from trpc_agent_sdk.evaluation._utils import EvalResultHandler


class TestEvalResultHandlerStatus:
    """Test suite for EvalResultHandler.eval_status_str."""

    def test_passed(self):
        """Test PASSED returns 'passed'."""
        h = EvalResultHandler()
        assert h.eval_status_str(EvalStatus.PASSED) == "passed"

    def test_failed(self):
        """Test FAILED returns 'failed'."""
        h = EvalResultHandler()
        assert h.eval_status_str(EvalStatus.FAILED) == "failed"

    def test_not_evaluated(self):
        """Test NOT_EVALUATED returns 'not_evaluated'."""
        h = EvalResultHandler()
        assert h.eval_status_str(EvalStatus.NOT_EVALUATED) == "not_evaluated"


class TestEvalResultHandlerTerminalWidth:
    """Test suite for EvalResultHandler._terminal_width."""

    def test_from_columns_env(self):
        """Test terminal width from COLUMNS env var."""
        h = EvalResultHandler()
        with patch.dict(os.environ, {"COLUMNS": "120"}):
            assert h._terminal_width() == 120

    def test_invalid_columns_env(self):
        """Test invalid COLUMNS env var falls back."""
        h = EvalResultHandler()
        with patch.dict(os.environ, {"COLUMNS": "not_a_number"}):
            w = h._terminal_width()
            assert w > 0

    def test_zero_columns_env(self):
        """Test zero COLUMNS env var falls back."""
        h = EvalResultHandler()
        with patch.dict(os.environ, {"COLUMNS": "0"}):
            w = h._terminal_width()
            assert w > 0

    def test_no_columns_env(self):
        """Test no COLUMNS env var uses shutil."""
        h = EvalResultHandler()
        env = os.environ.copy()
        env.pop("COLUMNS", None)
        with patch.dict(os.environ, env, clear=True):
            w = h._terminal_width()
            assert w > 0


class TestEvalResultHandlerFormatNumber:
    """Test suite for EvalResultHandler.format_number_like_json."""

    def test_none_returns_na(self):
        """Test None returns N/A."""
        h = EvalResultHandler()
        assert h.format_number_like_json(None) == "N/A"

    def test_float(self):
        """Test float is formatted as JSON number."""
        h = EvalResultHandler()
        assert h.format_number_like_json(0.5) == "0.5"

    def test_int(self):
        """Test int is formatted as JSON number."""
        h = EvalResultHandler()
        assert h.format_number_like_json(1) == "1"


class TestEvalResultHandlerPrintSectionHeader:
    """Test suite for EvalResultHandler.print_section_header."""

    def test_prints_header(self, capsys):
        """Test print_section_header outputs formatted header."""
        h = EvalResultHandler()
        h.print_section_header("Test Title")
        captured = capsys.readouterr()
        assert "Test Title" in captured.out
        assert "=" in captured.out


class TestEvalResultHandlerBuildSummary:
    """Test suite for EvalResultHandler.build_summary."""

    def _make_eval_set(self, case_ids):
        cases = [
            EvalCase(
                eval_id=cid,
                conversation=[],
                session_input=SessionInput(app_name="a", user_id="u", state={}),
            ) for cid in case_ids
        ]
        return EvalSet(eval_set_id="s1", eval_cases=cases)

    def _make_case_result(self, eval_id="c1", status=EvalStatus.PASSED, metrics=None):
        return EvalCaseResult(
            eval_id=eval_id,
            final_eval_status=status,
            overall_eval_metric_results=metrics or [],
            eval_metric_result_per_invocation=[],
            session_id="s1",
        )

    def test_empty_results(self):
        """Test build_summary with empty results."""
        h = EvalResultHandler()
        es = self._make_eval_set(["c1"])
        summary = h.build_summary(es, {}, "test_agent", 1)
        assert summary["overall_status"] == "passed"
        assert summary["agent_name"] == "test_agent"

    def test_all_passed(self):
        """Test build_summary when all cases pass."""
        h = EvalResultHandler()
        es = self._make_eval_set(["c1"])
        mr = EvalMetricResult(metric_name="m1", score=1.0, eval_status=EvalStatus.PASSED, threshold=0.5)
        cr = self._make_case_result("c1", EvalStatus.PASSED, [mr])
        summary = h.build_summary(es, {"c1": [cr]}, "agent", 1)
        assert summary["overall_status"] == "passed"

    def test_any_failed(self):
        """Test build_summary when any case fails."""
        h = EvalResultHandler()
        es = self._make_eval_set(["c1", "c2"])
        mr_pass = EvalMetricResult(metric_name="m1", score=1.0, eval_status=EvalStatus.PASSED, threshold=0.5)
        mr_fail = EvalMetricResult(metric_name="m1", score=0.1, eval_status=EvalStatus.FAILED, threshold=0.5)
        cr1 = self._make_case_result("c1", EvalStatus.PASSED, [mr_pass])
        cr2 = self._make_case_result("c2", EvalStatus.FAILED, [mr_fail])
        summary = h.build_summary(es, {"c1": [cr1], "c2": [cr2]}, "agent", 1)
        assert summary["overall_status"] == "failed"

    def test_multiple_runs(self):
        """Test build_summary with multiple runs."""
        h = EvalResultHandler()
        es = self._make_eval_set(["c1"])
        cr1 = self._make_case_result("c1", EvalStatus.PASSED)
        cr2 = self._make_case_result("c1", EvalStatus.FAILED)
        summary = h.build_summary(es, {"c1": [cr1, cr2]}, "agent", 2)
        assert summary["runs"] == 2

    def test_summary_to_export_dict(self):
        """Test summary_to_export_dict converts to camelCase."""
        h = EvalResultHandler()
        es = self._make_eval_set(["c1"])
        mr = EvalMetricResult(metric_name="m1", score=1.0, eval_status=EvalStatus.PASSED, threshold=0.5)
        cr = self._make_case_result("c1", EvalStatus.PASSED, [mr])
        summary = h.build_summary(es, {"c1": [cr]}, "agent", 1)
        export = h.summary_to_export_dict(summary)
        assert "agentName" in export
        assert "evalSetId" in export
        assert "overallStatus" in export
