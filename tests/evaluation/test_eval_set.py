# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for EvalSet (_eval_set)."""

import pytest

pytest.importorskip("trpc_agent_sdk._runners", reason="trpc_agent_sdk._runners not yet implemented")

from trpc_agent_sdk.evaluation import EvalSet


class TestEvalSet:
    """Test suite for EvalSet model."""

    def test_eval_set_minimal(self):
        """Test EvalSet with required fields only."""
        s = EvalSet(eval_set_id="set1", eval_cases=[])
        assert s.eval_set_id == "set1"
        assert s.eval_cases == []
        assert s.name is None
        assert s.description is None
        assert s.app_name is None

    def test_eval_set_with_name_and_description(self):
        """Test EvalSet with optional name and description."""
        s = EvalSet(
            eval_set_id="set1",
            eval_cases=[],
            name="My Eval Set",
            description="Tests weather agent.",
        )
        assert s.name == "My Eval Set"
        assert s.description == "Tests weather agent."

    def test_eval_set_with_app_name(self):
        """Test EvalSet with app_name."""
        s = EvalSet(
            eval_set_id="set1",
            eval_cases=[],
            app_name="my_agent",
        )
        assert s.app_name == "my_agent"
