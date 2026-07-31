# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Normalize diff files, fixtures, and Git worktrees into review input data."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Iterable

from .models import ChangedFile
from .models import ChangedLine
from .models import DiffHunk
from .models import InputSummary
from .models import InputType

_DIFF_HEADER = re.compile(r"^diff --git a/(.*?) b/(.*?)$")
_HUNK_HEADER = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
                          r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)$")
_QUOTED_PATH = re.compile(r'^"(.*)"$')
_GIT_TIMEOUT_SECONDS = 15.0


class InputParseError(ValueError):
    """Raised when an input source cannot be normalized."""


def parse_unified_diff(
    diff_text: str,
    *,
    task_id: str = "",
    input_type: InputType = InputType.DIFF_FILE,
    input_ref: str = "<diff>",
) -> InputSummary:
    """Parse a unified diff while retaining hunk and line-number semantics.

    The parser is deliberately tolerant at file boundaries. A malformed hunk
    produces a diagnostic and does not discard files and hunks parsed before
    it, which is useful when a patch contains a binary section or truncated
    output.
    """
    normalized = diff_text.replace("\r\n", "\n").replace("\r", "\n")
    changed_files: list[ChangedFile] = []
    diagnostics: list[str] = []
    current: ChangedFile | None = None
    current_hunk: DiffHunk | None = None
    old_line = 0
    new_line = 0

    def finish_file() -> None:
        nonlocal current, current_hunk
        if current is not None:
            current.candidate_lines = sorted(set(current.candidate_lines))
            changed_files.append(current)
        current = None
        current_hunk = None

    for raw_line in normalized.splitlines():
        header = _DIFF_HEADER.match(raw_line)
        if header:
            finish_file()
            old_path, new_path = header.groups()
            current = ChangedFile(path=_unquote_path(new_path), old_path=_unquote_path(old_path))
            continue

        if current is None:
            if raw_line.strip():
                diagnostics.append("input contains content before a diff file header")
            continue

        if raw_line.startswith("new file mode "):
            current.status = "added"
            continue
        if raw_line.startswith("deleted file mode "):
            current.status = "deleted"
            continue
        if raw_line.startswith("rename from "):
            current.old_path = _unquote_path(raw_line[len("rename from "):])
            current.status = "renamed"
            continue
        if raw_line.startswith("rename to "):
            current.path = _unquote_path(raw_line[len("rename to "):])
            current.status = "renamed"
            continue
        if raw_line.startswith("similarity index ") or raw_line.startswith("index "):
            continue
        if raw_line.startswith("Binary files ") or raw_line.startswith("GIT binary patch"):
            current.is_binary = True
            diagnostics.append(f"{current.path}: binary patch content is not parsed")
            current_hunk = None
            continue
        if raw_line.startswith("--- "):
            old_path = _parse_patch_path(raw_line[4:])
            if old_path and old_path != "/dev/null":
                current.old_path = old_path
            continue
        if raw_line.startswith("+++ "):
            new_path = _parse_patch_path(raw_line[4:])
            if new_path and new_path != "/dev/null":
                current.path = new_path
            continue

        hunk_match = _HUNK_HEADER.match(raw_line)
        if hunk_match:
            current_hunk = DiffHunk(
                old_start=int(hunk_match.group("old_start")),
                old_count=int(hunk_match.group("old_count") or "1"),
                new_start=int(hunk_match.group("new_start")),
                new_count=int(hunk_match.group("new_count") or "1"),
                section_header=hunk_match.group("section").strip(),
            )
            current.hunks.append(current_hunk)
            old_line = current_hunk.old_start
            new_line = current_hunk.new_start
            continue
        if raw_line.startswith("@@"):
            diagnostics.append(f"{current.path}: malformed hunk header: {raw_line}")
            current_hunk = None
            continue
        if current_hunk is None:
            if raw_line.strip() and not raw_line.startswith("\\ "):
                diagnostics.append(f"{current.path}: unexpected diff metadata: {raw_line}")
            continue
        if raw_line.startswith("\\ No newline at end of file"):
            diagnostics.append(f"{current.path}: missing newline at end of file")
            continue
        if not raw_line:
            diagnostics.append(f"{current.path}: malformed empty hunk line")
            continue

        marker = raw_line[0]
        content = raw_line[1:]
        if marker == " ":
            current_hunk.lines.append(ChangedLine("context", content, old_line=old_line, new_line=new_line))
            old_line += 1
            new_line += 1
        elif marker == "+":
            current_hunk.lines.append(ChangedLine("added", content, new_line=new_line))
            current.added_lines += 1
            current.candidate_lines.append(new_line)
            new_line += 1
        elif marker == "-":
            current_hunk.lines.append(ChangedLine("deleted", content, old_line=old_line))
            current.deleted_lines += 1
            old_line += 1
        else:
            diagnostics.append(f"{current.path}: malformed hunk line: {raw_line}")

    finish_file()
    if not changed_files and normalized.strip():
        diagnostics.append("input contains no parseable git diff file headers")

    return _build_summary(
        task_id=task_id,
        input_type=input_type,
        input_ref=input_ref,
        changed_files=changed_files,
        raw_text=normalized,
        diagnostics=_unique(diagnostics),
    )


