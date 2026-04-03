# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.code_executors._types import CodeBlockDelimiter
from trpc_agent_sdk.code_executors._types import CodeExecutionResult
from trpc_agent_sdk.code_executors.utils import CodeExecutionUtils
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Outcome
from trpc_agent_sdk.types import Part


class TestCodeExecutionUtilsPrepareGlobals:
    """Test suite for CodeExecutionUtils.prepare_globals method."""

    def test_prepare_globals_with_main_check(self):
        """Test prepare_globals injects __name__ when code has __main__ check."""
        code = "if __name__ == '__main__':\n    print('hello')"
        globals_ = {}

        CodeExecutionUtils.prepare_globals(code, globals_)

        assert globals_["__name__"] == "__main__"

    def test_prepare_globals_without_main_check(self):
        """Test prepare_globals does not inject __name__ when code has no __main__ check."""
        code = "print('hello')"
        globals_ = {}

        CodeExecutionUtils.prepare_globals(code, globals_)

        assert "__name__" not in globals_

    def test_prepare_globals_with_double_quotes(self):
        """Test prepare_globals handles double quotes in __main__ check."""
        code = 'if __name__ == "__main__":\n    print("hello")'
        globals_ = {}

        CodeExecutionUtils.prepare_globals(code, globals_)

        assert globals_["__name__"] == "__main__"

    def test_prepare_globals_preserves_existing_globals(self):
        """Test prepare_globals preserves existing globals."""
        code = "if __name__ == '__main__':\n    pass"
        globals_ = {"existing_var": "value"}

        CodeExecutionUtils.prepare_globals(code, globals_)

        assert globals_["__name__"] == "__main__"
        assert globals_["existing_var"] == "value"


class TestCodeExecutionUtilsExtractFenceLanguage:
    """Test suite for CodeExecutionUtils.extract_fence_language method."""

    def test_extract_fence_language_python(self):
        """Test extracting Python language from fence."""
        text = "```python\nprint('hello')\n```"
        result = CodeExecutionUtils.extract_fence_language(text)
        assert result == "python"

    def test_extract_fence_language_bash(self):
        """Test extracting Bash language from fence."""
        text = "```bash\necho hello\n```"
        result = CodeExecutionUtils.extract_fence_language(text)
        assert result == "bash"

    def test_extract_fence_language_with_indentation(self):
        """Test extracting language with indentation."""
        text = "    ```python\n    print('hello')\n    ```"
        result = CodeExecutionUtils.extract_fence_language(text)
        assert result == "python"

    def test_extract_fence_language_no_match(self):
        """Test extracting language when no fence found."""
        text = "Just plain text"
        result = CodeExecutionUtils.extract_fence_language(text)
        assert result == ""

    def test_extract_fence_language_triple_tilde(self):
        """Test extracting language from triple tilde fence."""
        text = "~~~python\nprint('hello')\n~~~"
        result = CodeExecutionUtils.extract_fence_language(text)
        assert result == "python"


class TestCodeExecutionUtilsGetEncodedFileContent:
    """Test suite for CodeExecutionUtils.get_encoded_file_content method."""

    def test_get_encoded_file_content_already_base64(self):
        """Test get_encoded_file_content returns base64 data as-is."""
        import base64
        original_data = b"test data"
        encoded_data = base64.b64encode(original_data)

        result = CodeExecutionUtils.get_encoded_file_content(encoded_data)

        assert result == encoded_data

    def test_get_encoded_file_content_not_base64(self):
        """Test get_encoded_file_content encodes non-base64 data."""
        import base64
        original_data = b"test data"

        result = CodeExecutionUtils.get_encoded_file_content(original_data)

        assert result == base64.b64encode(original_data)

    def test_get_encoded_file_content_empty(self):
        """Test get_encoded_file_content with empty data."""
        import base64
        result = CodeExecutionUtils.get_encoded_file_content(b"")
        assert result == base64.b64encode(b"")


