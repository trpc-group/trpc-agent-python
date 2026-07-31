# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Fake model that replays canned responses for Agent-driven code review pipeline.

Enables full Agent testing without real LLM API calls.
"""

from typing import AsyncGenerator, List, Optional

from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import Content, Part


class FakeModel(LLMModel):
    """A fake LLM model that returns pre-recorded agent actions.

    Used to drive the LlmAgent through skill_load → skill_run → report
    without calling any real LLM API.
    """

    def __init__(self, model_name: str = "fake-model", **kwargs):
        super().__init__(model_name=model_name, **kwargs)
        self._steps: list[LlmResponse] = []
        self._step_index = 0

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"fake-.*", r"mock-.*"]

    def set_steps(self, steps: list[LlmResponse]):
        """Configure the sequence of responses this model will return."""
        self._steps = steps
        self._step_index = 0

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: Optional[InvocationContext] = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        if self._steps and self._step_index < len(self._steps):
            resp = self._steps[self._step_index]
            self._step_index += 1
            yield resp
        else:
            yield LlmResponse(
                content=Content(parts=[Part(text="Review complete.")]),
                partial=False,
            )

    def validate_request(self, request: LlmRequest) -> None:
        pass


def build_code_review_steps(diff_path: str) -> list[LlmResponse]:
    """Build a sequence of model responses that drive the code review pipeline."""
    return [
        LlmResponse(
            content=Content(
                role="model",
                parts=[Part.from_function_call(
                    name="skill_load",
                    args={"skill_name": "code-review"}
                )]
            ),
            partial=False,
        ),
        LlmResponse(
            content=Content(
                role="model",
                parts=[Part.from_function_call(
                    name="skill_run",
                    args={"skill": "code-review", "command": f"python parse_diff.py {diff_path}"}
                )]
            ),
            partial=False,
        ),
        LlmResponse(
            content=Content(
                role="model",
                parts=[Part(text=(
                    "Code review completed. Findings have been stored.\n\n"
                    "FINDINGS_JSON\n"
                    "```json\n"
                    '[{"severity": "high", "category": "security", '
                    '"file": "a.py", "line": 1, '
                    '"title": "Hardcoded credential (LLM)", '
                    '"evidence": "token = \\"abc123\\"", '
                    '"recommendation": "Use env var", "confidence": 0.9}]\n'
                    "```"
                ))]
            ),
            partial=False,
        ),
    ]
