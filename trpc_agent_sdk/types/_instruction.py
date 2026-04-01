# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Instruction for TRPC Agent framework."""

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List


@dataclass
class InstructionMetadata:
    """Lightweight instruction provenance metadata for trace association.

    Carries the instruction name and version so that each call_llm span can
    be precisely linked to the instruction that produced it,
    regardless of how many instructions have been fetched globally.
    """

    name: str
    """Unique identifier of the instruction in the remote platform."""

    version: int
    """Version number of the instruction snapshot."""

    type: str = "text"
    """Instruction format type: ``"text"`` for plain string, ``"chat"`` for message list."""

    labels: List[str] = field(default_factory=list)
    """Deployment labels associated with this version (e.g. ``["production"]``)."""

    config: Dict[str, Any] = field(default_factory=dict)
    """Platform-side configuration tied to the instruction (e.g. model parameters)."""


@dataclass
class Instruction:
    """Instruction fetch result containing template content and version metadata.

    Implements the ``InstructionProvider`` protocol via ``__call__``, so an
    instance can be passed directly to ``LlmAgent(instruction=result)``.
    """

    instruction: str
    metadata: InstructionMetadata

    def compile(self, **variables: str) -> str:
        """Replace ``{{variable}}`` placeholders and return compiled text."""
        text = self.instruction
        for key, value in variables.items():
            text = text.replace(f"{{{{{key}}}}}", value)
        return text

    def __call__(self, ctx: Any) -> str:
        """``InstructionProvider`` protocol — returns the instruction as a string."""
        return self.instruction