class TestCodeExecutionUtilsExtractCodeAndTruncateContent:
    """Test suite for CodeExecutionUtils.extract_code_and_truncate_content method."""

    def test_extract_code_from_executable_code_parts(self):
        """Test extracting code from executable code parts."""

        content = Content(parts=[Part.from_executable_code(code="print('hello')", language="PYTHON")])
        delimiters = [CodeBlockDelimiter(start="```python\n", end="\n```")]

        code_blocks = CodeExecutionUtils.extract_code_and_truncate_content(content, delimiters)

        assert len(code_blocks) == 1
        assert code_blocks[0].code == "print('hello')"
        assert code_blocks[0].language == "PYTHON"

    def test_extract_code_from_text_parts(self):
        """Test extracting code from text parts."""
        content = Content(parts=[Part(text="Here is some code:\n```python\nprint('hello')\n```\nThat's it.")])
        delimiters = [CodeBlockDelimiter(start="```python\n", end="\n```")]
        code_blocks = CodeExecutionUtils.extract_code_and_truncate_content(content, delimiters)

        assert len(code_blocks) >= 1
        assert any(block.code == "print('hello')" for block in code_blocks)

    def test_extract_code_empty_content(self):
        """Test extracting code from empty content."""
        content = Content(parts=[])
        delimiters = [CodeBlockDelimiter(start="```python\n", end="\n```")]

        code_blocks = CodeExecutionUtils.extract_code_and_truncate_content(content, delimiters)

        assert len(code_blocks) == 0

    def test_extract_code_no_code_blocks(self):
        """Test extracting code when no code blocks found."""
        content = Content(parts=[Part(text="Just plain text")])
        delimiters = [CodeBlockDelimiter(start="```python\n", end="\n```")]

        code_blocks = CodeExecutionUtils.extract_code_and_truncate_content(content, delimiters)

        assert len(code_blocks) == 0

    def test_extract_code_multiple_delimiters(self):
        """Test extracting code with multiple delimiters."""
        content = Content(parts=[
            Part(text="```tool_code\nprint('hello')\n```"),
            Part(text="```python\nprint('world')\n```"),
            Part(text="```bash\nprint('foo')\n```"),
        ])
        delimiters = [
            CodeBlockDelimiter(start="```tool_code\n", end="\n```"),
            CodeBlockDelimiter(start="```python\n", end="\n```"),
            CodeBlockDelimiter(start="```bash\n", end="\n```"),
        ]

        code_blocks = CodeExecutionUtils.extract_code_and_truncate_content(content, delimiters)

        assert len(code_blocks) >= 1
        assert code_blocks[0].code == "print('hello')"
        assert code_blocks[0].language.lower() == "python"
        assert code_blocks[1].code == "print('world')"
        assert code_blocks[1].language.lower() == "python"
        assert code_blocks[2].code == "print('foo')"
        assert code_blocks[2].language.lower() == "bash"


class TestCodeExecutionUtilsBuildExecutableCodePart:
    """Test suite for CodeExecutionUtils.build_executable_code_part method."""

    def test_build_executable_code_part(self):
        """Test building executable code part."""
        code = "print('hello')"

        part = CodeExecutionUtils.build_executable_code_part(code)

        assert part.executable_code is not None
        assert part.executable_code.code == code
        assert part.executable_code.language == "PYTHON"


class TestCodeExecutionUtilsBuildCodeExecutionResultPart:
    """Test suite for CodeExecutionUtils.build_code_execution_result_part method."""

    def test_build_code_execution_result_part_with_output(self):
        """Test building code execution result part with output."""
        result = CodeExecutionResult(output="output", outcome=Outcome.OUTCOME_OK)

        part = CodeExecutionUtils.build_code_execution_result_part(result)

        assert part.code_execution_result is not None
        assert part.code_execution_result.output == "output"

    def test_build_code_execution_result_part_empty_output(self):
        """Test building code execution result part with empty output."""
        result = CodeExecutionResult(output="", outcome=Outcome.OUTCOME_OK)
        part = CodeExecutionUtils.build_code_execution_result_part(result)

        assert part.code_execution_result is not None
        assert part.code_execution_result.output == ""


class TestCodeExecutionUtilsConvertCodeExecutionParts:
    """Test suite for CodeExecutionUtils.convert_code_execution_parts method."""

    def test_convert_executable_code_part(self):
        """Test converting executable code part to text."""
        content = Content(parts=[Part.from_executable_code(code="print('hello')", language="PYTHON")])
        code_delimiter = CodeBlockDelimiter(start="```python\n", end="\n```")
        result_delimiter = CodeBlockDelimiter(start="```output\n", end="\n```")

        CodeExecutionUtils.convert_code_execution_parts(content, code_delimiter, result_delimiter)

        assert len(content.parts) == 1
        assert content.parts[0].text is not None
        assert "print('hello')" in content.parts[0].text

    def test_convert_code_execution_result_part_single(self):
        """Test converting code execution result part to text when single part."""
        content = Content(parts=[Part.from_code_execution_result(outcome=Outcome.OUTCOME_OK, output="output text")])
        code_delimiter = CodeBlockDelimiter(start="```python\n", end="\n```")
        result_delimiter = CodeBlockDelimiter(start="```output\n", end="\n```")

        CodeExecutionUtils.convert_code_execution_parts(content, code_delimiter, result_delimiter)

        assert len(content.parts) == 1
        assert content.parts[0].text is not None
        assert "output text" in content.parts[0].text
        assert content.role == "user"

    def test_convert_code_execution_parts_empty(self):
        """Test converting empty content."""
        content = Content(parts=[])
        code_delimiter = CodeBlockDelimiter(start="```python\n", end="\n```")
        result_delimiter = CodeBlockDelimiter(start="```output\n", end="\n```")

        # Should not raise error
        CodeExecutionUtils.convert_code_execution_parts(content, code_delimiter, result_delimiter)

        assert len(content.parts) == 0
