# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for PlanReActPlanner."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.planners._plan_re_act_planner import (
    ACTION_TAG,
    FINAL_ANSWER_TAG,
    PLANNING_TAG,
    REASONING_TAG,
    REPLANNING_TAG,
    PlanReActPlanner,
)
from trpc_agent_sdk.types import FunctionCall, Part


@pytest.fixture
def planner():
    return PlanReActPlanner()


@pytest.fixture
def ctx():
    return Mock()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_planning_tag(self):
        assert PLANNING_TAG == "/*PLANNING*/"

    def test_replanning_tag(self):
        assert REPLANNING_TAG == "/*REPLANNING*/"

    def test_reasoning_tag(self):
        assert REASONING_TAG == "/*REASONING*/"

    def test_action_tag(self):
        assert ACTION_TAG == "/*ACTION*/"

    def test_final_answer_tag(self):
        assert FINAL_ANSWER_TAG == "/*FINAL_ANSWER*/"


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_state(self, planner):
        assert planner._accumulated_text == ""
        assert planner._current_section == "planning"
        assert planner._is_in_final_answer is False


# ---------------------------------------------------------------------------
# build_planning_instruction
# ---------------------------------------------------------------------------


class TestBuildPlanningInstruction:
    def test_returns_non_empty_string(self, planner, ctx):
        request = LlmRequest()
        result = planner.build_planning_instruction(ctx, request)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_instruction_contains_all_tags(self, planner, ctx):
        result = planner.build_planning_instruction(ctx, LlmRequest())
        assert PLANNING_TAG in result
        assert REPLANNING_TAG in result
        assert REASONING_TAG in result
        assert ACTION_TAG in result
        assert FINAL_ANSWER_TAG in result

    def test_instruction_contains_workflow_guidance(self, planner, ctx):
        result = planner.build_planning_instruction(ctx, LlmRequest())
        assert "plan" in result.lower()
        assert "final answer" in result.lower()
        assert "reasoning" in result.lower()


# ---------------------------------------------------------------------------
# _split_by_last_pattern
# ---------------------------------------------------------------------------


class TestSplitByLastPattern:
    def test_splits_at_last_occurrence(self, planner):
        text = "aXbXc"
        before, after = planner._split_by_last_pattern(text, "X")
        assert before == "aXbX"
        assert after == "c"

    def test_no_match_returns_full_text_and_empty(self, planner):
        before, after = planner._split_by_last_pattern("hello", "X")
        assert before == "hello"
        assert after == ""

    def test_separator_at_end(self, planner):
        before, after = planner._split_by_last_pattern("helloX", "X")
        assert before == "helloX"
        assert after == ""

    def test_separator_at_start(self, planner):
        before, after = planner._split_by_last_pattern("Xhello", "X")
        assert before == "X"
        assert after == "hello"

    def test_multi_char_separator(self, planner):
        text = f"before{FINAL_ANSWER_TAG}after"
        before, after = planner._split_by_last_pattern(text, FINAL_ANSWER_TAG)
        assert before == f"before{FINAL_ANSWER_TAG}"
        assert after == "after"


# ---------------------------------------------------------------------------
# _mark_as_thought
# ---------------------------------------------------------------------------


class TestMarkAsThought:
    def test_marks_text_part_as_thought(self, planner):
        part = Part(text="some reasoning")
        planner._mark_as_thought(part)
        assert part.thought is True

    def test_does_not_mark_empty_text(self, planner):
        part = Part(text=None)
        planner._mark_as_thought(part)
        assert not getattr(part, "thought", None)

    def test_does_not_mark_empty_string(self, planner):
        part = Part(text="")
        planner._mark_as_thought(part)
        assert not getattr(part, "thought", None)


# ---------------------------------------------------------------------------
# _update_current_section
# ---------------------------------------------------------------------------


