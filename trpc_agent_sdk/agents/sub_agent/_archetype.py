# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Frozen archetype template describing one kind of sub-agent.

The archetype locks down instruction / tools / model so a call cannot reshape
the sub-agent into something arbitrary. When used with ``SpawnSubAgentTool``,
only ``prompt`` varies at call time; the rest is fixed at registration. When
used with ``DynamicSubAgentTool``, a new archetype is constructed per call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional
from typing import Union

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet

InstructionProvider = Callable[[InvocationContext], Union[str, Awaitable[str]]]
ToolItem = Union[BaseTool, BaseToolSet, Callable[[], Union[BaseTool, BaseToolSet]]]

# Permitted name characters: letters, digits, hyphen, underscore. Allows
# both identifier-style ("plan", "ops_audit") and hyphenated style
# ("general-purpose", "code-guide") so users can pick whichever convention
# matches their archetype catalog.
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class SubAgentArchetype:
    """Template for a kind of sub-agent the parent agent may spawn.

    The two prompt-shaped fields target different audiences and are kept
    distinct so each can be written in the right voice:

    - ``description`` is read by the **parent LLM** when it decides which
      archetype to spawn. The framework may render it into the spawn tool
      description with a trailing ``(Tools: ...)`` suffix for the parent
      LLM to read. Phrase it third-person, focused on selection criteria:
      "Use it for ... Do NOT use it for ... **IMPORTANT:** ...".

    - ``instruction`` is the **sub-agent's** system prompt. Phrase it
      second-person: "You are X. Your role is ... Constraints: ...".

    Other fields:

    - ``tools``: ``None`` = inherit all parent-agent tools at spawn time
      (minus spawn tools, which are always stripped).
      Otherwise, a tuple of ``BaseTool`` / ``BaseToolSet`` instances OR
      zero-arg factory callables (e.g. class references). Factories avoid
      import-time side effects and keep tool state per-spawn.
    - ``model``: ``None`` = always inherited (resolved via
      ``SubAgentConfig.model`` > parent's model at spawn time).
    """

    name: str
    description: str
    instruction: Union[str, InstructionProvider]
    tools: Optional[tuple] = None
    model: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            raise ValueError(f"SubAgentArchetype.name must match {_NAME_RE.pattern!r}, got {self.name!r}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("SubAgentArchetype.description must be a non-empty string")
        if isinstance(self.instruction, str) and not self.instruction.strip():
            raise ValueError("SubAgentArchetype.instruction must be a non-empty string")

        # Coerce tools to a tuple if a list was passed (frozen dataclass + immutability hint).
        if self.tools is not None and not isinstance(self.tools, tuple):
            object.__setattr__(self, "tools", tuple(self.tools))

    def model_or(self, fallback: Any) -> Any:
        """Return ``self.model`` if set, otherwise ``fallback``."""
        return self.model if self.model else fallback


__all__ = ["SubAgentArchetype", "InstructionProvider", "ToolItem"]