def parse_diff_file(path: str | Path, *, task_id: str = "") -> InputSummary:
    """Read a UTF-8 unified diff file and normalize it."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise InputParseError(f"diff file not found: {source}")
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as ex:
        raise InputParseError(f"diff file is not UTF-8 text: {source}") from ex
    return parse_unified_diff(
        text,
        task_id=task_id,
        input_type=InputType.DIFF_FILE,
        input_ref=str(source.resolve()),
    )


def parse_repo_path(path: str | Path, *, task_id: str = "") -> InputSummary:
    """Parse tracked, staged, unstaged, and untracked Git worktree changes."""
    requested = Path(path).expanduser()
    if not requested.is_dir():
        raise InputParseError(f"repository path not found: {requested}")
    root_text = _run_git(requested, ["rev-parse", "--show-toplevel"])
    root = Path(root_text.strip()).resolve()

    if _has_head(root):
        tracked_diff = _run_git(root, ["diff", "--binary", "HEAD", "--"])
    else:
        staged_diff = _run_git(root, ["diff", "--binary", "--cached", "--"])
        unstaged_diff = _run_git(root, ["diff", "--binary", "--"])
        tracked_diff = _merge_diff_texts(staged_diff, unstaged_diff)

    untracked_paths = _untracked_paths(root)
    untracked_diff, untracked_diagnostics = _build_untracked_diff(root, untracked_paths)
    combined = _merge_diff_texts(tracked_diff, untracked_diff)
    summary = parse_unified_diff(
        combined,
        task_id=task_id,
        input_type=InputType.REPO_PATH,
        input_ref=str(root),
    )
    summary.diagnostics.extend(untracked_diagnostics)
    summary.diagnostics = _unique(summary.diagnostics)
    summary.warnings = list(summary.diagnostics)
    if not combined and not untracked_paths:
        summary.warnings.append("repository has no tracked, staged, or untracked changes")
    return summary


def parse_fixture(name: str | Path, *, task_id: str = "") -> InputSummary:
    """Parse a named fixture or an explicit fixture path."""
    source = Path(name).expanduser()
    if not source.is_file():
        source = Path(__file__).parents[1] / "fixtures" / f"{name}.diff"
    if not source.is_file():
        raise InputParseError(f"fixture not found: {name}")
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as ex:
        raise InputParseError(f"fixture is not UTF-8 text: {source}") from ex
    return parse_unified_diff(
        text,
        task_id=task_id,
        input_type=InputType.FIXTURE,
        input_ref=str(source.resolve()),
    )


def parse_file_list(path: str | Path, *, task_id: str = "") -> InputSummary:
    """Parse a UTF-8 file-list input into added-file style review data."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise InputParseError(f"file list not found: {source}")
    try:
        list_text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as ex:
        raise InputParseError(f"file list is not UTF-8 text: {source}") from ex

    base_dir = source.parent.resolve()
    changed_files: list[ChangedFile] = []
    diagnostics: list[str] = []
    digest = hashlib.sha256(list_text.encode("utf-8"))
    for raw_line in list_text.splitlines():
        entry = raw_line.strip()
        if not entry or entry.startswith("#"):
            continue
        requested = Path(entry).expanduser()
        file_path = requested if requested.is_absolute() else base_dir / requested
        try:
            resolved = file_path.resolve()
        except OSError as ex:
            diagnostics.append(f"{entry}: unable to resolve path: {ex}")
            continue
        if not resolved.exists():
            diagnostics.append(f"{entry}: file not found")
            continue
        if not resolved.is_file():
            diagnostics.append(f"{entry}: not a regular file")
            continue
        try:
            content = resolved.read_bytes()
        except OSError as ex:
            diagnostics.append(f"{entry}: unable to read file: {ex}")
            continue
        digest.update(str(resolved).encode("utf-8"))
        digest.update(content)
        if b"\0" in content:
            diagnostics.append(f"{entry}: binary file content is not parsed")
            changed_files.append(
                ChangedFile(path=_relative_file_list_path(resolved, base_dir), status="added", is_binary=True))
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            diagnostics.append(f"{entry}: file is not UTF-8 text")
            changed_files.append(
                ChangedFile(path=_relative_file_list_path(resolved, base_dir), status="added", is_binary=True))
            continue
        rel_path = _relative_file_list_path(resolved, base_dir)
        lines = text.splitlines()
        hunk = DiffHunk(old_start=0, old_count=0, new_start=1, new_count=len(lines))
        changed = ChangedFile(path=rel_path, old_path="/dev/null", status="added", hunks=[hunk])
        for index, line in enumerate(lines, start=1):
            hunk.lines.append(ChangedLine("added", line, new_line=index))
            changed.added_lines += 1
            changed.candidate_lines.append(index)
        changed.candidate_lines = sorted(set(changed.candidate_lines))
        changed_files.append(changed)

    return _build_summary(
        task_id=task_id,
        input_type=InputType.FILE_LIST,
        input_ref=str(source.resolve()),
        changed_files=changed_files,
        raw_text=digest.hexdigest(),
        diagnostics=_unique(diagnostics),
    )


