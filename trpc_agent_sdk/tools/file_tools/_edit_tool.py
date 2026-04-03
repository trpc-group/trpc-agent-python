# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Edit file tool implementation.

This module provides the EditTool class which enables agents to edit file content
using search/replace operations with tolerance matching and similarity hints.
Works with any text file type.
"""

import os
import re
from typing import Any
from typing import Optional

from rapidfuzz import fuzz

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._file_utils import safe_read_file

# Similarity hint threshold: 90% (for showing similar content in error message)
_SIMILARITY_THRESHOLD = 0.90
# Tab size: 4 spaces
_TABSIZE = 4
# Pattern to match line number prefix: "123 | " or "  45 | " (with optional leading spaces)
_LINE_NUMBER_PREFIX_PATTERN = re.compile(r'^\s*\d+\s*\|\s?')


def _calculate_similarity(text1: str, text2: str, tabsize: int = _TABSIZE) -> float:
    """Calculate similarity between two texts (after expanding tabs).

    Args:
        text1: First text
        text2: Second text
        tabsize: Tab size for expansion

    Returns:
        Similarity score (0.0-1.0), 1.0 means identical
    """
    norm1 = text1.expandtabs(tabsize)
    norm2 = text2.expandtabs(tabsize)
    ratio = fuzz.ratio(norm1, norm2)
    return ratio / 100.0


def _is_line_match_with_tolerance(file_line: str, search_line: str) -> bool:
    """Check if two lines match using tolerance strategy.

    Tolerance strategy:
    - Newline normalization
    - Empty line optimization
    - Trailing space tolerance
    - Leading space (indentation) tolerance

    Args:
        file_line: Line from file (tabs expanded)
        search_line: Line to search (tabs expanded)

    Returns:
        Whether the lines match
    """
    file_line_normalized = file_line.replace("\r", "    ")
    search_line_normalized = search_line.replace("\r", "    ")

    # Exact match
    if file_line_normalized == search_line_normalized:
        return True

    # Empty line match
    file_line_stripped = file_line_normalized.strip()
    search_line_stripped = search_line_normalized.strip()
    if file_line_stripped == "" and search_line_stripped == "":
        return True

    # Trailing space tolerance
    file_line_rstripped = file_line_normalized.rstrip()
    search_line_rstripped = search_line_normalized.rstrip()
    if file_line_rstripped == search_line_rstripped:
        return True

    # Leading space (indentation) tolerance - strip both leading and trailing
    if file_line_stripped == search_line_stripped:
        return True

    return False


def _strip_line_number_prefix(text: str) -> str:
    """Strip line number prefix from text if present.

    Read tool returns format "line_number | code_content", but the prefix
    is display only and should not be included in Edit parameters.
    This function automatically detects and removes such prefixes.

    Args:
        text: Text that may contain line number prefixes

    Returns:
        Text with line number prefixes removed (if all lines have them)
    """
    if not text:
        return text

    lines = text.splitlines()
    if not lines:
        return text

    # Check if ALL non-empty lines have the line number prefix pattern
    non_empty_lines = [line for line in lines if line.strip()]
    if not non_empty_lines:
        return text

    all_have_prefix = all(_LINE_NUMBER_PREFIX_PATTERN.match(line) for line in non_empty_lines)

    if not all_have_prefix:
        return text

    # Strip the prefix from all lines
    stripped_lines = []
    for line in lines:
        if line.strip():  # Non-empty line
            stripped_lines.append(_LINE_NUMBER_PREFIX_PATTERN.sub('', line, count=1))
        else:  # Empty line - keep as is
            stripped_lines.append(line)

    return '\n'.join(stripped_lines)


class EditTool(BaseTool):
    """Tool for editing file content.

    Used to perform search/replace modifications in specified text files, line by line.
    Works with any text file type: code files, configuration files, data files, documents, etc.
    Parameters include path/old_string/new_string. Search uses exact matching
    (line-by-line exact match), with tab expansion before comparison.
    """

    def __init__(self, cwd: Optional[str] = None):
        super().__init__(
            name="Edit",
            description=("Replace text block in file using exact string matching. "
                         "Uses line-by-line matching with whitespace tolerance. "
                         "old_string must be unique and match exactly."),
        )
        self.cwd = cwd
        self._tabsize: int = _TABSIZE
        self._similarity_hint_threshold: float = _SIMILARITY_THRESHOLD

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity between two texts (after expanding tabs).

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score (0.0-1.0)
        """
        return _calculate_similarity(text1, text2, self._tabsize)

    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name="Edit",
            description=("Replace text block in file using exact string matching. "
                         "Use when: modifying file content, updating configuration values, "
                         "changing text sections, or making partial file changes. "
                         "Works with any text file: code files, config files, data files, documents, etc. "
                         "Method: line-by-line matching with whitespace tolerance "
                         "(tabs, trailing spaces, newlines). "
                         "CRITICAL: old_string must be unique in file and match exactly "
                         "(including whitespace, indentation, newlines). "
                         "If multiple matches found, tool returns error with all match "
                         "locations. "
                         "If no match found, tool suggests similar text blocks "
                         "(≥90% similarity). "
                         "Example: Edit(path='config.txt', old_string='key=old_value', "
                         "new_string='key=new_value') replaces the configuration line."),
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
                    "old_string":
                    Schema(
                        type=Type.STRING,
                        description=("Exact text block to replace. Must match file content "
                                     "exactly including whitespace, indentation, newlines. "
                                     "MUST be unique in file (only one occurrence). "
                                     "Tool uses tolerance for minor whitespace differences, "
                                     "but provide exact match when possible. "
                                     "Include surrounding context if needed to ensure uniqueness. "
                                     "Example: '    key=old_value\\n    another_key=value' "
                                     "(include indentation and newlines)."),
                    ),
                    "new_string":
                    Schema(
                        type=Type.STRING,
                        description=("Replacement text block. Must include whitespace, "
                                     "indentation, newlines to match file format. "
                                     "Preserve original formatting style (tabs vs spaces, "
                                     "indentation level). "
                                     "Example: '    key=new_value\\n    another_key=value' "
                                     "(match indentation style)."),
                    ),
                },
                required=["path", "old_string", "new_string"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        path = args.get("path")
        old_string = args.get("old_string")
        new_string = args.get("new_string")

        if not path or not isinstance(path, str):
            return {
                "success": False,
                "error": "INVALID_PARAMETER: path parameter cannot be empty",
                "path": path or "",
            }
        if not isinstance(old_string, str) or old_string == "":
            return {
                "success": False,
                "error": "INVALID_PARAMETER: old_string parameter cannot be empty",
                "path": path,
            }
        if not isinstance(new_string, str):
            return {
                "success": False,
                "error": "INVALID_PARAMETER: new_string must be a string",
                "path": path,
            }

        # Auto-strip line number prefixes (e.g., "123 | code") from Read tool output
        old_string = _strip_line_number_prefix(old_string)
        new_string = _strip_line_number_prefix(new_string)

        if self.cwd and not os.path.isabs(path):
            path = os.path.join(self.cwd, path)
        path = os.path.abspath(path)

        try:
            if not os.path.exists(path):
                return {
                    "success": False,
                    "error": f"FILE_NOT_FOUND: file does not exist: {path}",
                    "path": path,
                }

            try:
                original_content, encoding = safe_read_file(path)
            except Exception as ex:  # pylint: disable=broad-except
                return {
                    "success": False,
                    "error": f"READ_ERROR: failed to read file: {str(ex)}",
                    "path": path,
                }

            original_lines: list[str] = original_content.splitlines()
            expanded_lines: list[str] = [ln.expandtabs(self._tabsize) for ln in original_lines]

            search_lines_raw: list[str] = old_string.splitlines()
            search_lines_expanded: list[str] = [ln.expandtabs(self._tabsize) for ln in search_lines_raw]
            search_len: int = len(search_lines_expanded)

            match_positions_1based: list[int] = self._find_all_matches(expanded_lines, search_lines_expanded)

            if not match_positions_1based:
                error_text: str = self._build_not_found_message(expanded_lines, search_lines_expanded)
                return {
                    "success": False,
                    "error": error_text,
                    "path": path,
                }

            if len(match_positions_1based) > 1:
                error_text_multi: str = self._build_multiple_matches_message(expanded_lines, match_positions_1based,
                                                                             search_len)
                return {
                    "success": False,
                    "error": error_text_multi,
                    "path": path,
                }

            start_line_1based: int = match_positions_1based[0]
            start_idx = start_line_1based - 1

            replace_lines: list[str] = new_string.splitlines()
            new_lines: list[str] = (original_lines[:start_idx] + replace_lines +
                                    original_lines[start_idx + search_len:])
            modified_content: str = "\n".join(new_lines)

            if original_content.endswith("\n") and not modified_content.endswith("\n"):
                modified_content += "\n"
            elif not original_content.endswith("\n") and modified_content.endswith("\n"):
                modified_content = modified_content.rstrip("\n")

            try:
                with open(path, "w", encoding=encoding) as f:
                    f.write(modified_content)
            except Exception as ex:  # pylint: disable=broad-except
                return {
                    "success": False,
                    "error": f"WRITE_ERROR: failed to write file: {str(ex)}",
                    "path": path,
                }

            success_text = f"SUCCESS: file {path} modified successfully"
            end_line_1based = start_line_1based + max(search_len, len(replace_lines)) - 1

            return {
                "success": True,
                "path": path,
                "message": success_text,
                "line_range": f"{start_line_1based}-{end_line_1based}",
                "changed_line_ranges": [(start_line_1based, end_line_1based)],
            }

        except Exception as ex:  # pylint: disable=broad-except
            return {
                "success": False,
                "error": f"EXECUTION_ERROR: error occurred during edit operation: {str(ex)}",
                "path": path,
            }

    def _find_all_matches(self, expanded_lines: list[str], search_lines_expanded: list[str]) -> list[int]:
        """Find all matching start line positions (with tolerance, returns 1-based).

        Args:
            expanded_lines: Target file line list (tabs expanded)
            search_lines_expanded: Search block line list (tabs expanded)

        Returns:
            List of all matching start line numbers (1-based)
        """
        positions_1based: list[int] = []
        search_len: int = len(search_lines_expanded)
        if search_len == 0:
            return positions_1based
        max_start_idx: int = len(expanded_lines) - search_len

        for idx in range(max_start_idx + 1):
            file_block = expanded_lines[idx:idx + search_len]
            if file_block == search_lines_expanded:
                positions_1based.append(idx + 1)
                continue
            if self._is_block_match_with_tolerance(file_block, search_lines_expanded):
                positions_1based.append(idx + 1)

        return positions_1based

    def _is_block_match_with_tolerance(self, file_block: list[str], search_block: list[str]) -> bool:
        """Check if two text blocks match using tolerance strategy.

        Args:
            file_block: Text block from file
            search_block: Text block to search

        Returns:
            Whether the blocks match
        """
        if len(file_block) != len(search_block):
            return False

        for file_line, search_line in zip(file_block, search_block):
            if not _is_line_match_with_tolerance(file_line, search_line):
                return False

        return True

    def _build_not_found_message(self, expanded_lines: list[str], search_lines_expanded: list[str]) -> str:
        """Build error message when no match found, with similarity hints (≥90%).

        Args:
            expanded_lines: Target file line list (tabs expanded)
            search_lines_expanded: Search block line list (tabs expanded)

        Returns:
            Error message text
        """
        search_content_display = "\n".join(search_lines_expanded)
        header = ("ERROR: exact match not found\n\n"
                  "Search is performed line-by-line and must exactly match consecutive lines in file "
                  "(including whitespace, indentation, newlines, and surrounding content).\n")

        search_len = len(search_lines_expanded)
        candidates: list[tuple[int, float]] = []
        if search_len > 0:
            max_start_idx = len(expanded_lines) - search_len
            for idx in range(max(0, max_start_idx + 1)):
                block = "\n".join(expanded_lines[idx:idx + search_len])
                sim = self._calculate_similarity(search_content_display, block)
                if sim >= self._similarity_hint_threshold:
                    candidates.append((idx + 1, sim))

        candidates.sort(key=lambda x: x[1], reverse=True)
        suggestions = candidates[:3]

        text_lines: list[str] = [header]
        if suggestions:
            text_lines.append("SIMILAR_CONTENT_FOUND: similar text blocks found (similarity >= 90%):")
            for start_line_1based, sim in suggestions:
                preview_lines = []
                start_idx = start_line_1based - 1
                for off in range(min(search_len, 3)):
                    line_num_1based = start_line_1based + off
                    line = expanded_lines[start_idx + off]
                    preview_lines.append(f"{line_num_1based} | {line}")
                preview = "\n".join(preview_lines)
                if search_len > 3:
                    preview += "\n..."
                text_lines.append(f"\n- Start line {start_line_1based} | Similarity: {sim:.1%}\n{preview}")

        text_lines.append("\nRECOMMENDATIONS:\n"
                          "  1. Ensure search content matches file line-by-line (including "
                          "whitespace, indentation, newlines, and surrounding content).\n"
                          "  2. Use Read tool to review file content and ensure search "
                          "content is accurate.\n"
                          "  3. If content differs significantly, consider using Write tool "
                          "for complete file replacement.")
        return "".join(text_lines)

    def _build_multiple_matches_message(self, expanded_lines: list[str], positions_1based: list[int],
                                        search_len: int) -> str:
        """Build error message for multiple matches, listing all match positions and previews.

        Args:
            expanded_lines: Target file line list (tabs expanded)
            positions_1based: All matching start line numbers (1-based)
            search_len: Search block length (number of lines)

        Returns:
            Error message text
        """
        lines: list[str] = [
            "ERROR: multiple matches found. Search block must be unique in file.\n",
            f"Found {len(positions_1based)} exact matches:\n",
        ]
        for idx, start_line_1based in enumerate(positions_1based, 1):
            preview_lines = []
            start_idx = start_line_1based - 1
            for off in range(min(search_len, 3)):
                line_num_1based = start_line_1based + off
                line = expanded_lines[start_idx + off]
                preview_lines.append(f"{line_num_1based} | {line}")
            preview = "\n".join(preview_lines)
            if search_len > 3:
                preview += "\n..."
            lines.append(f"\nMatch #{idx} | Start line {start_line_1based}\n{preview}\n")
        return "".join(lines)
