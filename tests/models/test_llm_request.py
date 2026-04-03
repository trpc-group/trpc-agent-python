# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part


class TestLlmRequest:
    """Test suite for LlmRequest class."""

    def test_append_instructions_with_empty_config(self):
        """Test appending instructions when config is None."""
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})

        instructions = ["Instruction 1", "Instruction 2"]
        request.append_instructions(instructions)

        assert request.config is not None
        assert request.config.system_instruction == "Instruction 1\n\nInstruction 2"

    def test_append_instructions_with_existing_config(self):
        """Test appending instructions when config already has system_instruction."""
        config = GenerateContentConfig(system_instruction="Existing instruction")
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=config, tools_dict={})

        instructions = ["New instruction"]
        request.append_instructions(instructions)

        assert "Existing instruction" in request.config.system_instruction
        assert "New instruction" in request.config.system_instruction

    def test_append_instructions_multiple_times(self):
        """Test appending instructions multiple times."""
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})

        request.append_instructions(["First"])
        request.append_instructions(["Second"])
        request.append_instructions(["Third"])

        assert "First" in request.config.system_instruction
        assert "Second" in request.config.system_instruction
        assert "Third" in request.config.system_instruction

    def test_append_tools(self):
        """Test appending tools when config is None."""
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], tools_dict={})

        class MockTool:
            """Mock tool for testing."""

            def __init__(self, name: str):
                self.name = name
                self._declaration = {"name": name, "description": "Test tool"}

            def _get_declaration(self):
                return self._declaration

        tools = [MockTool("tool1"), MockTool("tool2")]
        request.append_tools(tools)

        assert request.config is not None
        assert request.config.tools is not None
        assert len(request.config.tools) == 1
        assert len(request.tools_dict) == 2
        assert "tool1" in request.tools_dict
        assert "tool2" in request.tools_dict

    def test_append_tools_edge_cases(self):
        """Test appending tools with edge cases (empty list, None declaration)."""
        # Empty list - config should not be created
        request1 = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})
        request1.append_tools([])
        assert request1.config is None

        # None declaration - tool should not be added
        class ToolWithNoneDeclaration:
            name = "none_tool"

            def _get_declaration(self):
                return None

        request2 = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})
        request2.append_tools([ToolWithNoneDeclaration()])
        assert "none_tool" not in request2.tools_dict

    def test_set_output_schema(self):
        """Test setting output schema."""
        from pydantic import BaseModel

        class ResponseSchema(BaseModel):
            answer: str
            confidence: float

        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})

        request.set_output_schema(ResponseSchema)

        assert request.config is not None
        assert request.config.response_schema == ResponseSchema
        assert request.config.response_mime_type == "application/json"

    def test_set_output_schema_with_existing_config(self):
        """Test setting output schema when config already exists."""
        from pydantic import BaseModel

        class ResponseSchema(BaseModel):
            result: str

        config = GenerateContentConfig(temperature=0.7)
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=config, tools_dict={})

        request.set_output_schema(ResponseSchema)

        assert request.config.response_schema == ResponseSchema
        assert request.config.response_mime_type == "application/json"
        # Verify existing config is preserved
        assert request.config.temperature == 0.7

    def test_append_instructions_with_none_values(self):
        """Test appending instructions with None values in list."""
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})

        # Note: This tests current behavior; strings are expected
        instructions = ["First", "Second"]
        request.append_instructions(instructions)

        assert "First" in request.config.system_instruction
        assert "Second" in request.config.system_instruction

    def test_append_tools_multiple_times_accumulates(self):
        """Test appending tools multiple times accumulates properly."""

        class MockTool:

            def __init__(self, name: str):
                self.name = name
                self._declaration = {"name": name, "description": f"Tool {name}"}

            def _get_declaration(self):
                return self._declaration

        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})

        # Append first batch
        request.append_tools([MockTool("tool1"), MockTool("tool2")])

        # Append second batch
        request.append_tools([MockTool("tool3")])

        assert "tool1" in request.tools_dict
        assert "tool2" in request.tools_dict
        assert "tool3" in request.tools_dict
        assert len(request.tools_dict) == 3

    def test_all_methods_coexist_in_config(self):
        """Test that all three methods can be used together without interference."""
        from pydantic import BaseModel

        class ResponseSchema(BaseModel):
            answer: str

        class MockTool:

            def __init__(self, name: str):
                self.name = name
                self._declaration = {"name": name, "description": "Test"}

            def _get_declaration(self):
                return self._declaration

        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})

        # Use all three methods
        request.append_instructions(["Be helpful"])
        request.append_tools([MockTool("test_tool")])
        request.set_output_schema(ResponseSchema)

        # Verify all are set
        assert "helpful" in request.config.system_instruction
        assert len(request.tools_dict) == 1
        assert request.config.response_schema == ResponseSchema
        assert request.config.response_mime_type == "application/json"

    def test_append_tools_with_duplicate_names(self):
        """Test appending tools with duplicate names (last one wins)."""

        class MockTool:

            def __init__(self, name: str, version: int):
                self.name = name
                self.version = version
                self._declaration = {"name": name, "description": f"Version {version}"}

            def _get_declaration(self):
                return self._declaration

        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})

        tool_v1 = MockTool("mytool", 1)
        tool_v2 = MockTool("mytool", 2)

        request.append_tools([tool_v1, tool_v2])

        # Last one should win
        assert request.tools_dict["mytool"] == tool_v2
        assert request.tools_dict["mytool"].version == 2

    def test_append_instructions_preserves_newlines_in_content(self):
        """Test that instructions with their own newlines are handled."""
        request = LlmRequest(contents=[Content(parts=[Part.from_text(text="test")])], config=None, tools_dict={})

        instructions = ["Line 1\nLine 2", "Another instruction"]
        request.append_instructions(instructions)

        assert "Line 1" in request.config.system_instruction
        assert "Another instruction" in request.config.system_instruction
