# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Directly reuse the types from adk-python
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Plan-ReAct Planner module for TRPC Agent framework.

This module provides the PlanReActPlanner class which enforces a structured
Plan-Reasoning-Action workflow using XML-like tags to organize model responses.
"""

from __future__ import annotations

from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.abc import PlannerABC as BasePlanner
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Part

# Planning workflow tags
PLANNING_TAG = "/*PLANNING*/"
REPLANNING_TAG = "/*REPLANNING*/"
REASONING_TAG = "/*REASONING*/"
ACTION_TAG = "/*ACTION*/"
FINAL_ANSWER_TAG = "/*FINAL_ANSWER*/"


class PlanReActPlanner(BasePlanner):
    """Plan-ReAct planner that constrains the LLM response to generate a plan before any action/observation.

    This planner enforces a structured workflow:
    1. PLANNING: Generate an initial plan to solve the problem
    2. ACTION: Execute tools/actions based on the plan
    3. REASONING: Analyze results and determine next steps
    4. REPLANNING: Revise the plan if needed based on results
    5. FINAL_ANSWER: Provide the final response to the user

    Note: This planner does not require the model to support built-in thinking
    features or setting the thinking config.
    """

    def __init__(self):
        """Initialize the planner with streaming state tracking."""
        super().__init__()
        # Streaming state tracking
        self._accumulated_text = ""
        self._current_section = "planning"  # Start with planning section to make thought=True from beginning
        self._is_in_final_answer = False

    @override
    def build_planning_instruction(
        self,
        context: InvocationContext,
        llm_request: LlmRequest,
    ) -> str:
        """Builds the structured planning instruction for Plan-ReAct workflow.

        Args:
            context: The invocation context
            llm_request: The LLM request

        Returns:
            Comprehensive planning instruction for structured reasoning
        """
        return self._build_nl_planner_instruction()

    @override
    def process_planning_response(
        self,
        context: InvocationContext,
        response_parts: List[Part],
        is_partial: bool = False,
    ) -> Optional[List[Part]]:
        """Processes the LLM response to handle planning workflow with streaming support.

        This method:
        1. Accumulates streaming content when is_partial=True
        2. Separates planning/reasoning content from tool calls
        3. Marks planning content as "thoughts" (internal reasoning) based on accumulated text
        4. Preserves tool calls for execution
        5. Extracts final answers for user display
        6. Clears accumulated text when encountering final answer in complete response

        Args:
            context: The invocation context
            response_parts: The LLM response parts to process
            is_partial: Whether this is a partial response (streaming)

        Returns:
            Processed response parts with proper categorization
        """
        if not response_parts:
            return None

        preserved_parts = []
        first_fc_part_index = -1

        # Process each part to separate content types
        for i in range(len(response_parts)):
            # Stop at the first (group of) function calls
            if response_parts[i].function_call:
                # Ignore and filter out function calls with empty names
                if not response_parts[i].function_call.name:
                    continue
                preserved_parts.append(response_parts[i])
                first_fc_part_index = i
                break

            # Handle text parts with streaming awareness
            self._handle_non_function_call_parts(response_parts[i], preserved_parts, is_partial)

        # Include remaining function calls after the first one
        if first_fc_part_index > 0:
            j = first_fc_part_index + 1
            while j < len(response_parts):
                if response_parts[j].function_call:
                    preserved_parts.append(response_parts[j])
                    j += 1
                else:
                    break

        # Clear accumulated text when we encounter final answer in complete response
        if not is_partial and self._is_in_final_answer:
            self._reset_streaming_state()

        return preserved_parts

    def _split_by_last_pattern(self, text: str, separator: str) -> tuple[str, str]:
        """Splits the text by the last occurrence of the separator.

        Args:
            text: The text to split
            separator: The separator to split on

        Returns:
            A tuple containing the text before the last separator and the text after
            the last separator
        """
        index = text.rfind(separator)
        if index == -1:
            return text, ""
        return text[:index + len(separator)], text[index + len(separator):]

    def _handle_non_function_call_parts(self, response_part: Part, preserved_parts: List[Part],
                                        is_partial: bool) -> None:
        """Handles text parts of the response with streaming awareness.

        Args:
            response_part: The response part to handle
            preserved_parts: The mutable list of parts to store the processed parts in
            is_partial: Whether this is a partial response (streaming)
        """
        if not response_part.text:
            preserved_parts.append(response_part)
            return

        # Accumulate text for streaming responses
        if is_partial:
            self._accumulated_text += response_part.text

            # Update current section based on accumulated text
            self._update_current_section()

            # Mark as thought if we're in a thinking section
            if self._should_mark_as_thought():
                self._mark_as_thought(response_part)

            preserved_parts.append(response_part)
        else:
            # For complete responses, use the original logic
            self._handle_complete_text_part(response_part, preserved_parts)

    def _handle_complete_text_part(self, response_part: Part, preserved_parts: List[Part]) -> None:
        """Handles complete (non-streaming) text parts.

        Args:
            response_part: The response part to handle
            preserved_parts: The mutable list of parts to store the processed parts in
        """
        if response_part.text and FINAL_ANSWER_TAG in response_part.text:
            # Split reasoning and final answer
            reasoning_text, final_answer_text = self._split_by_last_pattern(response_part.text, FINAL_ANSWER_TAG)

            if reasoning_text:
                reasoning_part = Part(text=reasoning_text)
                self._mark_as_thought(reasoning_part)
                preserved_parts.append(reasoning_part)

            if final_answer_text:
                preserved_parts.append(Part(text=final_answer_text))
        else:
            response_text = response_part.text or ""
            # If the part is a text part with a planning/reasoning/action tag,
            # label it as reasoning
            if response_text and any(
                    response_text.startswith(tag) for tag in [PLANNING_TAG, REASONING_TAG, ACTION_TAG, REPLANNING_TAG]):
                self._mark_as_thought(response_part)
            preserved_parts.append(response_part)

    def _mark_as_thought(self, response_part: Part) -> None:
        """Marks the response part as thought.

        Args:
            response_part: The mutable response part to mark as thought
        """
        if response_part.text:
            response_part.thought = True

    def _update_current_section(self) -> None:
        """Updates the current section based on accumulated text."""
        # Check for section tags in accumulated text
        if FINAL_ANSWER_TAG in self._accumulated_text:
            self._is_in_final_answer = True
            self._current_section = None  # Final answer is not a thought section
        elif PLANNING_TAG in self._accumulated_text:
            self._current_section = "planning"
            self._is_in_final_answer = False
        elif REPLANNING_TAG in self._accumulated_text:
            self._current_section = "replanning"
            self._is_in_final_answer = False
        elif REASONING_TAG in self._accumulated_text:
            self._current_section = "reasoning"
            self._is_in_final_answer = False
        elif ACTION_TAG in self._accumulated_text:
            self._current_section = "action"
            self._is_in_final_answer = False

    def _should_mark_as_thought(self) -> bool:
        """Determines if the current content should be marked as thought.

        Returns:
            True if the current section is a thinking section, False otherwise
        """
        # Always mark as thought unless we're explicitly in final answer section
        # This ensures thought=True from the very first chunk
        return not self._is_in_final_answer

    def _reset_streaming_state(self) -> None:
        """Resets the streaming state for the next response."""
        self._accumulated_text = ""
        self._current_section = None
        self._is_in_final_answer = False

    def _build_nl_planner_instruction(self) -> str:
        """Builds the NL planner instruction for the Plan-ReAct planner.

        Returns:
            NL planner system instruction with structured workflow guidance
        """
        high_level_preamble = f"""
