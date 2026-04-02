# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for BuiltInPlanner."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.planners._built_in_planner import BuiltInPlanner
from trpc_agent_sdk.types import GenerateContentConfig, Part, ThinkingConfig


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestBuiltInPlannerInit:
    def test_stores_thinking_config(self):
        tc = ThinkingConfig(include_thoughts=True)
        planner = BuiltInPlanner(thinking_config=tc)
        assert planner.thinking_config is tc

    def test_requires_thinking_config_kwarg(self):
        with pytest.raises(TypeError):
            BuiltInPlanner()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# apply_thinking_config
# ---------------------------------------------------------------------------


class TestApplyThinkingConfig:
    def test_applies_config_to_request_without_existing_config(self):
        tc = ThinkingConfig(include_thoughts=True)
        planner = BuiltInPlanner(thinking_config=tc)
        request = LlmRequest()
        assert request.config is None

        planner.apply_thinking_config(request)

        assert request.config is not None
        assert request.config.thinking_config is tc

    def test_applies_config_to_request_with_existing_config(self):
        tc = ThinkingConfig(include_thoughts=True)
        planner = BuiltInPlanner(thinking_config=tc)
        existing_config = GenerateContentConfig(temperature=0.7)
        request = LlmRequest(config=existing_config)

        planner.apply_thinking_config(request)

        assert request.config is existing_config
        assert request.config.thinking_config is tc
        assert request.config.temperature == 0.7

    def test_noop_when_thinking_config_is_none(self):
        planner = BuiltInPlanner(thinking_config=None)
        request = LlmRequest()

        planner.apply_thinking_config(request)

        assert request.config is None

    def test_overwrites_previous_thinking_config(self):
        tc_old = ThinkingConfig(include_thoughts=False)
        tc_new = ThinkingConfig(include_thoughts=True)
        planner = BuiltInPlanner(thinking_config=tc_new)
        request = LlmRequest(config=GenerateContentConfig(thinking_config=tc_old))

        planner.apply_thinking_config(request)

        assert request.config.thinking_config is tc_new


# ---------------------------------------------------------------------------
# build_planning_instruction
# ---------------------------------------------------------------------------


class TestBuildPlanningInstruction:
    def _make_context(self):
        return Mock()

    def test_returns_none(self):
        tc = ThinkingConfig(include_thoughts=True)
        planner = BuiltInPlanner(thinking_config=tc)
        request = LlmRequest()

        result = planner.build_planning_instruction(self._make_context(), request)

        assert result is None

    def test_applies_thinking_config_as_side_effect(self):
        tc = ThinkingConfig(include_thoughts=True)
        planner = BuiltInPlanner(thinking_config=tc)
        request = LlmRequest()

        planner.build_planning_instruction(self._make_context(), request)

        assert request.config is not None
        assert request.config.thinking_config is tc

    def test_no_config_when_thinking_config_is_none(self):
        planner = BuiltInPlanner(thinking_config=None)
        request = LlmRequest()

        result = planner.build_planning_instruction(self._make_context(), request)

        assert result is None
        assert request.config is None


# ---------------------------------------------------------------------------
# process_planning_response
# ---------------------------------------------------------------------------


class TestProcessPlanningResponse:
    def _make_context(self):
        return Mock()

    def test_returns_none_with_parts(self):
        planner = BuiltInPlanner(thinking_config=ThinkingConfig(include_thoughts=True))
        parts = [Part(text="hello")]

        result = planner.process_planning_response(self._make_context(), parts)

        assert result is None

    def test_returns_none_with_empty_parts(self):
        planner = BuiltInPlanner(thinking_config=ThinkingConfig(include_thoughts=True))

        result = planner.process_planning_response(self._make_context(), [])

        assert result is None

    def test_returns_none_when_partial_true(self):
        planner = BuiltInPlanner(thinking_config=ThinkingConfig(include_thoughts=True))
        parts = [Part(text="streaming")]

        result = planner.process_planning_response(self._make_context(), parts, is_partial=True)

        assert result is None

    def test_returns_none_when_partial_false(self):
        planner = BuiltInPlanner(thinking_config=ThinkingConfig(include_thoughts=True))
        parts = [Part(text="complete")]

        result = planner.process_planning_response(self._make_context(), parts, is_partial=False)

        assert result is None
