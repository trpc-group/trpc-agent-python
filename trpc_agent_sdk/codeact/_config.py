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
"""CodeAct configuration and final-result protocol."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

from ._in_process_runtime import InProcessCodeActRuntime
from ._runtime import BaseCodeActRuntime

CODEACT_RESULT_PREFIX = "__TRPC_AGENT_CODEACT_RESULT__:"

_CODEACT_PRELUDE = f"""\
import json as __trpc_codeact_json

def return_result(value):
    \"\"\"Submit the final JSON-serializable result for this CodeAct invocation.\"\"\"
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print({CODEACT_RESULT_PREFIX!r} + __trpc_codeact_json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ))
    raise __trpc_codeact_return_signal()

"""


class CodeActConfig(BaseModel):
    """Configure the CodeAct protocol and its control-code runtime."""

    model_config = ConfigDict(extra="forbid")

    runtime: BaseCodeActRuntime = Field(default_factory=InProcessCodeActRuntime)
    """Runtime used to execute model-generated CodeAct Python cells."""

    max_iterations: int = Field(default=10, ge=1)
    """Maximum model/code iterations for this CodeAct invocation."""

    require_result: bool = True
    """Retry text-only model responses until ``return_result`` is used."""

    text_only_retry_message: str = Field(
        default=("CodeAct requires executable Python. Reply with a fenced ```python "
                 "cell and call return_result(value) only when the final answer is ready."),
        min_length=1,
    )
    """Model-visible feedback used when no executable Python was produced."""

    def build_instruction(
        self,
        output_schema: type[BaseModel] | None = None,
        *,
        supports_tools: bool = False,
    ) -> str:
        """Build the model instruction for the CodeAct execution protocol."""
        lines = [
            "# CodeAct execution mode",
            "Solve the task by writing one executable Python cell in a fenced ```python code block.",
            "Emit exactly one code block per response and end the response immediately after it.",
            "Do not claim or predict a cell's output before the runtime returns the observation.",
            "Each cell runs in the configured CodeAct runtime.",
            "A helper named return_result(value) is available in every Python cell.",
            "Call return_result exactly once only when the task is complete.",
            "The submitted value must be JSON-serializable.",
            "Do not print or imitate internal protocol markers.",
            "Do not use ordinary function/tool calls; Python code is the action interface.",
            f"Complete the task within {self.max_iterations} model/code iterations.",
        ]
        if supports_tools:
            lines.extend([
                "Use print(doc(self)) to discover the available self.<tool>(...) capabilities.",
                "Use print(doc(self.<tool>)) to inspect one capability's detailed declaration.",
                ("Tool names mentioned in task instructions refer to capabilities on self; "
                 "translate tool_name(...) to await self.tool_name(...)."),
                "Call self.<tool>(...) with keyword arguments and await the result.",
                ("Tool results may be ObjectRef values; use doc(value), "
                 "indexing, iteration, or public methods as needed."),
            ])
        if output_schema is not None:
            schema_json = json.dumps(output_schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
            lines.append(f"The return_result value must satisfy this JSON Schema:\n{schema_json}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ParsedCodeActResult:
    """Parsed and validated final value emitted by ``return_result``."""

    value: Any | None = None
    serialized: str | None = None
    validation_error: str | None = None

    @property
    def is_final(self) -> bool:
        """Whether a valid final result was found."""
        return self.serialized is not None


def prepare_codeact_code(code: str) -> str:
    """Inject the ``return_result`` helper into one generated Python cell."""
    return _CODEACT_PRELUDE + code


def parse_codeact_result(
    output: str,
    output_schema: type[BaseModel] | None = None,
) -> ParsedCodeActResult:
    """Parse the last ``return_result`` payload from runtime output."""
    matches = re.findall(re.escape(CODEACT_RESULT_PREFIX) + r"([^\r\n]*)", output or "")
    if not matches:
        return ParsedCodeActResult()

    payload = matches[-1]
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        return ParsedCodeActResult(validation_error=f"return_result emitted invalid JSON: {exc}")

    if output_schema is not None:
        try:
            validated = output_schema.model_validate(value)
        except ValidationError as exc:
            return ParsedCodeActResult(value=value, validation_error=f"return_result validation failed: {exc}")
        value = validated.model_dump(mode="json")

    serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return ParsedCodeActResult(value=value, serialized=serialized)