def _build_summary(
    *,
    task_id: str,
    input_type: InputType,
    input_ref: str,
    changed_files: list[ChangedFile],
    raw_text: str,
    diagnostics: list[str],
) -> InputSummary:
    hunk_count = sum(len(item.hunks) for item in changed_files)
    added = sum(item.added_lines for item in changed_files)
    deleted = sum(item.deleted_lines for item in changed_files)
    return InputSummary(
        task_id=task_id,
        input_type=input_type,
        input_ref=input_ref,
        changed_files=changed_files,
        raw_diff_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        file_count=len(changed_files),
        hunk_count=hunk_count,
        added_lines=added,
        deleted_lines=deleted,
        summary=f"{len(changed_files)} file(s), {hunk_count} hunk(s), {added} added, {deleted} deleted",
        diagnostics=list(diagnostics),
        warnings=list(diagnostics),
    )


def _parse_patch_path(value: str) -> str:
    path = value.split("\t", 1)[0].strip()
    return _strip_prefix(_unquote_path(path))


def _strip_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _unquote_path(path: str) -> str:
    match = _QUOTED_PATH.match(path.strip())
    return match.group(1) if match else path.strip()


def _relative_file_list_path(path: Path, base_dir: Path) -> str:
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return str(path)


def _run_git(root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as ex:
        raise InputParseError(f"git command timed out in {root}: git {' '.join(args)}") from ex
    except OSError as ex:
        raise InputParseError(f"unable to execute git in {root}: {ex}") from ex
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise InputParseError(f"git command failed in {root}: git {' '.join(args)}: {detail}")
    return result.stdout


def _has_head(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as ex:
        raise InputParseError(f"git HEAD lookup timed out in {root}") from ex
    except OSError as ex:
        raise InputParseError(f"unable to execute git in {root}: {ex}") from ex
    return result.returncode == 0


def _untracked_paths(root: Path) -> list[str]:
    output = _run_git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    paths: list[str] = []
    records = output.split("\0")
    for record in records:
        if len(record) >= 4 and record[:2] == "??":
            paths.append(record[3:])
    return paths


def _build_untracked_diff(root: Path, paths: Iterable[str]) -> tuple[str, list[str]]:
    chunks: list[str] = []
    diagnostics: list[str] = []
    for rel_path in paths:
        source = root / rel_path
        if not source.is_file():
            diagnostics.append(f"untracked path is not a regular file: {rel_path}")
            continue
        try:
            content = source.read_bytes()
        except OSError as ex:
            diagnostics.append(f"unable to read untracked file {rel_path}: {ex}")
            continue
        if b"\0" in content:
            chunks.append(f"diff --git a/{rel_path} b/{rel_path}\n"
                          f"new file mode 100644\n"
                          f"Binary files /dev/null and b/{rel_path} differ\n")
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            chunks.append(f"diff --git a/{rel_path} b/{rel_path}\n"
                          f"new file mode 100644\n"
                          f"Binary files /dev/null and b/{rel_path} differ\n")
            continue
        content_lines = text.splitlines()
        chunks.append(f"diff --git a/{rel_path} b/{rel_path}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel_path}\n")
        if content_lines:
            chunks.append(f"@@ -0,0 +1,{len(content_lines)} @@\n")
            chunks.extend(f"+{line}\n" for line in content_lines)
        else:
            chunks.append("@@ -0,0 +0 @@\n")
    return "".join(chunks), diagnostics


def _merge_diff_texts(*texts: str) -> str:
    return "\n".join(text.strip("\n") for text in texts if text.strip())


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


# Compatibility aliases retained for callers of the initial example contract.
parse_diff_text = parse_unified_diff
parse_repo = parse_repo_path