class TestUpdateCurrentSection:
    def test_detects_planning(self, planner):
        planner._accumulated_text = f"something{PLANNING_TAG}more"
        planner._update_current_section()
        assert planner._current_section == "planning"
        assert planner._is_in_final_answer is False

    def test_detects_replanning(self, planner):
        planner._accumulated_text = f"something{REPLANNING_TAG}more"
        planner._update_current_section()
        assert planner._current_section == "replanning"
        assert planner._is_in_final_answer is False

    def test_detects_reasoning(self, planner):
        planner._accumulated_text = f"something{REASONING_TAG}more"
        planner._update_current_section()
        assert planner._current_section == "reasoning"
        assert planner._is_in_final_answer is False

    def test_detects_action(self, planner):
        planner._accumulated_text = f"something{ACTION_TAG}more"
        planner._update_current_section()
        assert planner._current_section == "action"
        assert planner._is_in_final_answer is False

    def test_detects_final_answer(self, planner):
        planner._accumulated_text = f"something{FINAL_ANSWER_TAG}more"
        planner._update_current_section()
        assert planner._current_section is None
        assert planner._is_in_final_answer is True

    def test_final_answer_takes_precedence(self, planner):
        planner._accumulated_text = f"{PLANNING_TAG}plan{FINAL_ANSWER_TAG}answer"
        planner._update_current_section()
        assert planner._is_in_final_answer is True
        assert planner._current_section is None

    def test_no_tags_keeps_state(self, planner):
        planner._current_section = "reasoning"
        planner._accumulated_text = "no tags here"
        planner._update_current_section()
        assert planner._current_section == "reasoning"


# ---------------------------------------------------------------------------
# _should_mark_as_thought
# ---------------------------------------------------------------------------


class TestShouldMarkAsThought:
    def test_true_when_in_planning(self, planner):
        planner._is_in_final_answer = False
        assert planner._should_mark_as_thought() is True

    def test_false_when_in_final_answer(self, planner):
        planner._is_in_final_answer = True
        assert planner._should_mark_as_thought() is False


# ---------------------------------------------------------------------------
# _reset_streaming_state
# ---------------------------------------------------------------------------


class TestResetStreamingState:
    def test_resets_all_fields(self, planner):
        planner._accumulated_text = "some text"
        planner._current_section = "reasoning"
        planner._is_in_final_answer = True

        planner._reset_streaming_state()

        assert planner._accumulated_text == ""
        assert planner._current_section is None
        assert planner._is_in_final_answer is False


# ---------------------------------------------------------------------------
# _handle_complete_text_part
# ---------------------------------------------------------------------------


class TestHandleCompleteTextPart:
    def test_splits_final_answer_from_reasoning(self, planner):
        text = f"{PLANNING_TAG}my plan{FINAL_ANSWER_TAG}the answer"
        part = Part(text=text)
        preserved = []

        planner._handle_complete_text_part(part, preserved)

        assert len(preserved) == 2
        assert preserved[0].thought is True
        assert PLANNING_TAG in preserved[0].text
        assert preserved[1].text == "the answer"
        assert not getattr(preserved[1], "thought", None)

    def test_only_reasoning_when_no_final_answer_text(self, planner):
        text = f"some reasoning{FINAL_ANSWER_TAG}"
        part = Part(text=text)
        preserved = []

        planner._handle_complete_text_part(part, preserved)

        assert len(preserved) == 1
        assert preserved[0].thought is True

    def test_marks_planning_tag_as_thought(self, planner):
        text = f"{PLANNING_TAG}my plan steps"
        part = Part(text=text)
        preserved = []

        planner._handle_complete_text_part(part, preserved)

        assert len(preserved) == 1
        assert preserved[0].thought is True

    def test_marks_reasoning_tag_as_thought(self, planner):
        part = Part(text=f"{REASONING_TAG}analysis")
        preserved = []
        planner._handle_complete_text_part(part, preserved)
        assert preserved[0].thought is True

    def test_marks_action_tag_as_thought(self, planner):
        part = Part(text=f"{ACTION_TAG}do something")
        preserved = []
        planner._handle_complete_text_part(part, preserved)
        assert preserved[0].thought is True

    def test_marks_replanning_tag_as_thought(self, planner):
        part = Part(text=f"{REPLANNING_TAG}new plan")
        preserved = []
        planner._handle_complete_text_part(part, preserved)
        assert preserved[0].thought is True

    def test_plain_text_not_marked_as_thought(self, planner):
        part = Part(text="just a normal response")
        preserved = []
        planner._handle_complete_text_part(part, preserved)
        assert len(preserved) == 1
        assert not getattr(preserved[0], "thought", None)

    def test_empty_text_not_marked_as_thought(self, planner):
        part = Part(text="")
        preserved = []
        planner._handle_complete_text_part(part, preserved)
        assert len(preserved) == 1
        assert not getattr(preserved[0], "thought", None)


