# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import pytest
from google.genai.types import FunctionDeclaration
from google.genai.types import Schema
from google.genai.types import Tool
from google.genai.types import Type
from trpc_agent_sdk.models.tool_prompt import JsonToolPrompt
from trpc_agent_sdk.models.tool_prompt import ToolPromptFactory
from trpc_agent_sdk.models.tool_prompt import XmlToolPrompt
from trpc_agent_sdk.models.tool_prompt import get_factory


class TestJsonToolPrompt:
    """Test suite for JsonToolPrompt class - focused on core logic."""

    def test_build_prompt_with_single_tool(self):
        """Test building JSON prompt with a single tool."""
        func_decl = FunctionDeclaration(name="search",
                                        description="Search for information",
                                        parameters=Schema(type=Type.OBJECT,
                                                          properties={
                                                              "query": Schema(type=Type.STRING,
                                                                              description="Search query"),
                                                              "limit": Schema(type=Type.INTEGER,
                                                                              description="Max results"),
                                                          }))
        tool = Tool(function_declarations=[func_decl])

        prompt_builder = JsonToolPrompt()
        prompt = prompt_builder.build_prompt([tool])

        assert "Produce JSON OUTPUT ONLY!" in prompt
        assert '"name": "search"' in prompt
        assert '"description": "Search for information"' in prompt
        assert '"query"' in prompt
        assert '"limit"' in prompt

    def test_build_prompt_with_multiple_tools(self):
        """Test building JSON prompt with multiple tools."""
        func_decl1 = FunctionDeclaration(name="search",
                                         description="Search function",
                                         parameters=Schema(type=Type.OBJECT, properties={}))
        func_decl2 = FunctionDeclaration(name="calculate",
                                         description="Calculate function",
                                         parameters=Schema(type=Type.OBJECT, properties={}))
        tool1 = Tool(function_declarations=[func_decl1])
        tool2 = Tool(function_declarations=[func_decl2])

        prompt_builder = JsonToolPrompt()
        prompt = prompt_builder.build_prompt([tool1, tool2])

        assert '"name": "search"' in prompt
        assert '"name": "calculate"' in prompt

    def test_build_prompt_with_empty_tools(self):
        """Test building JSON prompt with empty tools list or no function declarations."""
        prompt_builder = JsonToolPrompt()

        # Empty tools list
        prompt1 = prompt_builder.build_prompt([])
        assert prompt1 == ""

        # Tool with no function declarations
        tool = Tool(function_declarations=[])
        prompt2 = prompt_builder.build_prompt([tool])
        assert prompt2 == ""

    def test_build_prompt_with_complex_nested_schema(self):
        """Test building JSON prompt with deeply nested schema properties."""
        nested_params = Schema(type=Type.OBJECT,
                               properties={
                                   "query":
                                   Schema(type=Type.STRING),
                                   "filters":
                                   Schema(type=Type.OBJECT,
                                          properties={
                                              "min_price": Schema(type=Type.NUMBER),
                                              "max_price": Schema(type=Type.NUMBER),
                                          }),
                                   "tags":
                                   Schema(type=Type.ARRAY, items=Schema(type=Type.STRING)),
                               })
        func_decl = FunctionDeclaration(name="advanced_search",
                                        description="Advanced search with filters",
                                        parameters=nested_params)
        tool = Tool(function_declarations=[func_decl])

        prompt_builder = JsonToolPrompt()
        prompt = prompt_builder.build_prompt([tool])

        assert '"name": "advanced_search"' in prompt
        assert '"min_price"' in prompt
        assert '"max_price"' in prompt
        assert '"tags"' in prompt

    def test_parse_function_with_valid_json(self):
        """Test parsing a valid JSON function call."""
        content = '{"name": "search", "arguments": {"query": "test"}}'

        prompt_builder = JsonToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 1
        assert function_calls[0].name == "search"
        assert function_calls[0].args == {"query": "test"}

    def test_parse_function_with_multiple_json_objects(self):
        """Test parsing multiple JSON function calls - core brace-counting logic."""
        content = '''
        {"name": "search", "arguments": {"query": "test1"}}
        Some text in between
        {"name": "calculate", "arguments": {"value": 42}}
        '''

        prompt_builder = JsonToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 2
        assert function_calls[0].name == "search"
        assert function_calls[1].name == "calculate"

    def test_parse_function_with_nested_json_arguments(self):
        """Test parsing JSON with deeply nested arguments - complex parsing logic."""
        content = '{"name": "api_call", "arguments": {"payload": {"key": "value", "nested": {"data": 123}}}}'

        prompt_builder = JsonToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 1
        assert function_calls[0].name == "api_call"
        assert function_calls[0].args["payload"]["nested"]["data"] == 123

    def test_parse_function_with_missing_or_invalid_arguments(self):
        """Test parsing JSON with missing or invalid arguments field - edge case handling."""
        # Missing arguments field
        content1 = '{"name": "get_status"}'
        prompt_builder = JsonToolPrompt()
        function_calls1 = prompt_builder.parse_function(content1)
        assert len(function_calls1) == 1
        assert function_calls1[0].name == "get_status"
        assert function_calls1[0].args == {}

        # String arguments (should be handled)
        content2 = '{"name": "test", "arguments": "{\\"key\\": \\"value\\"}"}'
        function_calls2 = prompt_builder.parse_function(content2)
        assert len(function_calls2) == 1
        assert function_calls2[0].name == "test"

    def test_parse_function_with_array_arguments(self):
        """Test parsing JSON with array arguments."""
        content = '{"name": "batch_process", "arguments": {"items": [1, 2, 3, 4, 5]}}'

        prompt_builder = JsonToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 1
        assert function_calls[0].args["items"] == [1, 2, 3, 4, 5]

    def test_parse_function_with_invalid_json(self):
        """Test parsing invalid JSON returns empty list - error resilience."""
        content = '{"name": "test", "arguments": {malformed}'

        prompt_builder = JsonToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert function_calls == []

    def test_parse_function_with_missing_name_field(self):
        """Test parsing JSON without name field - validation logic."""
        content = '{"arguments": {"key": "value"}}'

        prompt_builder = JsonToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert function_calls == []

    def test_parse_function_with_non_string_name(self):
        """Test parsing JSON with non-string name field - type validation."""
        content = '{"name": 123, "arguments": {}}'

        prompt_builder = JsonToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert function_calls == []

    def test_parse_function_with_unicode_characters(self):
        """Test parsing JSON with unicode characters - encoding handling."""
        content = '{"name": "search", "arguments": {"query": "搜索 🔍"}}'

        prompt_builder = JsonToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 1
        assert function_calls[0].args["query"] == "搜索 🔍"


