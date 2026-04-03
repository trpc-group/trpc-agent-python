# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""File system tools: read_file, write_file, edit_file, list_dir.

All four tools are implemented as :class:`~trpc_agent_sdk.tools.BaseTool` subclasses.
They share state (``workspace`` / ``allowed_dir``) set at construction time and
have no per-turn external setters, which rules out simple function wrapping via
:class:`~trpc_agent_sdk.tools.FunctionTool`. :class:`BaseTool` provides a clean
``_get_declaration()`` + ``_run_async_impl()`` split that matches the existing
pattern in ``trpc-claw/tools/file_tools/``.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any
from typing import List
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

# ---------------------------------------------------------------------------
# Shared path helper
# ---------------------------------------------------------------------------


def _resolve_path(
    path: str,
    workspace: Optional[Path] = None,
    allowed_dir: Optional[Path] = None,
) -> Path:
    """Resolve *path* against *workspace* and enforce *allowed_dir* restriction."""
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace:
        p = workspace / p
    resolved = p.resolve()
    if allowed_dir:
        try:
            resolved.relative_to(allowed_dir.resolve())
        except ValueError:
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


# ---------------------------------------------------------------------------
# Shared BaseTool base for filesystem tools
# ---------------------------------------------------------------------------


class _FsTool(BaseTool):
    """Internal base — shared constructor and path resolution for fs tools."""

    def __init__(
        self,
        name: str,
        description: str,
        workspace: Optional[Path] = None,
        allowed_dir: Optional[Path] = None,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            filters_name=filters_name,
            filters=filters,
        )
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    def _resolve(self, path: str) -> Path:
        return _resolve_path(path, self._workspace, self._allowed_dir)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class ReadFileTool(_FsTool):
    """Read file contents with optional line-based pagination."""

    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000

    def __init__(
        self,
        workspace: Optional[Path] = None,
        allowed_dir: Optional[Path] = None,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="read_file",
            description=("Read the contents of a file. Returns numbered lines. "
                         "Use offset and limit to paginate through large files."),
            workspace=workspace,
            allowed_dir=allowed_dir,
            filters_name=filters_name,
            filters=filters,
        )

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="read_file",
            description=("Read the contents of a file. Returns numbered lines. "
                         "Use offset and limit to paginate through large files."),
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "path":
                    Schema(type=Type.STRING, description="The file path to read"),
                    "offset":
                    Schema(
                        type=Type.INTEGER,
                        description="Line number to start reading from (1-indexed, default 1)",
                        minimum=1,
                    ),
                    "limit":
                    Schema(
                        type=Type.INTEGER,
                        description="Maximum number of lines to read (default 2000)",
                        minimum=1,
                    ),
                },
                required=["path"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        path = args.get("path", "")
        offset: int = int(args.get("offset") or 1)
        limit: Optional[int] = args.get("limit")
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            all_lines = fp.read_text(encoding="utf-8").splitlines()
            total = len(all_lines)

            if offset < 1:
                offset = 1
            if total == 0:
                return f"(Empty file: {path})"
            if offset > total:
                return f"Error: offset {offset} is beyond end of file ({total} lines)"

            start = offset - 1
            end = min(start + (limit or self._DEFAULT_LIMIT), total)
            numbered = [f"{start + i + 1}| {line}" for i, line in enumerate(all_lines[start:end])]
            result = "\n".join(numbered)

            if len(result) > self._MAX_CHARS:
                trimmed, chars = [], 0
                for line in numbered:
                    chars += len(line) + 1
                    if chars > self._MAX_CHARS:
                        break
                    trimmed.append(line)
                end = start + len(trimmed)
                result = "\n".join(trimmed)

            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file — {total} lines total)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:  # pylint: disable=broad-except
            return f"Error reading file: {e}"


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class WriteFileTool(_FsTool):
    """Write content to a file, creating parent directories as needed."""

    def __init__(
        self,
        workspace: Optional[Path] = None,
        allowed_dir: Optional[Path] = None,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="write_file",
            description="Write content to a file at the given path. Creates parent directories if needed.",
            workspace=workspace,
            allowed_dir=allowed_dir,
            filters_name=filters_name,
            filters=filters,
        )

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="write_file",
            description="Write content to a file at the given path. Creates parent directories if needed.",
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "path": Schema(type=Type.STRING, description="The file path to write to"),
                    "content": Schema(type=Type.STRING, description="The content to write"),
                },
                required=["path", "content"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        path = args.get("path", "")
        content = args.get("content", "")
        try:
            fp = self._resolve(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:  # pylint: disable=broad-except
            return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# edit_file — internal helpers
# ---------------------------------------------------------------------------


def _find_match(content: str, old_text: str) -> tuple[Optional[str], int]:
    """Locate *old_text* in *content*: exact first, then line-trimmed window.

    Both inputs must use LF line endings (caller normalizes CRLF).
    Returns ``(matched_fragment, count)`` or ``(None, 0)``.
    """
    if old_text in content:
        return old_text, content.count(old_text)

    old_lines = old_text.splitlines()
    if not old_lines:
        return None, 0
    stripped_old = [ln.strip() for ln in old_lines]
    content_lines = content.splitlines()

    candidates: list[str] = []
    for i in range(len(content_lines) - len(stripped_old) + 1):
        window = content_lines[i:i + len(stripped_old)]
        if [ln.strip() for ln in window] == stripped_old:
            candidates.append("\n".join(window))

    if candidates:
        return candidates[0], len(candidates)
    return None, 0


def _not_found_msg(old_text: str, content: str, path: str) -> str:
    lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines(keepends=True)
    window = len(old_lines)

    best_ratio, best_start = 0.0, 0
    for i in range(max(1, len(lines) - window + 1)):
        ratio = difflib.SequenceMatcher(None, old_lines, lines[i:i + window]).ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i

    if best_ratio > 0.5:
        diff = "\n".join(
            difflib.unified_diff(
                old_lines,
                lines[best_start:best_start + window],
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
        return (f"Error: old_text not found in {path}.\n"
                f"Best match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}")
    return f"Error: old_text not found in {path}. No similar text found. Verify the file content."


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------


class EditFileTool(_FsTool):
    """Edit a file by replacing a text fragment, with fuzzy whitespace matching."""

    def __init__(
        self,
        workspace: Optional[Path] = None,
        allowed_dir: Optional[Path] = None,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="edit_file",
            description=("Edit a file by replacing old_text with new_text. "
                         "Supports minor whitespace/line-ending differences. "
                         "Set replace_all=true to replace every occurrence."),
            workspace=workspace,
            allowed_dir=allowed_dir,
            filters_name=filters_name,
            filters=filters,
        )

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="edit_file",
            description=("Edit a file by replacing old_text with new_text. "
                         "Supports minor whitespace/line-ending differences. "
                         "Set replace_all=true to replace every occurrence."),
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "path": Schema(type=Type.STRING, description="The file path to edit"),
                    "old_text": Schema(type=Type.STRING, description="The text to find and replace"),
                    "new_text": Schema(type=Type.STRING, description="The replacement text"),
                    "replace_all": Schema(
                        type=Type.BOOLEAN,
                        description="Replace all occurrences (default false)",
                    ),
                },
                required=["path", "old_text", "new_text"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        path = args.get("path", "")
        old_text = args.get("old_text", "")
        new_text = args.get("new_text", "")
        replace_all: bool = bool(args.get("replace_all", False))
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return f"Error: File not found: {path}"

            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            match, count = _find_match(content, old_text.replace("\r\n", "\n"))

            if match is None:
                return _not_found_msg(old_text, content, path)
            if count > 1 and not replace_all:
                return (f"Warning: old_text appears {count} times. "
                        "Provide more context to make it unique, or set replace_all=true.")

            norm_new = new_text.replace("\r\n", "\n")
            new_content = (content.replace(match, norm_new) if replace_all else content.replace(match, norm_new, 1))
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            fp.write_bytes(new_content.encode("utf-8"))
            return f"Successfully edited {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:  # pylint: disable=broad-except
            return f"Error editing file: {e}"


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


class ListDirTool(_FsTool):
    """List directory contents with optional recursion."""

    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".coverage",
        "htmlcov",
    }

    def __init__(
        self,
        workspace: Optional[Path] = None,
        allowed_dir: Optional[Path] = None,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="list_dir",
            description=("List the contents of a directory. "
                         "Set recursive=true to explore nested structure. "
                         "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."),
            workspace=workspace,
            allowed_dir=allowed_dir,
            filters_name=filters_name,
            filters=filters,
        )

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="list_dir",
            description=("List the contents of a directory. "
                         "Set recursive=true to explore nested structure. "
                         "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."),
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "path":
                    Schema(type=Type.STRING, description="The directory path to list"),
                    "recursive":
                    Schema(
                        type=Type.BOOLEAN,
                        description="Recursively list all files (default false)",
                    ),
                    "max_entries":
                    Schema(
                        type=Type.INTEGER,
                        description="Maximum entries to return (default 200)",
                        minimum=1,
                    ),
                },
                required=["path"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        path = args.get("path", "")
        recursive: bool = bool(args.get("recursive", False))
        max_entries: Optional[int] = args.get("max_entries")
        try:
            dp = self._resolve(path)
            if not dp.exists():
                return f"Error: Directory not found: {path}"
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    if any(part in self._IGNORE_DIRS for part in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        rel = item.relative_to(dp)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "D " if item.is_dir() else "F "
                        items.append(f"{pfx}{item.name}")

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:  # pylint: disable=broad-except
            return f"Error listing directory: {e}"
