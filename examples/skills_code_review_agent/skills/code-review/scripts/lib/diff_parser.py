#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Unified diff domain parser.

The module is intentionally standard-library only because the same source is
staged into an isolated workspace by the code-review Skill.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Tuple


_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_GIT_PATH_TOKEN = re.compile(r'"(?:\\.|[^"])*"|\S+')


@dataclass(frozen=True)
class Hunk:
    """One unified-diff hunk with explicit old/new coordinates."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    context_lines: Dict[int, str]
    added_lines: Dict[int, str]
    deleted_lines: Dict[int, str]
    old_to_new_line_map: Dict[int, int]


@dataclass(frozen=True)
class FileChange:
    """A normalized file-level change."""

    old_path: str
    new_path: str
    normalized_path: str
    status: str
    review_scope: str
    is_binary: bool
    hunks: Tuple[Hunk, ...]
    old_changed_lines: Tuple[int, ...]
    new_changed_lines: Tuple[int, ...]
    full_text: str | None
    analysis_mode: str


@dataclass(frozen=True)
class ChangeSet:
    """Deterministic summary of one review input."""

    source_kind: str
    input_sha256: str
    files: Tuple[FileChange, ...]
    file_count: int
    hunk_count: int
    additions: int
    deletions: int
    parse_warnings: Tuple[str, ...]


def _normalize_path(path: str, *, strip_diff_prefix: bool = False) -> str:
    """规范化 Git 路径并拒绝跨出受控工作区的组成部分。"""

    normalized = path.strip().replace("\\", "/")
    if normalized in {"/dev/null", "dev/null"}:
        return "/dev/null"
    if strip_diff_prefix and normalized.startswith(("a/", "b/")):
        normalized = normalized[2:]
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError("diff path is outside the review namespace")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("diff path is outside the review namespace")
    return "/".join(parts)


def _decode_git_path(path: str) -> str:
    """解码 Git 头中可能带引号转义的单个路径标记。"""

    path = path.strip()
    if len(path) >= 2 and path[0] == path[-1] == '"':
        try:
            decoded = ast.literal_eval(path)
        except (SyntaxError, ValueError):
            decoded = path[1:-1]
        try:
            return decoded.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return decoded
    return path


def _parse_git_header(line: str) -> Tuple[str, str]:
    """解析 ``diff --git`` 文件头并返回旧、新侧路径。"""

    path_tokens = _GIT_PATH_TOKEN.findall(line[len("diff --git "):])
    if len(path_tokens) != 2:
        raise ValueError("invalid git diff file header")
    return (
        _normalize_path(_decode_git_path(path_tokens[0]), strip_diff_prefix=True),
        _normalize_path(_decode_git_path(path_tokens[1]), strip_diff_prefix=True),
    )


def _parse_file_marker(line: str) -> str:
    """解析 ``---`` 或 ``+++`` 标记中的规范化文件路径。"""

    marker_value = line[4:].split("\t", 1)[0]
    return _normalize_path(
        _decode_git_path(marker_value),
        strip_diff_prefix=True,
    )


def _parse_hunk(lines: List[str], start: int) -> Tuple[Hunk, int, bool]:
    """从给定索引解析一个 unified diff hunk 及其结束位置。"""

    match = _HUNK_HEADER.match(lines[start])
    if match is None:
        raise ValueError("invalid unified diff hunk header")

    old_start = int(match.group("old_start"))
    old_count = int(match.group("old_count") or "1")
    new_start = int(match.group("new_start"))
    new_count = int(match.group("new_count") or "1")
    old_line = old_start
    new_line = new_start
    context_lines: Dict[int, str] = {}
    added_lines: Dict[int, str] = {}
    deleted_lines: Dict[int, str] = {}
    old_to_new_line_map: Dict[int, int] = {}
    new_side_missing_newline = False
    last_content_in_new_side = False

    index = start + 1
    while (
        index < len(lines)
        and (
            old_line - old_start < old_count
            or new_line - new_start < new_count
        )
    ):
        line = lines[index]
        if line == r"\ No newline at end of file":
            if last_content_in_new_side:
                new_side_missing_newline = True
            index += 1
            continue
        if line.startswith("+"):
            added_lines[new_line] = line[1:]
            new_line += 1
            new_side_missing_newline = False
            last_content_in_new_side = True
        elif line.startswith("-"):
            deleted_lines[old_line] = line[1:]
            old_line += 1
            last_content_in_new_side = False
        elif line.startswith(" "):
            context_lines[new_line] = line[1:]
            old_to_new_line_map[old_line] = new_line
            old_line += 1
            new_line += 1
            new_side_missing_newline = False
            last_content_in_new_side = True
        else:
            raise ValueError("invalid unified diff hunk body")
        index += 1

    if (
        old_line - old_start != old_count
        or new_line - new_start != new_count
    ):
        raise ValueError("unified diff hunk body does not match header counts")
    if (
        index < len(lines)
        and lines[index] == r"\ No newline at end of file"
    ):
        if last_content_in_new_side:
            new_side_missing_newline = True
        index += 1

    return (
        Hunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            context_lines=context_lines,
            added_lines=added_lines,
            deleted_lines=deleted_lines,
            old_to_new_line_map=old_to_new_line_map,
        ),
        index,
        new_side_missing_newline,
    )


def _reconstruct_added_file(
    hunks: Tuple[Hunk, ...],
    *,
    final_line_missing_newline: bool,
) -> str | None:
    """在新增文件所有内容可见时重建完整文本供 AST 使用。"""

    if not hunks:
        return ""

    expected_new_line = 1
    reconstructed_lines: List[str] = []
    for hunk in hunks:
        if (
            hunk.old_start != 0
            or hunk.old_count != 0
            or hunk.deleted_lines
            or hunk.context_lines
            or hunk.old_to_new_line_map
            or hunk.new_start != expected_new_line
        ):
            return None
        expected_numbers = tuple(
            range(hunk.new_start, hunk.new_start + hunk.new_count)
        )
        if tuple(hunk.added_lines) != expected_numbers:
            return None
        reconstructed_lines.extend(hunk.added_lines.values())
        expected_new_line += hunk.new_count

    full_text = "\n".join(reconstructed_lines)
    if reconstructed_lines and not final_line_missing_newline:
        full_text += "\n"
    return full_text


def _analysis_mode(path: str, full_text: str | None) -> Tuple[str, str | None]:
    """返回安全的分析模式及可选的脱敏解析告警。"""

    if full_text is None or not path.endswith(".py"):
        return "diff_heuristic", None
    try:
        ast.parse(full_text)
    except (SyntaxError, ValueError, RecursionError):
        # Never expose a parser exception because it can include source text.
        return "diff_heuristic", f"ast_parse_failed:{path}"
    return "ast_validated", None


def build_snapshot_change_set(
    file_contents: Mapping[str, str],
    *,
    source_kind: str = "files",
) -> ChangeSet:
    """将显式文件快照构造成全文件范围的 ChangeSet。"""

    """Build a full-file ChangeSet from explicitly supplied text snapshots."""

    if source_kind not in {"files", "fixture"}:
        raise ValueError("source_kind must describe a snapshot input")

    normalized_files: Dict[str, str] = {}
    for path, content in file_contents.items():
        normalized_path = _normalize_path(path)
        if not normalized_path or normalized_path == "/dev/null":
            raise ValueError("snapshot path must identify a file")
        if normalized_path in normalized_files:
            raise ValueError("snapshot paths must be unique after normalization")
        normalized_files[normalized_path] = content

    hasher = hashlib.sha256()
    hasher.update(b"code-review-snapshot-v1\0")
    files: List[FileChange] = []
    parse_warnings: List[str] = []
    for normalized_path in sorted(normalized_files):
        full_text = normalized_files[normalized_path]
        path_bytes = normalized_path.encode("utf-8")
        content_bytes = full_text.encode("utf-8")
        hasher.update(len(path_bytes).to_bytes(8, "big"))
        hasher.update(path_bytes)
        hasher.update(len(content_bytes).to_bytes(8, "big"))
        hasher.update(content_bytes)

        text_lines = full_text.splitlines()
        if text_lines:
            hunk = Hunk(
                old_start=0,
                old_count=0,
                new_start=1,
                new_count=len(text_lines),
                context_lines={},
                added_lines={
                    line_number: text
                    for line_number, text in enumerate(text_lines, start=1)
                },
                deleted_lines={},
                old_to_new_line_map={},
            )
            hunks = (hunk,)
        else:
            hunks = ()

        analysis_mode, parse_warning = _analysis_mode(normalized_path, full_text)
        if parse_warning is not None:
            parse_warnings.append(parse_warning)
        files.append(
            FileChange(
                old_path="/dev/null",
                new_path=normalized_path,
                normalized_path=normalized_path,
                status="snapshot",
                review_scope="full_file",
                is_binary=False,
                hunks=hunks,
                old_changed_lines=(),
                new_changed_lines=tuple(range(1, len(text_lines) + 1)),
                full_text=full_text,
                analysis_mode=analysis_mode,
            )
        )

    return ChangeSet(
        source_kind=source_kind,
        input_sha256=hasher.hexdigest(),
        files=tuple(files),
        file_count=len(files),
        hunk_count=sum(len(file_change.hunks) for file_change in files),
        additions=sum(
            len(file_change.new_changed_lines) for file_change in files
        ),
        deletions=0,
        parse_warnings=tuple(parse_warnings),
    )


def restore_change_set_context(
    change_set: ChangeSet,
    *,
    source_kind: str,
    input_sha256: str,
    full_files: Mapping[str, str],
    file_metadata: Mapping[str, Mapping[str, object]],
) -> ChangeSet:
    """恢复宿主已验证的完整文件上下文和输入语义，供沙箱 AST 规则使用。"""

    if source_kind not in {"diff_file", "repo_path", "files", "fixture"}:
        raise ValueError("input source_kind is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", input_sha256) is None:
        raise ValueError("input sha256 is invalid")
    parsed_paths = {file_change.normalized_path for file_change in change_set.files}
    if set(full_files) - parsed_paths or set(file_metadata) != parsed_paths:
        raise ValueError("input file metadata does not match parsed files")

    allowed_statuses = {
        "added",
        "deleted",
        "modified",
        "renamed",
        "snapshot",
    }
    allowed_scopes = {
        "changed_lines",
        "deleted_lines",
        "full_file",
        "skipped",
    }
    allowed_analysis_modes = {
        "ast_validated",
        "diff_heuristic",
        "skipped",
    }
    restored_files: List[FileChange] = []
    parse_warnings = list(change_set.parse_warnings)
    for file_change in change_set.files:
        metadata = file_metadata[file_change.normalized_path]
        status = metadata.get("status")
        review_scope = metadata.get("review_scope")
        analysis_mode = metadata.get("analysis_mode")
        is_binary = metadata.get("is_binary")
        if (
            status not in allowed_statuses
            or review_scope not in allowed_scopes
            or analysis_mode not in allowed_analysis_modes
            or not isinstance(is_binary, bool)
        ):
            raise ValueError("input file metadata is invalid")
        full_text = full_files.get(file_change.normalized_path)
        if full_text is not None:
            if is_binary or status == "deleted":
                raise ValueError("input full text is invalid for file status")
            analysis_mode, warning = _analysis_mode(
                file_change.normalized_path,
                full_text,
            )
            if warning is not None and warning not in parse_warnings:
                parse_warnings.append(warning)
        restored_files.append(
            replace(
                file_change,
                status=status,
                review_scope=review_scope,
                full_text=full_text,
                analysis_mode=analysis_mode,
                is_binary=is_binary,
            )
        )

    return replace(
        change_set,
        source_kind=source_kind,
        input_sha256=input_sha256,
        files=tuple(restored_files),
        parse_warnings=tuple(parse_warnings),
    )


def parse_unified_diff(
    diff_text: str,
    *,
    source_kind: str = "diff_file",
) -> ChangeSet:
    """解析统一 diff，保留新旧侧行号、范围和安全解析摘要。"""

    """Parse unified diff text into the shared review domain model."""

    if source_kind not in {"diff_file", "repo_path", "fixture"}:
        raise ValueError("source_kind must describe a diff input")
    input_sha256 = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    lines = diff_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    files: List[FileChange] = []
    parse_warnings: List[str] = []
    index = 0

    while index < len(lines):
        is_git_header = lines[index].startswith("diff --git ")
        is_plain_header = (
            lines[index].startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
        )
        if not is_git_header and not is_plain_header:
            index += 1
            continue

        if is_git_header:
            old_path, new_path = _parse_git_header(lines[index])
            index += 1
            seen_file_markers = False
        else:
            old_path = _parse_file_marker(lines[index])
            new_path = _parse_file_marker(lines[index + 1])
            index += 2
            seen_file_markers = True
        hunks: List[Hunk] = []
        hunk_missing_newline: List[bool] = []
        is_binary = False
        is_rename = False
        file_mode_status = None

        while index < len(lines) and not lines[index].startswith("diff --git "):
            line = lines[index]
            if (
                seen_file_markers
                and line.startswith("--- ")
                and index + 1 < len(lines)
                and lines[index + 1].startswith("+++ ")
            ):
                break
            if line.startswith("--- "):
                old_path = _parse_file_marker(line)
                seen_file_markers = True
            elif line.startswith("+++ "):
                new_path = _parse_file_marker(line)
            elif line.startswith("rename from "):
                old_path = _normalize_path(
                    _decode_git_path(line[len("rename from "):])
                )
                is_rename = True
            elif line.startswith("rename to "):
                new_path = _normalize_path(
                    _decode_git_path(line[len("rename to "):])
                )
                is_rename = True
            elif line.startswith(("Binary files ", "GIT binary patch")):
                is_binary = True
            elif line.startswith("new file mode "):
                file_mode_status = "added"
            elif line.startswith("deleted file mode "):
                file_mode_status = "deleted"
            elif line.startswith("@@ "):
                hunk, index, missing_newline = _parse_hunk(lines, index)
                hunks.append(hunk)
                hunk_missing_newline.append(missing_newline)
                continue
            index += 1

        if old_path == "/dev/null" or file_mode_status == "added":
            status = "added"
            old_path = "/dev/null"
        elif new_path == "/dev/null" or file_mode_status == "deleted":
            status = "deleted"
            new_path = "/dev/null"
        elif is_rename:
            status = "renamed"
        else:
            status = "modified"
        normalized_path = old_path if status == "deleted" else new_path
        old_changed_lines = tuple(
            line_number
            for hunk in hunks
            for line_number in hunk.deleted_lines
        )
        new_changed_lines = tuple(
            line_number
            for hunk in hunks
            for line_number in hunk.added_lines
        )
        full_text = None
        if status == "added" and not is_binary:
            full_text = _reconstruct_added_file(
                tuple(hunks),
                final_line_missing_newline=(
                    hunk_missing_newline[-1] if hunk_missing_newline else False
                ),
            )

        if is_binary:
            review_scope = "skipped"
            analysis_mode = "skipped"
        elif status == "added" and full_text is not None:
            review_scope = "full_file"
            analysis_mode, parse_warning = _analysis_mode(normalized_path, full_text)
            if parse_warning is not None:
                parse_warnings.append(parse_warning)
        else:
            review_scope = (
                "deleted_lines" if status == "deleted" else "changed_lines"
            )
            analysis_mode = "diff_heuristic"
        files.append(
            FileChange(
                old_path=old_path,
                new_path=new_path,
                normalized_path=normalized_path,
                status=status,
                review_scope=review_scope,
                is_binary=is_binary,
                hunks=tuple(hunks),
                old_changed_lines=old_changed_lines,
                new_changed_lines=new_changed_lines,
                full_text=full_text,
                analysis_mode=analysis_mode,
            )
        )

    return ChangeSet(
        source_kind=source_kind,
        input_sha256=input_sha256,
        files=tuple(files),
        file_count=len(files),
        hunk_count=sum(len(file_change.hunks) for file_change in files),
        additions=sum(
            len(hunk.added_lines)
            for file_change in files
            for hunk in file_change.hunks
        ),
        deletions=sum(
            len(hunk.deleted_lines)
            for file_change in files
            for hunk in file_change.hunks
        ),
        parse_warnings=tuple(parse_warnings),
    )
