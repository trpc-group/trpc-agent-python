# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Grep search tool implementation.

This module provides the GrepTool class which enables agents to search files
using regex patterns. Works with any text file type.
"""

import os
import re
from typing import Any
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._file_utils import safe_read_file


class GrepTool(BaseTool):
    """Tool for searching patterns in files."""

    def __init__(self, cwd: Optional[str] = None, max_file_size: int = 10 * 1024 * 1024, max_results: int = 50):
        super().__init__(
            name="Grep",
            description=("Search files with regex pattern. Returns matches with file "
                         "paths and line numbers (1-based). Auto-skips binary files "
                         "and .git/node_modules."),
        )
        self.cwd = cwd
        self.max_file_size = max_file_size
        self.max_results = max_results

    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name="Grep",
            description=("Search files with regex pattern. Returns matches with file paths and line numbers (1-based). "
                         "Use when: finding text patterns, searching for specific content, "
                         "or locating text across files. "
                         "Works with any text file: code files, configuration files, data files, documents, etc. "
                         "Supports: regex patterns, case-sensitive/insensitive search, "
                         "recursive directory search. "
                         "Auto-skips: binary files, .git, node_modules, __pycache__, .venv, venv, .env, dist, "
                         "build. "
                         "Limits: max file size 10MB, max 50 results (configurable). "
                         "Example: Grep(pattern='key=\\w+', path='config/', case_sensitive=True) "
                         "finds all configuration entries."),
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "pattern":
                    Schema(
                        type=Type.STRING,
                        description=("Regex pattern to search. Supports standard regex syntax. "
                                     "Example: 'key=\\w+' for configuration entries, '^#.*' for comment lines, "
                                     "'TODO|FIXME' for notes, '\\d{4}-\\d{2}-\\d{2}' for dates."),
                    ),
                    "path":
                    Schema(
                        type=Type.STRING,
                        description=("Optional. File or directory to search (recursive if directory). "
                                     "Default: current directory ('.'). "
                                     "Example: 'src/' searches recursively in src, "
                                     "'main.py' searches single file."),
                    ),
                    "case_sensitive":
                    Schema(
                        type=Type.BOOLEAN,
                        description=("Optional. Case-sensitive search. Default: true. "
                                     "Set false for case-insensitive. "
                                     "Example: case_sensitive=false finds both 'key' and 'Key'."),
                    ),
                    "max_results":
                    Schema(
                        type=Type.INTEGER,
                        description=("Optional. Max results to return. Default: 50. "
                                     "Results truncated if exceeded. Use to limit output for large file sets."),
                    ),
                },
                required=["pattern"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        pattern = args.get("pattern")
        path = args.get("path", ".")
        case_sensitive = args.get("case_sensitive", True)
        max_results = args.get("max_results", self.max_results)

        if not pattern:
            return {"error": "INVALID_PARAMETER: pattern parameter is required"}

        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
        except re.error as ex:
            return {"error": f"INVALID_REGEX: regex syntax error: {str(ex)}"}

        if self.cwd and not os.path.isabs(path):
            path = os.path.join(self.cwd, path)
        path = os.path.abspath(path)

        try:
            if not os.path.exists(path):
                return {"error": f"PATH_NOT_FOUND: path does not exist: {path}"}

            matches = []
            skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".env", "dist", "build"}

            if os.path.isfile(path):
                file_matches = await self._search_single_file(path, regex)
                if file_matches:
                    matches.append((path, file_matches))
            elif os.path.isdir(path):
                matches = await self._search_in_directory(path, regex, skip_dirs, max_results)
            else:
                return {"error": f"INVALID_PATH: path is neither a file nor a directory: {path}"}

            total_matches = sum(len(file_matches) for _, file_matches in matches)

            if not matches:
                success_text = "SEARCH_COMPLETE: found 0 results"
                content_text = ""
            else:
                content_parts = []
                for file_path, file_matches in matches:
                    content_parts.append(f"# {file_path}")
                    for line_num, line_content in file_matches:
                        content_parts.append(f"  {line_num} | {line_content}")
                    content_parts.append("----")

                if content_parts and content_parts[-1] == "----":
                    content_parts.pop()

                content_text = "\n".join(content_parts)
                limit_info = ""
                if total_matches >= max_results:
                    limit_info = f" (max results limit reached: {max_results})"
                success_text = f"SEARCH_COMPLETE: found {total_matches} results{limit_info}"

            return {
                "success": True,
                "pattern": pattern,
                "total_matches": total_matches,
                "matches": matches,
                "formatted_output": f"{success_text}\n\n{content_text}" if content_text else success_text,
            }
        except Exception as ex:  # pylint: disable=broad-except
            return {"error": f"SEARCH_ERROR: error occurred during search: {str(ex)}"}

    async def _search_single_file(self, file_path: str, regex: re.Pattern) -> list[tuple[int, str]]:
        """Search a single file.

        Args:
            file_path: File path
            regex: Compiled regex pattern

        Returns:
            List of matches, each element is (line_num, line_content), line_num is 1-based
        """
        matches = []
        try:
            if os.path.getsize(file_path) > self.max_file_size:
                return matches

            content, _ = safe_read_file(file_path)
            for line_num_1based, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append((line_num_1based, line.rstrip()))
                    if len(matches) >= self.max_results:
                        break
        except Exception:  # pylint: disable=broad-except
            pass
        return matches

    async def _search_in_directory(self, directory: str, regex: re.Pattern, skip_dirs: set,
                                   max_results: int) -> list[tuple[str, list[tuple[int, str]]]]:
        """Recursively search in directory.

        Args:
            directory: Directory path
            regex: Compiled regex pattern
            skip_dirs: Set of directory names to skip
            max_results: Maximum number of results

        Returns:
            List of matches, each element is (file_path, [(line_num, line_content), ...])
        """
        matches = []
        total_matches = 0

        def should_skip_dir(dirname: str) -> bool:
            return dirname.startswith(".") or dirname in skip_dirs

        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not should_skip_dir(d)]

            for file in files:
                if total_matches >= max_results:
                    break

                file_path = os.path.join(root, file)
                try:
                    if any(file_path.endswith(ext) for ext in [".pyc", ".so", ".dll", ".exe", ".bin", ".o", ".a"]):
                        continue
                    if os.path.getsize(file_path) > self.max_file_size:
                        continue

                    file_matches = await self._search_single_file(file_path, regex)
                    if file_matches:
                        matches.append((file_path, file_matches))
                        total_matches += len(file_matches)
                        if total_matches >= max_results:
                            if total_matches > max_results:
                                excess = total_matches - max_results
                                matches[-1] = (file_path, file_matches[:-excess])
                            break
                except Exception:  # pylint: disable=broad-except
                    continue

        return matches