When answering the question, try to leverage the available tools to gather the information instead of your memorized knowledge.

Follow this process when answering the question: (1) first come up with a plan in natural language text format; (2) Then use tools to execute the plan and provide reasoning between tool code snippets to make a summary of current state and next step. Tool code snippets and reasoning should be interleaved with each other. (3) In the end, return one final answer.

Follow this format when answering the question: (1) The planning part should be under {PLANNING_TAG}. (2) The tool code snippets should be under {ACTION_TAG}, and the reasoning parts should be under {REASONING_TAG}. (3) The final answer part should be under {FINAL_ANSWER_TAG}.
"""

        planning_preamble = f"""
Below are the requirements for the planning:
The plan is made to answer the user query if following the plan. The plan is coherent and covers all aspects of information from user query, and only involves the tools that are accessible by the agent. The plan contains the decomposed steps as a numbered list where each step should use one or multiple available tools. By reading the plan, you can intuitively know which tools to trigger or what actions to take.
If the initial plan cannot be successfully executed, you should learn from previous execution results and revise your plan. The revised plan should be under {REPLANNING_TAG}. Then use tools to follow the new plan.
"""

        reasoning_preamble = """
Below are the requirements for the reasoning:
The reasoning makes a summary of the current trajectory based on the user query and tool outputs. Based on the tool outputs and plan, the reasoning also comes up with instructions to the next steps, making the trajectory closer to the final answer.
"""

        final_answer_preamble = """
Below are the requirements for the final answer:
The final answer should be precise and follow query formatting requirements. Some queries may not be answerable with the available tools and information. In those cases, inform the user why you cannot process their query and ask for more information.
"""

        # Tool code requirements
        tool_code_without_python_libraries_preamble = """
Below are the requirements for the tool code:

**Custom Tools:** The available tools are described in the context and can be directly used.
- Code must be valid self-contained Python snippets with no imports and no references to tools or Python libraries that are not in the context.
- You cannot use any parameters or fields that are not explicitly defined in the APIs in the context.
- The code snippets should be readable, efficient, and directly relevant to the user query and reasoning steps.
- When using the tools, you should use the library name together with the function name, e.g., vertex_search.search().
- If Python libraries are not provided in the context, NEVER write your own code other than the function calls using the provided tools.
"""

        user_input_preamble = """
VERY IMPORTANT instruction that you MUST follow in addition to the above instructions:

You should ask for clarification if you need more information to answer the question.
You should prefer using the information available in the context instead of repeated tool use.
"""

        return "\n\n".join([
            high_level_preamble,
            planning_preamble,
            reasoning_preamble,
            final_answer_preamble,
            tool_code_without_python_libraries_preamble,
            user_input_preamble,
        ])
