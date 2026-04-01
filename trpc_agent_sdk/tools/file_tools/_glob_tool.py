# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Glob file search tool implementation.

This module provides the GlobTool class which enables agents to find files
using glob patterns. Works with any file type.
"""

import glob as pyglob
import os
import re
from typing import Any
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type


class GlobTool(BaseTool):
    """Tool for finding files using glob patterns."""

    def __init__(self, cwd: Optional[str] = None):
        super().__init__(
            name="Glob",
            description=("Find files matching glob pattern. Returns list of matching "
                         "file paths. Supports recursive patterns (**) and file type "
                         "filtering. More efficient than Grep for file discovery only."),
        )
        self.cwd = cwd

    def _expand_brace_pattern(self, pattern: str) -> list[str]:
        """Expand brace pattern.

        Expands `**/*.{py,go,js}` to `['**/*.py', '**/*.go', '**/*.js']`
        Supports nested braces and multiple braces.

        Args:
            pattern: Pattern that may contain braces

        Returns:
            List of expanded patterns
        """
        # Find innermost brace pattern {a,b,c}
        brace_pattern = re.compile(r'\{([^{}]+)\}')
        match = brace_pattern.search(pattern)

        if not match:
            return [pattern]

        # Extract content inside braces
        brace_content = match.group(1)
        # Split options (supports comma-separated)
        options = [opt.strip() for opt in brace_content.split(',')]

        # Expand pattern
        expanded = []
        for option in options:
            expanded_pattern = pattern[:match.start()] + option + pattern[match.end():]
            # Recursively process nested braces
            expanded.extend(self._expand_brace_pattern(expanded_pattern))

        return expanded

    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name="Glob",
            description=("Find files matching glob pattern. Returns list of matching file paths. "
                         "Use when: discovering files by pattern, finding all files of specific type, "
                         "or locating files in directory structure. "
                         "More efficient than Grep when only finding files (not searching contents). "
                         "Works with any file type: text files, data files, documents, images, etc. "
                         "Supports: recursive patterns (**), multiple file types, "
                         "directory filtering. "
                         "Limits: max 1000 results (configurable). "
                         "Example: Glob(pattern='**/*.txt', include_dirs=False) finds all text files recursively."),
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "pattern":
                    Schema(
                        type=Type.STRING,
                        description=("Glob pattern to match files. Supports standard glob syntax and brace expansion. "
                                     "Use '**' for recursive matching. "
                                     "Example: '*.txt' finds text files in current directory, "
                                     "'**/*.json' finds all JSON files recursively, "
                                     "'data/**/*.csv' finds CSV files in data directory, "
                                     "'*.{txt,json,xml}' finds multiple file types (brace expansion supported)."),
                    ),
                    "include_dirs":
                    Schema(
                        type=Type.BOOLEAN,
                        description=(
                            "Optional. Include directories in results. Default: false (only files). "
                            "Set true to find directories matching pattern. "
                            "Example: pattern='**/tests' with include_dirs=true finds all 'tests' directories."),
                    ),
                    "max_results":
                    Schema(
                        type=Type.INTEGER,
                        description=("Optional. Max results to return. Default: 1000. "
                                     "Results truncated if exceeded. Use to limit output for large file sets."),
                    ),
                },
                required=["pattern"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        pattern = args.get("pattern")
        include_dirs = args.get("include_dirs", False)
        max_results = args.get("max_results", 1000)

        if not pattern:
            return {"error": "INVALID_PARAMETER: pattern parameter is required"}

        try:
            # Expand brace patterns
            expanded_patterns = self._expand_brace_pattern(pattern)

            matches = []
            seen = set()  # For deduplication

            for expanded_pattern in expanded_patterns:
                if len(matches) >= max_results:
                    break

                if self.cwd and not os.path.isabs(expanded_pattern):
                    search_path = os.path.join(self.cwd, expanded_pattern)
                else:
                    search_path = expanded_pattern

                for file_path in pyglob.glob(search_path, recursive=True):
                    if len(matches) >= max_results:
                        break

                    abs_path = os.path.abspath(file_path)
                    # Deduplication
                    if abs_path in seen:
                        continue
                    seen.add(abs_path)

                    if os.path.isfile(file_path):
                        matches.append(abs_path)
                    elif include_dirs and os.path.isdir(file_path):
                        matches.append(abs_path)

            truncated = len(matches) >= max_results
            return {
                "success": True,
                "matches": matches,
                "count": len(matches),
                "truncated": truncated,
                "pattern": pattern,
            }
        except Exception as ex:  # pylint: disable=broad-except
            return {"success": False, "error": f"GLOB_ERROR: error globbing: {str(ex)}"}
