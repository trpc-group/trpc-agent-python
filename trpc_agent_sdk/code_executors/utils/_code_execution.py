# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Code execution utilities for TRPC Agent framework.

This module provides utility functions for processing code blocks,
extracting code from responses, and handling code execution results.
"""
import base64
import binascii
import re
from typing import Any

from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from .._types import CodeBlock
from .._types import CodeBlockDelimiter
from .._types import CodeExecutionResult


class CodeExecutionUtils:
    """Utility functions for code execution."""

    @classmethod
    def _is_ignored_code_block(cls, code: str, ignore_codes: list[str]) -> bool:
        """Return True when code block first line is in ignore list."""
        if not code:
            return False
        lines = code.splitlines()
        if not lines:
            return False
        first_line = lines[0].strip()
        if not first_line:
            return False
        ignore_set = {item.strip() for item in ignore_codes if item and item.strip()}
        return first_line in ignore_set

    @classmethod
    def prepare_globals(cls, code: str, globals_: dict[str, Any]) -> None:
        """Prepare globals for code execution, injecting __name__ if needed."""
        if re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]", code):
            globals_["__name__"] = "__main__"

    @classmethod
    def extract_fence_language(cls, text: str) -> str:
        """Extract the language from the text."""
        pattern = r'(?m)^[ \t]*(?:`{3,}|~{3,})[ \t]*([^\s`~]+)'
        m = re.search(pattern, text)
        return m.group(1) if m else ""

    @classmethod
    def get_encoded_file_content(cls, data: bytes) -> bytes:
        """Gets the file content as a base64-encoded bytes.

        Args:
          data: The file content bytes.

        Returns:
          The file content as a base64-encoded bytes.
        """

        def _is_base64_encoded(data: bytes) -> bool:
            try:
                return base64.b64encode(base64.b64decode(data)) == data
            except binascii.Error:
                return False

        return data if _is_base64_encoded(data) else base64.b64encode(data)

    @classmethod
    def extract_code_and_truncate_content(
        cls,
        content: Content,
        code_block_delimiters: list[CodeBlockDelimiter],
        ignore_codes: list[str] = None,
    ) -> list[CodeBlock]:
        """Extracts all code blocks from the content and reconstructs content.parts.

        This function extracts all code blocks from the content and rebuilds content.parts
        to contain alternating text and executable code parts in their original order.

        Args:
          content: The mutable content to extract the code from.
          code_block_delimiters: The list of the enclosing delimiters to identify
            the code blocks.
          ignore_codes: The list of codes to ignore.

        Returns:
          The first code block if found; otherwise, None.
        """
        ignore_codes = ignore_codes or []
        code_blocks = []
        if not content or not content.parts:
            return code_blocks

        # Extract the code from the executable code parts if there are no associated
        # code execution result parts.
        total_len = len(content.parts)
        for idx, part in enumerate(content.parts):
            if part.executable_code:
                code_str = part.executable_code.code or ""
                if cls._is_ignored_code_block(code_str, ignore_codes):
                    continue
                if idx < total_len - 1 and not content.parts[idx + 1].code_execution_result:
                    code_blocks.append(CodeBlock(code=code_str, language=part.executable_code.language))
                if idx == total_len - 1:
                    code_blocks.append(CodeBlock(code=code_str, language=part.executable_code.language))
        # If there are code blocks, return them.
        if code_blocks:
            return code_blocks

        # Extract the code from the text parts.
        text_parts = [p for p in content.parts if p.text]
        if not text_parts:
            return code_blocks

        response_text = '\n'.join([p.text for p in text_parts])

        # Build regex pattern to match all code blocks
        leading_delimiter_pattern = '|'.join(re.escape(d.start) for d in code_block_delimiters)
        trailing_delimiter_pattern = '|'.join(re.escape(d.end) for d in code_block_delimiters)

        # Pattern to capture: delimiter start, optional language identifier, code content, and delimiter end
        # The start delimiter may already include the language (e.g., "```python\n")
        # So we need to match the start delimiter, then capture everything until the end delimiter
        pattern = re.compile(
            rf'({leading_delimiter_pattern})(.*?)({trailing_delimiter_pattern})',
            re.DOTALL,
        )

        # Find all code blocks and their positions
        matches = list(pattern.finditer(response_text))
        if not matches:
            return code_blocks

        # Rebuild content.parts with alternating text and code blocks
        new_parts = []
        last_end = 0
        first_code = None

        for match in matches:
            # Add text before this code block (if any)
            text_before = response_text[last_end:match.start()].strip()
            if text_before:
                new_parts.append(Part(text=text_before))

            # Extract the matched parts
            start_delimiter = match.group(1)  # e.g., "```python\n"
            code_content = match.group(2)  # The code content between delimiters

            # Extract language from start delimiter if present
            # Try to match language from patterns like "```python\n" or "```tool_code\n"
            lang_match = re.search(r'```(\w+)', start_delimiter)
            language = lang_match.group(1) if lang_match else ""

            # Extract code content, removing leading/trailing whitespace and newlines
            code_str = code_content.strip()
            if cls._is_ignored_code_block(code_str, ignore_codes):
                last_end = match.end()
                continue

            # Store first code block for return value
            if first_code is None:
                first_code = code_str

            # Determine language for executable code part
            # Default to PYTHON if not specified or not recognized
            exec_language = 'PYTHON'  # Default value
            if language:
                lang_lower = language.lower()
                if lang_lower in ('python', 'py', 'python3', 'tool_code'):
                    exec_language = 'PYTHON'
                elif lang_lower in ('bash', 'sh', 'shell'):
                    exec_language = 'BASH'  # Keep as PYTHON since API may not support BASH
                else:
                    exec_language = 'PYTHON'
                # Add more language mappings as needed
            code_blocks.append(CodeBlock(code=code_str, language=exec_language))
            # Add executable code part
            new_parts.append(Part.from_executable_code(
                code=code_str,
                language='PYTHON',
            ))

            last_end = match.end()

        # Add any remaining text after the last code block
        text_after = response_text[last_end:].strip()
        if text_after:
            new_parts.append(Part(text=text_after))

        # Replace content.parts with new parts
        content.parts = new_parts

        return code_blocks

    @classmethod
    def build_executable_code_part(cls, code: str) -> Part:
        """Builds an executable code part with code string.

        Args:
          cls: The class instance.
          code: The code string.

        Returns:
          The constructed executable code part.
        """
        return Part.from_executable_code(
            code=code,
            language='PYTHON',
        )

    @classmethod
    def build_code_execution_result_part(
        cls,
        code_execution_result: CodeExecutionResult,
    ) -> Part:
        """Builds the code execution result part from the code execution result.

        Args:
          cls: The class instance.
          code_execution_result: The code execution result.

        Returns:
          The constructed code execution result part.
        """
        return Part.from_code_execution_result(
            outcome=code_execution_result.outcome,
            output=code_execution_result.output if code_execution_result.output else '',
        )

    @classmethod
    def convert_code_execution_parts(
        cls,
        content: Content,
        code_block_delimiter: CodeBlockDelimiter,
        execution_result_delimiters: CodeBlockDelimiter,
    ) -> None:
        """Converts the code execution parts to text parts in a Content.

        Args:
          cls: The class instance.
          content: The mutable content to convert the code execution parts to text
            parts.
          code_block_delimiter: The delimiter to format the code block.
          execution_result_delimiters: The delimiter to format the code execution
            result.
        """
        if not content.parts:
            return

        # Handle the conversion of trailing executable code parts.
        if content.parts[-1].executable_code:
            content.parts[-1] = Part(text=(f"{code_block_delimiter.start}"
                                           f"{content.parts[-1].executable_code.code}"
                                           f"{code_block_delimiter.end}"))
        # Handle the conversion of trailing code execution result parts.
        # Skip if the Content has multiple parts, which means the Content is
        # likely generated by the model.
        elif len(content.parts) == 1 and content.parts[-1].code_execution_result:
            content.parts[-1] = Part(text=(f"{execution_result_delimiters.start}"
                                           f"{content.parts[-1].code_execution_result.output}"
                                           f"{execution_result_delimiters.end}"))
            content.role = 'user'