# ---------------------------------------------------------------------------
# _handle_non_function_call_parts (streaming path)
# ---------------------------------------------------------------------------


class TestHandleNonFunctionCallParts:
    def test_streaming_accumulates_text(self, planner):
        part = Part(text="chunk1")
        preserved = []

        planner._handle_non_function_call_parts(part, preserved, is_partial=True)

        assert planner._accumulated_text == "chunk1"
        assert len(preserved) == 1

    def test_streaming_marks_as_thought_in_planning_section(self, planner):
        planner._current_section = "planning"
        planner._is_in_final_answer = False
        part = Part(text="planning text")
        preserved = []

        planner._handle_non_function_call_parts(part, preserved, is_partial=True)

        assert preserved[0].thought is True

    def test_streaming_does_not_mark_thought_in_final_answer(self, planner):
        planner._is_in_final_answer = True
        part = Part(text="answer text")
        preserved = []

        planner._handle_non_function_call_parts(part, preserved, is_partial=True)

        assert not getattr(preserved[0], "thought", None)

    def test_non_streaming_delegates_to_complete_handler(self, planner):
        part = Part(text=f"{PLANNING_TAG}plan content")
        preserved = []

        planner._handle_non_function_call_parts(part, preserved, is_partial=False)

        assert preserved[0].thought is True

    def test_non_text_part_appended_directly(self, planner):
        part = Part(text=None)
        preserved = []

        planner._handle_non_function_call_parts(part, preserved, is_partial=True)

        assert len(preserved) == 1
        assert planner._accumulated_text == ""

    def test_non_text_part_non_streaming(self, planner):
        part = Part(text=None)
        preserved = []

        planner._handle_non_function_call_parts(part, preserved, is_partial=False)

        assert len(preserved) == 1


# ---------------------------------------------------------------------------
# process_planning_response
# ---------------------------------------------------------------------------


