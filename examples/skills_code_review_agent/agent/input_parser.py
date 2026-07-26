"""Parse diff, file-list, and Git workspace inputs into one contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from pathlib import PureWindowsPath

from .constants import GIT_TIMEOUT_SECONDS
from .constants import MAX_CHANGED_LINES
from .constants import MAX_DIFF_BYTES
from .constants import MAX_FILE_BYTES
from .constants import MAX_FILES
from .constants import MAX_HUNKS_PER_FILE
from .constants import MAX_PATH_LENGTH
from .models import ChangedLine
from .models import DiffHunk
from .models import InputKind
from .models import LineKind
from .models import ReviewFile
from .models import ReviewInput

_HUNK_PATTERN = re.compile(r"^@@ -(?P<old>\d+)(?:,(?P<old_count>\d+))? "
                           r"\+(?P<new>\d+)(?:,(?P<new_count>\d+))? @@", )
_DIFF_HEADER_PATTERN = re.compile(r"^diff --git a/(.+) b/(.+)$")
_NO_NEWLINE_MARKER = "\\ No newline at end of file"
_REVISION_PATTERN = re.compile(r"^[A-Za-z0-9._/@{}~^:+]+$")


class InputValidationError(ValueError):
    """Raised when review input is unsafe or malformed."""


@dataclass(frozen=True)
class GitDiffOptions:
    """Safe Git diff selection options."""

    staged: bool = False
    worktree: bool = False
    base: str | None = None
    head: str | None = None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_size(data: bytes, limit: int, label: str) -> None:
    if len(data) > limit:
        raise InputValidationError(f"{label} exceeds {limit} bytes")
    if b"\x00" in data:
        raise InputValidationError(f"{label} contains NUL bytes")


def _normalize_path(raw_path: str) -> str | None:
    path = raw_path.strip().split("\t", maxsplit=1)[0]
    if path == "/dev/null":
        return None
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    path = path.replace("\\", "/")
    posix_path = PurePosixPath(path)
    windows_path = PureWindowsPath(path)
    if (not path or "\x00" in path or posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive
            or ".." in posix_path.parts):
        raise InputValidationError(f"unsafe path: {raw_path}")
    if len(path) > MAX_PATH_LENGTH:
        raise InputValidationError("path exceeds configured limit")
    return path


def _parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    match = _HUNK_PATTERN.match(header)
    if not match:
        raise InputValidationError(f"invalid hunk header: {header}")
    old_count = int(match.group("old_count") or 1)
    new_count = int(match.group("new_count") or 1)
    return int(match.group("old")), old_count, int(match.group("new")), new_count


def _parse_hunk_lines(lines: list[str], old_start: int, new_start: int) -> list[ChangedLine]:
    changed: list[ChangedLine] = []
    old_line = old_start
    new_line = new_start
    for raw_line in lines:
        if raw_line == _NO_NEWLINE_MARKER:
            continue
        prefix, content = raw_line[:1], raw_line[1:]
        if prefix == "+":
            changed.append(ChangedLine(kind=LineKind.ADDED, content=content, new_line=new_line))
            new_line += 1
        elif prefix == "-":
            changed.append(ChangedLine(kind=LineKind.DELETED, content=content, old_line=old_line))
            old_line += 1
        elif prefix == " ":
            changed.append(ChangedLine(
                kind=LineKind.CONTEXT,
                content=content,
                old_line=old_line,
                new_line=new_line,
            ), )
            old_line += 1
            new_line += 1
        else:
            raise InputValidationError(f"invalid hunk line prefix: {prefix!r}")
    return changed


def _collect_hunk(raw_lines: list[str], start: int) -> tuple[DiffHunk, int]:
    header = raw_lines[start]
    old_start, old_count, new_start, new_count = _parse_hunk_header(header)
    cursor = start + 1
    body: list[str] = []
    seen_old = 0
    seen_new = 0
    while cursor < len(raw_lines):
        if seen_old == old_count and seen_new == new_count:
            break
        line = raw_lines[cursor]
        if line.startswith("@@ ") or line.startswith("diff --git "):
            break
        body.append(line)
        if line != _NO_NEWLINE_MARKER:
            seen_old += int(not line.startswith("+"))
            seen_new += int(not line.startswith("-"))
        cursor += 1
    changed = _parse_hunk_lines(body, old_start, new_start)
    actual_old = sum(line.kind != LineKind.ADDED for line in changed)
    actual_new = sum(line.kind != LineKind.DELETED for line in changed)
    if (actual_old, actual_new) != (old_count, new_count):
        raise InputValidationError(
            f"hunk count mismatch: declared {old_count}/{new_count}, "
            f"parsed {actual_old}/{actual_new}", )
    return DiffHunk(
        header=header,
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        lines=changed,
    ), cursor


def _parse_files(raw_lines: list[str]) -> list[ReviewFile]:
    files: list[ReviewFile] = []
    current: ReviewFile | None = None
    cursor = 0
    while cursor < len(raw_lines):
        line = raw_lines[cursor]
        header = _DIFF_HEADER_PATTERN.match(line)
        if header:
            current = ReviewFile(
                old_path=_normalize_path(header.group(1)),
                new_path=_normalize_path(header.group(2)),
            )
            files.append(current)
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            if current:
                current.is_binary = True
        elif line.startswith("--- "):
            if current is None or current.hunks:
                current = ReviewFile(old_path=_normalize_path(line[4:]))
                files.append(current)
            else:
                current.old_path = _normalize_path(line[4:])
        elif line.startswith("+++ ") and current:
            current.new_path = _normalize_path(line[4:])
        elif line.startswith("@@ ") and current:
            hunk, cursor = _collect_hunk(raw_lines, cursor)
            current.hunks.append(hunk)
            continue
        cursor += 1
    return files


def _validate_parsed_files(files: list[ReviewFile]) -> None:
    if len(files) > MAX_FILES:
        raise InputValidationError(f"input exceeds {MAX_FILES} files")
    changed_lines = 0
    for review_file in files:
        if len(review_file.hunks) > MAX_HUNKS_PER_FILE:
            raise InputValidationError("file exceeds configured hunk limit")
        changed_lines += sum(len(hunk.lines) for hunk in review_file.hunks)
    if changed_lines > MAX_CHANGED_LINES:
        raise InputValidationError("input exceeds configured line limit")


def parse_diff_text(text: str, source: str = "inline") -> ReviewInput:
    """Parse a unified diff string."""
    raw = text.encode("utf-8")
    _check_size(raw, MAX_DIFF_BYTES, "diff")
    files = _parse_files(text.splitlines())
    if not files:
        raise InputValidationError("input contains no Git diff headers")
    _validate_parsed_files(files)
    warnings = [f"binary file skipped: {item.new_path or item.old_path}" for item in files if item.is_binary]
    return ReviewInput(
        kind=InputKind.DIFF,
        source=source,
        digest=_digest(raw),
        files=files,
        warnings=warnings,
    )


def load_diff_file(path: Path) -> ReviewInput:
    """Load and parse one UTF-8 unified diff file."""
    data = path.read_bytes()
    _check_size(data, MAX_DIFF_BYTES, "diff")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputValidationError("diff must be UTF-8") from exc
    return parse_diff_text(text, source=str(path))


def _resolve_listed_file(root: Path, relative_path: str) -> Path:
    normalized = _normalize_path(relative_path)
    if normalized is None:
        raise InputValidationError("file list cannot contain /dev/null")
    root = root.resolve()
    lexical = root / normalized
    candidate = lexical.resolve()
    if candidate != root and root not in candidate.parents:
        raise InputValidationError(f"file escapes repository: {relative_path}")
    lexical_path = os.path.normcase(os.path.abspath(lexical))
    resolved_path = os.path.normcase(str(candidate))
    if lexical_path != resolved_path or not candidate.is_file():
        raise InputValidationError(f"file is missing or symbolic: {relative_path}")
    return candidate


def _file_as_added_hunk(path: Path, root: Path) -> ReviewFile:
    data = path.read_bytes()
    _check_size(data, MAX_FILE_BYTES, str(path))
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise InputValidationError(f"file must be UTF-8: {path}") from exc
    changed = [
        ChangedLine(kind=LineKind.ADDED, content=line, new_line=index) for index, line in enumerate(lines, start=1)
    ]
    relative = path.relative_to(root).as_posix()
    hunk = DiffHunk(
        header=f"@@ -0,0 +1,{len(lines)} @@",
        old_start=0,
        old_count=0,
        new_start=1,
        new_count=len(lines),
        lines=changed,
    )
    return ReviewFile(new_path=relative, hunks=[hunk])


def load_file_list(path: Path, repo_path: Path | None = None) -> ReviewInput:
    """Load repository-relative UTF-8 files named in a text file."""
    data = path.read_bytes()
    _check_size(data, MAX_FILE_BYTES, "file list")
    root = (repo_path or path.parent).resolve()
    try:
        names = [line.strip() for line in data.decode("utf-8").splitlines() if line.strip()]
    except UnicodeDecodeError as exc:
        raise InputValidationError("file list must be UTF-8") from exc
    if not names:
        raise InputValidationError("file list must not be empty")
    if len(names) > MAX_FILES:
        raise InputValidationError(f"file list exceeds {MAX_FILES} entries")
    files = [_file_as_added_hunk(_resolve_listed_file(root, name), root) for name in names]
    _validate_parsed_files(files)
    canonical = json.dumps(
        [item.model_dump(mode="json") for item in files],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ReviewInput(
        kind=InputKind.FILE_LIST,
        source=str(path),
        digest=_digest(canonical),
        files=files,
    )


def _run_git_diff(repo_path: Path, arguments: list[str]) -> str:
    command = [
        "git",
        "-C",
        str(repo_path),
        "-c",
        "core.quotePath=false",
        "diff",
        "--no-ext-diff",
        "--no-color",
        *arguments,
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise InputValidationError("git diff timed out") from exc
    _check_size(result.stdout, MAX_DIFF_BYTES, "git diff")
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise InputValidationError(f"git diff failed: {error}")
    return result.stdout.decode("utf-8")


def _validate_revision(revision: str) -> str:
    if revision.startswith("-") or not _REVISION_PATTERN.fullmatch(revision):
        raise InputValidationError(f"unsafe Git revision: {revision!r}")
    return revision


def load_repo_diff(
    repo_path: Path,
    options: GitDiffOptions | None = None,
) -> ReviewInput:
    """Read a worktree, staged, or revision-range diff from a Git repository."""
    root = repo_path.resolve()
    options = options or GitDiffOptions()
    if not (root / ".git").exists():
        raise InputValidationError(f"not a Git repository: {root}")
    if options.base or options.head:
        if not options.base or not options.head:
            raise InputValidationError("base and head must be provided together")
        arguments = [
            _validate_revision(options.base),
            _validate_revision(options.head),
            "--",
        ]
    elif options.staged:
        arguments = ["--cached"]
    elif options.worktree:
        arguments = []
    else:
        arguments = ["HEAD"]
    parsed = parse_diff_text(_run_git_diff(root, arguments), source=str(root))
    return parsed.model_copy(update={"kind": InputKind.REPOSITORY})