class TestXmlToolPrompt:
    """Test suite for XmlToolPrompt class - focused on core logic."""

    def test_build_prompt_with_single_tool(self):
        """Test building XML prompt with a single tool."""
        func_decl = FunctionDeclaration(name="search",
                                        description="Search for information",
                                        parameters=Schema(type=Type.OBJECT,
                                                          properties={
                                                              "query": Schema(type=Type.STRING,
                                                                              description="Search query"),
                                                          }))
        tool = Tool(function_declarations=[func_decl])

        prompt_builder = XmlToolPrompt()
        prompt = prompt_builder.build_prompt([tool])

        assert "<tools>" in prompt
        assert "<tool_description>" in prompt
        assert "<tool_name>search</tool_name>" in prompt
        assert "Search for information" in prompt
        assert "<function_calls>" in prompt
        assert "<invoke>" in prompt

    def test_build_prompt_includes_tool_use_instructions(self):
        """Test that build_prompt includes proper tool use instructions."""
        func_decl = FunctionDeclaration(name="test_tool",
                                        description="Test tool",
                                        parameters=Schema(type=Type.OBJECT, properties={}))
        tool = Tool(function_declarations=[func_decl])

        prompt_builder = XmlToolPrompt()
        prompt = prompt_builder.build_prompt([tool])

        assert "MUST call them by using below format" in prompt
        assert "<parameters>" in prompt
        assert "$PARAMETER_NAME" in prompt

    def test_parse_function_with_valid_xml(self):
        """Test parsing a valid XML function call."""
        content = """
        <function_calls>
        <invoke>
        <tool_name>search</tool_name>
        <parameters>
        <query>test</query>
        </parameters>
        </invoke>
        </function_calls>
        """

        prompt_builder = XmlToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 1
        assert function_calls[0].name == "search"
        assert function_calls[0].args["query"] == "test"

    def test_parse_function_with_multiple_invokes(self):
        """Test parsing XML with multiple invoke blocks - core multi-call logic."""
        content = """
        <function_calls>
        <invoke>
        <tool_name>search</tool_name>
        <parameters>
        <query>query1</query>
        </parameters>
        </invoke>
        <invoke>
        <tool_name>calculate</tool_name>
        <parameters>
        <value>42</value>
        </parameters>
        </invoke>
        </function_calls>
        """

        prompt_builder = XmlToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 2
        assert function_calls[0].name == "search"
        assert function_calls[1].name == "calculate"

    def test_parse_function_with_mixed_type_parameters(self):
        """Test parsing XML with mixed numeric, boolean, and string parameters."""
        content = """
        <function_calls>
        <invoke>
        <tool_name>configure</tool_name>
        <parameters>
        <int_value>42</int_value>
        <float_value>3.14</float_value>
        <enabled>true</enabled>
        <debug>false</debug>
        <name>test</name>
        </parameters>
        </invoke>
        </function_calls>
        """

        prompt_builder = XmlToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 1
        assert function_calls[0].args["int_value"] == 42
        assert function_calls[0].args["float_value"] == 3.14
        assert function_calls[0].args["enabled"] is True
        assert function_calls[0].args["debug"] is False
        assert function_calls[0].args["name"] == "test"

    def test_parse_function_with_json_embedded_in_xml(self):
        """Test parsing XML with JSON embedded in parameters - complex parsing."""
        content = """
        <function_calls>
        <invoke>
        <tool_name>api_call</tool_name>
        <parameters>
        <payload>{"key": "value", "number": 123}</payload>
        </parameters>
        </invoke>
        </function_calls>
        """

        prompt_builder = XmlToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 1
        assert function_calls[0].args["payload"]["key"] == "value"
        assert function_calls[0].args["payload"]["number"] == 123

    def test_parse_function_with_empty_or_missing_parameters(self):
        """Test parsing XML with empty or missing parameters - edge case handling."""
        # Empty parameters
        content1 = """
        <function_calls>
        <invoke>
        <tool_name>get_status</tool_name>
        <parameters>
        </parameters>
        </invoke>
        </function_calls>
        """

        # Missing parameters tag
        content2 = """
        <function_calls>
        <invoke>
        <tool_name>test</tool_name>
        </invoke>
        </function_calls>
        """

        prompt_builder = XmlToolPrompt()

        function_calls1 = prompt_builder.parse_function(content1)
        assert len(function_calls1) == 1
        assert function_calls1[0].name == "get_status"
        assert function_calls1[0].args == {}

        function_calls2 = prompt_builder.parse_function(content2)
        assert len(function_calls2) == 1
        assert function_calls2[0].name == "test"
        assert function_calls2[0].args == {}

    def test_parse_function_with_whitespace_in_values(self):
        """Test parsing XML with whitespace and special characters - stripping logic."""
        content = """
        <function_calls>
        <invoke>
        <tool_name>search</tool_name>
        <parameters>
        <query>  search term with spaces  </query>
        </parameters>
        </invoke>
        </function_calls>
        """

        prompt_builder = XmlToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 1
        assert function_calls[0].args["query"] == "search term with spaces"

    def test_parse_function_with_unicode_in_xml(self):
        """Test parsing XML with unicode characters - encoding handling."""
        content = """
        <function_calls>
        <invoke>
        <tool_name>search</tool_name>
        <parameters>
        <query>搜索 🔍</query>
        </parameters>
        </invoke>
        </function_calls>
        """

        prompt_builder = XmlToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert len(function_calls) == 1
        assert function_calls[0].args["query"] == "搜索 🔍"

    def test_parse_function_with_invalid_xml(self):
        """Test parsing invalid XML - error resilience."""
        content = """
        <function_calls>
        <invoke>
        <tool_name>test
        </invoke>
        </function_calls>
        """

        prompt_builder = XmlToolPrompt()
        function_calls = prompt_builder.parse_function(content)

        assert isinstance(function_calls, list)


