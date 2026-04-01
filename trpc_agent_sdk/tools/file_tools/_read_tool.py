# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Read file tool implementation.

This module provides the ReadTool class which enables agents to read file content
with support for line range specification. Works with any text file type.
"""

import os
from typing import Any
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._file_utils import safe_read_file

# File size limit: 100MB
_MAX_FILE_SIZE = 100 * 1024 * 1024
# Maximum read lines: 100K lines
_MAX_READ_LINES = 100000


class ReadTool(BaseTool):
    """Tool for reading file content."""

    def __init__(self, cwd: Optional[str] = None):
        super().__init__(
            name="Read",
            description=("Read file content with line numbers (1-based). Supports "
                         "entire file or line ranges. Returns format: "
                         "'line_number | content'."),
        )
        self.cwd = cwd

    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name="Read",
            description=("Read file content with line numbers. "
                         "Use when: viewing file content, checking specific lines, "
                         "or analyzing file content before editing. "
                         "Works with any text file: code files, configuration files, data files, documents, etc. "
                         "Returns: file content with format 'line_number | content' where line numbers are 1-based. "
                         "Supports: entire file or specific line ranges. "
                         "Constraints: max file size 100MB, max 100K lines per read. "
                         "Example: Read(path='config.txt', start_line=10, end_line=20) reads lines 10-20."),
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "path":
                    Schema(
                        type=Type.STRING,
                        description=(
                            "File path (absolute or relative to cwd). "
                            "Relative paths resolved from tool's working directory. "
                            "Works with any text file type. "
                            "Example: 'config.txt', 'data.json', 'document.md', or '/absolute/path/to/file.txt'."),
                    ),
                    "start_line":
                    Schema(
                        type=Type.INTEGER,
                        description=("Optional. Start line number (1-based). Default: 1 (beginning of file). "
                                     "First line is 1, not 0. "
                                     "Example: start_line=10 reads from line 10."),
                    ),
                    "end_line":
                    Schema(
                        type=Type.INTEGER,
                        description=("Optional. End line number (1-based, inclusive). Default: end of file. "
                                     "The end_line is included in the result. "
                                     "Example: end_line=20 reads up to and including line 20."),
                    ),
                },
                required=["path"],
            ),
        )

    def _calculate_line_range(
        self,
        total_lines: int,
        start_line: Optional[int],
        end_line: Optional[int],
    ) -> tuple[int, int]:
        """Calculate line range (1-based).

        Args:
            total_lines: Total number of lines in file
            start_line: Start line number (1-based, None means from line 1)
            end_line: End line number (1-based, None means to end of file)

        Returns:
            (start_line_1based, end_line_1based): 1-based line range
        """
        start_line_1based = start_line if start_line is not None else 1
        end_line_1based = end_line if end_line is not None else total_lines
        start_line_1based = max(1, start_line_1based)
        end_line_1based = min(total_lines, end_line_1based)
        if start_line_1based > end_line_1based:
            end_line_1based = start_line_1based
        return start_line_1based, end_line_1based

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        path = args.get("path")
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        if not path:
            return {"error": "INVALID_PARAMETER: path parameter is required"}

        if self.cwd and not os.path.isabs(path):
            path = os.path.join(self.cwd, path)
        path = os.path.abspath(path)

        try:
            if not os.path.exists(path):
                return {"error": f"FILE_NOT_FOUND: file does not exist: {path}"}
            if not os.path.isfile(path):
                return {"error": f"INVALID_PATH: path is not a file: {path}"}

            file_size = os.path.getsize(path)
            if file_size > _MAX_FILE_SIZE:
                return {"error": f"FILE_TOO_LARGE: file size {file_size} bytes exceeds max {_MAX_FILE_SIZE} bytes"}

            content, encoding = safe_read_file(path)
            lines = content.splitlines()
            total_lines = len(lines)

            if start_line is not None:
                start_line = int(start_line)
                if start_line < 1:
                    return {"error": f"INVALID_PARAMETER: start_line must be >= 1, got {start_line}"}
            if end_line is not None:
                end_line = int(end_line)
                if end_line < 1:
                    return {"error": f"INVALID_PARAMETER: end_line must be >= 1, got {end_line}"}

            start_line_1based, end_line_1based = self._calculate_line_range(total_lines, start_line, end_line)
            if start_line_1based > total_lines:
                return {"error": f"OUT_OF_RANGE: start_line {start_line_1based} exceeds file total lines {total_lines}"}

            read_lines_count = end_line_1based - start_line_1based + 1
            warning_msg = ""
            if read_lines_count > _MAX_READ_LINES:
                end_line_1based = start_line_1based + _MAX_READ_LINES - 1
                warning_msg = f"WARNING: file content exceeds {_MAX_READ_LINES} lines, showing partial content only.\n"

            start_idx = start_line_1based - 1
            end_idx = end_line_1based - 1
            selected_lines = lines[start_idx:end_idx + 1]

            content_lines = []
            for i, line in enumerate(selected_lines):
                line_num_1based = start_line_1based + i
                content_lines.append(f"{line_num_1based} | {line}")

            content_text = warning_msg + "\n".join(content_lines)
            success_text = f"File: {path}\n"
            success_text += f"Total lines: {total_lines}\n"
            success_text += f"Read range: {start_line_1based}-{end_line_1based}\n"
            success_text += f"Content:\n{content_text}"

            return {
                "success": True,
                "content": content_text,
                "path": path,
                "encoding": encoding,
                "total_lines": total_lines,
                "read_range": f"{start_line_1based}-{end_line_1based}",
                "file_size": file_size,
                "formatted_output": success_text,
            }
        except PermissionError:
            return {"error": "PERMISSION_DENIED: insufficient permissions to read file"}
        except FileNotFoundError:
            return {"error": f"FILE_NOT_FOUND: file does not exist: {path}"}
        except Exception as ex:  # pylint: disable=broad-except
            return {"error": f"READ_ERROR: error reading file: {str(ex)}"}
