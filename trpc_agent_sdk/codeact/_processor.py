# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# This CodeAct implementation is inspired by and adapted from
# NVIDIA NeMo Labs OO Agents (NOOA):
# https://github.com/NVIDIA-NeMo/labs-OO-Agents
#
# Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""LLM response processing for the CodeAct control runtime."""

from __future__ import annotations

import copy
import re
from collections.abc import AsyncGenerator

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import EventActions
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Outcome
from trpc_agent_sdk.types import Part

from ._config import parse_codeact_result
from ._config import prepare_codeact_code
from ._config import CodeActConfig

_PYTHON_FENCE_PATTERN = re.compile(
    r"(?P<fence>`{3,}|~{3,})[ \t]*(?P<language>python|py|python3)[ \t]*\n"
    r"(?P<code>.*?)(?:\n)?(?P=fence)",
    re.IGNORECASE | re.DOTALL,
)


class CodeActResponseProcessor:
    """Extract and execute model-generated CodeAct Python cells."""

    @staticmethod
    async def run_async(
        invocation_context: InvocationContext,
        llm_response: LlmResponse,
    ) -> AsyncGenerator[Event, None]:
        """Process one complete model response."""
        if llm_response.partial or not llm_response.content:
            return

        agent = invocation_context.agent
        code_act = getattr(agent, "code_act", None)
        if code_act is None or not isinstance(code_act, CodeActConfig):
            return

        runtime = code_act.runtime
        response_content = copy.deepcopy(llm_response.content)
        cells = _extract_first_cell_and_truncate_content(response_content)
        if not cells:
            return

        result = await runtime.execute(
            invocation_context,
            [prepare_codeact_code(cell) for cell in cells],
        )

        yield Event(
            invocation_id=invocation_context.invocation_id,
            author=agent.name,
            branch=invocation_context.branch,
            content=Content(
                role="model",
                parts=[Part.from_executable_code(code=cell, language="PYTHON") for cell in cells],
            ),
            actions=EventActions(),
        )

        if result.outcome == Outcome.OUTCOME_OK:
            parsed_result = parse_codeact_result(result.output, getattr(agent, "output_schema", None))
            if parsed_result.is_final:
                yield Event(
                    invocation_id=invocation_context.invocation_id,
                    author=agent.name,
                    branch=invocation_context.branch,
                    content=Content(role="model", parts=[Part(text=parsed_result.serialized)]),
                    object="codeact.result",
                )
                _retain_non_code_parts(llm_response, response_content)
                return
            if parsed_result.validation_error:
                yield Event(
                    invocation_id=invocation_context.invocation_id,
                    author=agent.name,
                    branch=invocation_context.branch,
                    content=Content(
                        role="user",
                        parts=[
                            Part(text=(f"{parsed_result.validation_error}\n"
                                       "Fix the value and call return_result again in a new Python cell."))
                        ],
                    ),
                    visible=False,
                    object="codeact.validation_error",
                )
                _retain_non_code_parts(llm_response, response_content)
                return

        yield Event(
            invocation_id=invocation_context.invocation_id,
            author=agent.name,
            branch=invocation_context.branch,
            content=Content(
                role="user",
                parts=[Part.from_code_execution_result(outcome=result.outcome, output=result.output)],
            ),
            actions=EventActions(),
        )
        _retain_non_code_parts(llm_response, response_content)


def _extract_first_cell_and_truncate_content(content: Content) -> list[str]:
    """Extract one Python cell and discard speculative trailing content."""
    for index, part in enumerate(content.parts or []):
        if part.executable_code and not getattr(part, "thought", False):
            language = (part.executable_code.language or "python").lower()
            if language in ("python", "py", "python3"):
                content.parts = [
                    preceding for preceding in content.parts[:index]
                    if preceding.text and not getattr(preceding, "thought", False)
                ]
                return [part.executable_code.code or ""]

    text_parts = [part for part in content.parts or [] if part.text and not getattr(part, "thought", False)]
    if not text_parts:
        return []

    response_text = "\n".join(part.text for part in text_parts)
    match = _PYTHON_FENCE_PATTERN.search(response_text)
    if not match:
        return []

    text_before = response_text[:match.start()].strip()
    content.parts = [Part(text=text_before)] if text_before else []
    return [match.group("code").strip()]


def _retain_non_code_parts(llm_response: LlmResponse, response_content: Content) -> None:
    """Keep model text and provider function calls after CodeAct extraction."""
    retained_parts = [copy.deepcopy(part) for part in response_content.parts or [] if part.text]
    if llm_response.content and llm_response.content.parts:
        retained_parts.extend(copy.deepcopy(part) for part in llm_response.content.parts if part.function_call)
    if retained_parts:
        llm_response.content = Content(
            role=llm_response.content.role if llm_response.content else "model",
            parts=retained_parts,
        )
    else:
        llm_response.content = None
