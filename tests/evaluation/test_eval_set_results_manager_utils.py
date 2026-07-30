# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for eval set results manager utils (_eval_set_results_manager_utils)."""

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import EvalStatusCounts
from trpc_agent_sdk.evaluation._eval_set_results_manager_utils import _add_status
from trpc_agent_sdk.evaluation._eval_set_results_manager_utils import _normalize_counts
from trpc_agent_sdk.evaluation._eval_set_results_manager_utils import _overall_status_from_counts
from trpc_agent_sdk.evaluation._eval_set_results_manager_utils import _sanitize_eval_set_result_name


class TestSanitizeEvalSetResultName:
    """Test suite for _sanitize_eval_set_result_name."""

    def test_replaces_slash_with_underscore(self):
        """Test / is replaced by _."""
        assert _sanitize_eval_set_result_name("a/b/c") == "a_b_c"

    def test_no_slash_unchanged(self):
        """Test string without / is unchanged."""
        assert _sanitize_eval_set_result_name("my_set") == "my_set"


class TestAddStatus:
    """Test suite for _add_status."""

    def test_add_passed(self):
        """Test _add_status increments passed."""
        c = EvalStatusCounts()
        _add_status(c, EvalStatus.PASSED)
        _add_status(c, EvalStatus.PASSED)
        assert c.passed == 2
        assert c.failed == 0
        assert c.not_evaluated == 0

    def test_add_failed(self):
        """Test _add_status increments failed."""
        c = EvalStatusCounts()
        _add_status(c, EvalStatus.FAILED)
        assert c.failed == 1

    def test_add_not_evaluated(self):
        """Test _add_status increments not_evaluated."""
        c = EvalStatusCounts()
        _add_status(c, EvalStatus.NOT_EVALUATED)
        assert c.not_evaluated == 1


class TestOverallStatusFromCounts:
    """Test suite for _overall_status_from_counts."""

    def test_any_failed_returns_failed(self):
        """Test any failed returns FAILED."""
        c = EvalStatusCounts(passed=2, failed=1, not_evaluated=0)
        assert _overall_status_from_counts(c) == EvalStatus.FAILED

    def test_all_passed_returns_passed(self):
        """Test all passed returns PASSED."""
        c = EvalStatusCounts(passed=2, failed=0, not_evaluated=0)
        assert _overall_status_from_counts(c) == EvalStatus.PASSED

    def test_all_not_evaluated_returns_not_evaluated(self):
        """Test all not_evaluated returns NOT_EVALUATED."""
        c = EvalStatusCounts(passed=0, failed=0, not_evaluated=3)
        assert _overall_status_from_counts(c) == EvalStatus.NOT_EVALUATED


class TestNormalizeCounts:
    """Test suite for _normalize_counts."""

    def test_all_zero_returns_none(self):
        """Test all zeros returns None."""
        c = EvalStatusCounts(passed=0, failed=0, not_evaluated=0)
        assert _normalize_counts(c) is None

    def test_any_nonzero_returns_counts(self):
        """Test any non-zero returns same counts."""
        c = EvalStatusCounts(passed=1, failed=0, not_evaluated=0)
        assert _normalize_counts(c) is c