class TestToolPromptFactory:
    """Test suite for ToolPromptFactory class."""

    def test_factory_register_and_create(self):
        """Test registering and creating tool prompt implementations."""
        factory = ToolPromptFactory()

        factory.register("json", JsonToolPrompt)
        factory.register("xml", XmlToolPrompt)

        json_prompt = factory.create("json")
        xml_prompt = factory.create("xml")

        assert isinstance(json_prompt, JsonToolPrompt)
        assert isinstance(xml_prompt, XmlToolPrompt)

    def test_factory_create_unregistered_raises_error(self):
        """Test creating unregistered tool prompt raises ValueError."""
        factory = ToolPromptFactory()

        with pytest.raises(ValueError) as exc_info:
            factory.create("unknown")

        assert "not registered" in str(exc_info.value)

    def test_get_factory_returns_initialized_instance(self):
        """Test get_factory returns initialized instance with JSON and XML registered."""
        factory = get_factory()

        assert isinstance(factory, ToolPromptFactory)
        assert factory.create("json") is not None
        assert factory.create("xml") is not None


class TestToolPromptIntegration:
    """Integration tests for tool prompt implementations."""

    def test_json_build_and_parse_roundtrip(self):
        """Test that JSON prompt can be built and parsed correctly."""
        func_decl = FunctionDeclaration(name="search",
                                        description="Search function",
                                        parameters=Schema(type=Type.OBJECT,
                                                          properties={
                                                              "query": Schema(type=Type.STRING),
                                                              "limit": Schema(type=Type.INTEGER),
                                                          }))
        tool = Tool(function_declarations=[func_decl])

        json_prompt = JsonToolPrompt()
        prompt = json_prompt.build_prompt([tool])

        function_call_content = '{"name": "search", "arguments": {"query": "test", "limit": 10}}'
        function_calls = json_prompt.parse_function(function_call_content)

        assert len(function_calls) == 1
        assert function_calls[0].name == "search"
        assert function_calls[0].args["query"] == "test"
        assert function_calls[0].args["limit"] == 10

    def test_xml_build_and_parse_roundtrip(self):
        """Test that XML prompt can be built and parsed correctly."""
        func_decl = FunctionDeclaration(name="calculate",
                                        description="Calculate function",
                                        parameters=Schema(type=Type.OBJECT,
                                                          properties={
                                                              "expression": Schema(type=Type.STRING),
                                                          }))
        tool = Tool(function_declarations=[func_decl])

        xml_prompt = XmlToolPrompt()
        prompt = xml_prompt.build_prompt([tool])

        function_call_content = """
        <function_calls>
        <invoke>
        <tool_name>calculate</tool_name>
        <parameters>
        <expression>2+2</expression>
        </parameters>
        </invoke>
        </function_calls>
        """
        function_calls = xml_prompt.parse_function(function_call_content)

        assert len(function_calls) == 1
        assert function_calls[0].name == "calculate"
        assert function_calls[0].args["expression"] == "2+2"

    def test_json_handles_missing_schema_fields(self):
        """Test JSON prompt handles functions with minimal schema."""
        func_decl = FunctionDeclaration(name="simple_func", description="Simple function")
        tool = Tool(function_declarations=[func_decl])

        json_prompt = JsonToolPrompt()
        prompt = json_prompt.build_prompt([tool])

        assert '"name": "simple_func"' in prompt
        assert '"parameters"' in prompt

    def test_xml_handles_missing_schema_fields(self):
        """Test XML prompt handles functions with minimal schema."""
        func_decl = FunctionDeclaration(name="simple_func", description="Simple function")
        tool = Tool(function_declarations=[func_decl])

        xml_prompt = XmlToolPrompt()
        prompt = xml_prompt.build_prompt([tool])

        assert "<tool_name>simple_func</tool_name>" in prompt
        assert "Simple function" in prompt