class TestProcessPlanningResponse:
    def test_returns_none_for_empty_parts(self, planner, ctx):
        result = planner.process_planning_response(ctx, [])
        assert result is None

    def test_returns_none_for_none_parts(self, planner, ctx):
        result = planner.process_planning_response(ctx, None)
        assert result is None

    def test_complete_text_with_final_answer(self, planner, ctx):
        text = f"{PLANNING_TAG}my plan{FINAL_ANSWER_TAG}the answer"
        parts = [Part(text=text)]
        result = planner.process_planning_response(ctx, parts, is_partial=False)

        assert result is not None
        assert len(result) == 2
        assert result[0].thought is True
        assert result[1].text == "the answer"

    def test_streaming_marks_planning_as_thought(self, planner, ctx):
        parts = [Part(text=f"{PLANNING_TAG}step1")]
        result = planner.process_planning_response(ctx, parts, is_partial=True)

        assert result is not None
        assert len(result) == 1
        assert result[0].thought is True

    def test_streaming_final_answer_not_thought(self, planner, ctx):
        planner._accumulated_text = f"{PLANNING_TAG}plan"
        planner._is_in_final_answer = False

        parts = [Part(text=f"{FINAL_ANSWER_TAG}the answer")]
        result = planner.process_planning_response(ctx, parts, is_partial=True)

        assert result is not None

    def test_preserves_function_call_parts(self, planner, ctx):
        fc = FunctionCall(name="search", args={"q": "test"})
        parts = [Part(function_call=fc)]
        result = planner.process_planning_response(ctx, parts, is_partial=False)

        assert result is not None
        assert len(result) == 1
        assert result[0].function_call.name == "search"

    def test_filters_empty_name_function_calls(self, planner, ctx):
        fc = FunctionCall(name="", args={})
        parts = [Part(function_call=fc)]
        result = planner.process_planning_response(ctx, parts, is_partial=False)

        assert result is not None
        assert len(result) == 0

    def test_mixed_text_and_function_calls(self, planner, ctx):
        fc = FunctionCall(name="tool1", args={})
        parts = [
            Part(text=f"{PLANNING_TAG}step1"),
            Part(function_call=fc),
        ]
        result = planner.process_planning_response(ctx, parts, is_partial=False)

        assert result is not None
        thought_parts = [p for p in result if getattr(p, "thought", None)]
        fc_parts = [p for p in result if p.function_call]
        assert len(thought_parts) >= 1
        assert len(fc_parts) == 1

    def test_multiple_function_calls_after_text(self, planner, ctx):
        fc1 = FunctionCall(name="tool1", args={})
        fc2 = FunctionCall(name="tool2", args={})
        parts = [
            Part(text=f"{PLANNING_TAG}plan"),
            Part(function_call=fc1),
            Part(function_call=fc2),
        ]
        result = planner.process_planning_response(ctx, parts, is_partial=False)

        assert result is not None
        fc_parts = [p for p in result if p.function_call]
        assert len(fc_parts) == 2

    def test_consecutive_fc_at_start_only_first_kept(self, planner, ctx):
        fc1 = FunctionCall(name="tool1", args={})
        fc2 = FunctionCall(name="tool2", args={})
        parts = [
            Part(function_call=fc1),
            Part(function_call=fc2),
        ]
        result = planner.process_planning_response(ctx, parts, is_partial=False)

        assert result is not None
        fc_parts = [p for p in result if p.function_call]
        assert len(fc_parts) == 1
        assert fc_parts[0].function_call.name == "tool1"

    def test_text_after_function_call_ignored(self, planner, ctx):
        fc = FunctionCall(name="tool1", args={})
        parts = [
            Part(function_call=fc),
            Part(text="should be ignored"),
        ]
        result = planner.process_planning_response(ctx, parts, is_partial=False)

        assert result is not None
        text_parts = [p for p in result if p.text]
        assert len(text_parts) == 0

    def test_resets_state_after_complete_final_answer(self, planner, ctx):
        planner._is_in_final_answer = True
        planner._accumulated_text = "some accumulated"
        parts = [Part(text="answer text")]

        planner.process_planning_response(ctx, parts, is_partial=False)

        assert planner._accumulated_text == ""
        assert planner._is_in_final_answer is False

    def test_does_not_reset_state_on_partial(self, planner, ctx):
        planner._is_in_final_answer = True
        planner._accumulated_text = "some accumulated"
        parts = [Part(text="more text")]

        planner.process_planning_response(ctx, parts, is_partial=True)

        assert planner._accumulated_text != ""

    def test_streaming_accumulation_across_chunks(self, planner, ctx):
        planner.process_planning_response(ctx, [Part(text="chunk1 ")], is_partial=True)
        planner.process_planning_response(ctx, [Part(text="chunk2 ")], is_partial=True)

        assert "chunk1" in planner._accumulated_text
        assert "chunk2" in planner._accumulated_text

    def test_function_call_with_none_name_filtered(self, planner, ctx):
        fc = FunctionCall(name=None, args={})
        parts = [Part(function_call=fc)]
        result = planner.process_planning_response(ctx, parts, is_partial=False)

        assert result is not None
        assert len(result) == 0


# ---------------------------------------------------------------------------
# _build_nl_planner_instruction
# ---------------------------------------------------------------------------


class TestBuildNlPlannerInstruction:
    def test_contains_all_sections(self, planner):
        instruction = planner._build_nl_planner_instruction()
        assert "planning" in instruction.lower()
        assert "reasoning" in instruction.lower()
        assert "final answer" in instruction.lower()
        assert "tool" in instruction.lower()

    def test_returns_string(self, planner):
        assert isinstance(planner._build_nl_planner_instruction(), str)
