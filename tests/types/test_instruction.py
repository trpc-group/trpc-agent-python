# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tests for trpc_agent_sdk.types._instruction.

Covers:
    - InstructionMetadata: defaults, field population
    - Instruction: construction, compile() template substitution, __call__ protocol
"""

from __future__ import annotations

from unittest.mock import MagicMock

from trpc_agent_sdk.types._instruction import Instruction, InstructionMetadata


# ---------------------------------------------------------------------------
# InstructionMetadata
# ---------------------------------------------------------------------------
class TestInstructionMetadata:
    """Tests for the InstructionMetadata dataclass."""

    def test_required_fields(self):
        meta = InstructionMetadata(name="prompt_v1", version=3)
        assert meta.name == "prompt_v1"
        assert meta.version == 3

    def test_default_type(self):
        meta = InstructionMetadata(name="x", version=1)
        assert meta.type == "text"

    def test_custom_type(self):
        meta = InstructionMetadata(name="x", version=1, type="chat")
        assert meta.type == "chat"

    def test_default_labels(self):
        meta = InstructionMetadata(name="x", version=1)
        assert meta.labels == []

    def test_labels_populated(self):
        meta = InstructionMetadata(name="x", version=1, labels=["production", "v2"])
        assert meta.labels == ["production", "v2"]

    def test_labels_default_is_independent(self):
        m1 = InstructionMetadata(name="a", version=1)
        m2 = InstructionMetadata(name="b", version=2)
        m1.labels.append("prod")
        assert "prod" not in m2.labels

    def test_default_config(self):
        meta = InstructionMetadata(name="x", version=1)
        assert meta.config == {}

    def test_config_populated(self):
        cfg = {"temperature": 0.7, "max_tokens": 100}
        meta = InstructionMetadata(name="x", version=1, config=cfg)
        assert meta.config == cfg

    def test_config_default_is_independent(self):
        m1 = InstructionMetadata(name="a", version=1)
        m2 = InstructionMetadata(name="b", version=2)
        m1.config["key"] = "val"
        assert "key" not in m2.config


# ---------------------------------------------------------------------------
# Instruction
# ---------------------------------------------------------------------------
class TestInstruction:
    """Tests for the Instruction dataclass."""

    def _make_instruction(self, text: str = "Hello {{name}}") -> Instruction:
        meta = InstructionMetadata(name="test", version=1)
        return Instruction(instruction=text, metadata=meta)

    def test_construction(self):
        instr = self._make_instruction("plain text")
        assert instr.instruction == "plain text"
        assert instr.metadata.name == "test"

    def test_compile_single_variable(self):
        instr = self._make_instruction("Hello {{name}}")
        result = instr.compile(name="Alice")
        assert result == "Hello Alice"

    def test_compile_multiple_variables(self):
        instr = self._make_instruction("{{greeting}}, {{name}}!")
        result = instr.compile(greeting="Hi", name="Bob")
        assert result == "Hi, Bob!"

    def test_compile_no_variables(self):
        instr = self._make_instruction("No placeholders")
        result = instr.compile()
        assert result == "No placeholders"

    def test_compile_missing_variable_keeps_placeholder(self):
        instr = self._make_instruction("Hello {{name}}")
        result = instr.compile()
        assert result == "Hello {{name}}"

    def test_compile_partial_variables(self):
        instr = self._make_instruction("{{a}} and {{b}}")
        result = instr.compile(a="X")
        assert result == "X and {{b}}"

    def test_compile_does_not_mutate_original(self):
        instr = self._make_instruction("{{x}}")
        instr.compile(x="replaced")
        assert instr.instruction == "{{x}}"

    def test_compile_repeated_placeholder(self):
        instr = self._make_instruction("{{v}} + {{v}}")
        result = instr.compile(v="1")
        assert result == "1 + 1"

    def test_call_returns_instruction(self):
        instr = self._make_instruction("system prompt")
        ctx = MagicMock()
        assert instr(ctx) == "system prompt"

    def test_call_ignores_ctx(self):
        instr = self._make_instruction("text")
        assert instr(None) == "text"
        assert instr("anything") == "text"
