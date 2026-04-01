# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Write file tool implementation.

This module provides the WriteTool class which enables agents to write complete
file content, supporting file creation and overwriting. Works with any text file type.
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


class WriteTool(BaseTool):
    """Tool for writing file content."""

    def __init__(self, cwd: Optional[str] = None):
        super().__init__(
            name="Write",
            description=("Write complete file content (overwrites existing file). "
                         "Creates file if missing. CRITICAL: Overwrites entire file. "
                         "Use Edit for partial changes."),
        )
        self.cwd = cwd

    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name="Write",
            description=("Write complete file content (overwrites existing file). "
                         "Use when: creating new files, completely replacing file content, "
                         "or when Edit tool is insufficient. "
                         "Works with any text file: code files, configuration files, data files, documents, etc. "
                         "CRITICAL: This tool overwrites entire file. Ensure content is complete and correct. "
                         "For partial edits, use Edit tool instead. "
                         "For appending, set append=true. "
                         "Auto-creates directories if missing. "
                         "Example: Write(path='config.txt', content='key=value\\nanother_key=value2') "
                         "creates/overwrites the file."),
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "path":
                    Schema(
                        type=Type.STRING,
                        description=(
                            "File path (absolute or relative to cwd). "
                            "Relative paths resolved from tool's working directory. "
                            "Directory created automatically if missing. "
                            "Works with any text file type. "
                            "Example: 'config.txt', 'data.json', 'document.md', or '/absolute/path/to/file.txt'."),
                    ),
                    "content":
                    Schema(
                        type=Type.STRING,
                        description=("Complete file content (entire file, not partial). "
                                     "Must include all content that should be in the file. "
                                     "For partial changes, use Edit tool instead. "
                                     "Example: For a config file, include all configuration entries. "
                                     "For a document, include all text content."),
                    ),
                    "append":
                    Schema(
                        type=Type.BOOLEAN,
                        description=("Optional. If true, append content to file end. Default: false (overwrite). "
                                     "Use when adding content without replacing existing content. "
                                     "Example: append=true adds new content at end of file."),
                    ),
                },
                required=["path", "content"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        path = args.get("path")
        content = args.get("content")
        append = args.get("append", False)

        if not path:
            return {"error": "INVALID_PARAMETER: path parameter is required"}

        if content is None:
            return {"error": "INVALID_PARAMETER: content parameter is required"}
        if not isinstance(content, str):
            return {"error": "INVALID_PARAMETER: content must be a string"}

        if self.cwd and not os.path.isabs(path):
            path = os.path.join(self.cwd, path)
        path = os.path.abspath(path)

        try:
            dir_path = os.path.dirname(path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)

            file_existed = os.path.exists(path)
            mode = "a" if append else "w"
            encoding = "utf-8"

            if append and file_existed:
                try:
                    _, encoding = safe_read_file(path)
                except Exception:  # pylint: disable=broad-except
                    pass

            with open(path, mode, encoding=encoding) as f:
                f.write(content)

            action = "appended to" if append else "written to"
            bytes_written = len(content.encode(encoding))

            result = {
                "success": True,
                "path": path,
                "action": action,
                "bytes_written": bytes_written,
                "file_existed": file_existed,
                "message": f"SUCCESS: file {path} {action} successfully ({bytes_written} bytes)",
            }

            return result
        except PermissionError:
            return {"error": "PERMISSION_DENIED: insufficient permissions to write file"}
        except OSError as ex:
            return {"error": f"WRITE_ERROR: failed to write file: {str(ex)}"}
        except Exception as ex:  # pylint: disable=broad-except
            return {"error": f"Error writing file: {str(ex)}"}
