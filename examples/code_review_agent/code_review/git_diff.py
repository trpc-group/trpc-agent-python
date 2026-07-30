"""Safe, deterministic Git diff collection and parsing."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import ChangedFile, ChangedLine, ChangeType, DiffHunk, LineChangeType

_HUNK_HEADER = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
                          r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)$")

_LANGUAGES = {
    ".bash": "shell",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".md": "markdown",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

_STATUS_TYPES = {
    "A": ChangeType.ADDED,
    "M": ChangeType.MODIFIED,
    "D": ChangeType.DELETED,
    "R": ChangeType.RENAMED,
    "C": ChangeType.COPIED,
    "T": ChangeType.TYPE_CHANGED,
    "U": ChangeType.UNMERGED,
}


class GitDiffError(RuntimeError):
    """Raised when repository validation or Git collection fails."""


@dataclass(frozen=True)
class _FileStatus:
    status: str
    path: str
    old_path: str | None = None


class GitDiffCollector:
    """Collect changes between two commits without invoking a shell."""

    def __init__(self, repository: str | Path, *, context_lines: int = 3):
        self.repository = Path(repository).expanduser().resolve()
        self.context_lines = max(0, context_lines)
        self._validate_repository()

    def collect(
        self,
        base_revision: str,
        head_revision: str,
        *,
        use_merge_base: bool = True,
    ) -> tuple[str, list[ChangedFile]]:
        """Validate revisions and collect one parsed patch per changed file."""
        base_commit = self.resolve_revision(base_revision)
        head_commit = self.resolve_revision(head_revision)
        effective_base = self.merge_base(base_commit, head_commit) if use_merge_base else base_commit
        statuses = self._collect_statuses(effective_base, head_commit)
        changed_files = [self._collect_file(effective_base, head_commit, status) for status in statuses]
        return effective_base, changed_files

    def resolve_revision(self, revision: str) -> str:
        """Resolve a user-provided revision to a full commit hash."""
        if not revision or revision.startswith("-"):
            raise GitDiffError(f"Invalid Git revision: {revision!r}")
        return self._git("rev-parse", "--verify", f"{revision}^{{commit}}").strip()

    def merge_base(self, base_commit: str, head_commit: str) -> str:
        """Resolve the merge base used by pull-request-style comparisons."""
        result = self._git("merge-base", base_commit, head_commit).strip()
        if not result:
            raise GitDiffError("The revisions do not have a merge base")
        return result

    def _validate_repository(self) -> None:
        if not self.repository.is_dir():
            raise GitDiffError(f"Repository directory does not exist: {self.repository}")
        inside = self._git("rev-parse", "--is-inside-work-tree").strip()
        if inside != "true":
            raise GitDiffError(f"Not a Git work tree: {self.repository}")

    def _git_bytes(self, *args: str) -> bytes:
        command = ["git", "-C", str(self.repository), *args]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            raise GitDiffError(f"Unable to execute Git: {exc}") from exc
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise GitDiffError(message or f"Git command failed with exit code {result.returncode}")
        return result.stdout

    def _git(self, *args: str) -> str:
        return self._git_bytes(*args).decode("utf-8", errors="replace")

    def _collect_statuses(self, base_commit: str, head_commit: str) -> list[_FileStatus]:
        raw = self._git_bytes(
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            "--find-copies",
            base_commit,
            head_commit,
            "--",
        )
        tokens = raw.decode("utf-8", errors="surrogateescape").split("\0")
        statuses: list[_FileStatus] = []
        index = 0
        while index < len(tokens) - 1:
            status = tokens[index]
            index += 1
            if not status:
                continue
            code = status[0]
            if code in {"R", "C"}:
                if index + 1 >= len(tokens):
                    raise GitDiffError("Malformed rename/copy record from git diff")
                old_path, path = tokens[index], tokens[index + 1]
                index += 2
                statuses.append(_FileStatus(status=status, path=path, old_path=old_path))
            else:
                if index >= len(tokens):
                    raise GitDiffError("Malformed file record from git diff")
                path = tokens[index]
                index += 1
                statuses.append(_FileStatus(status=status, path=path))
        return statuses

    def _collect_file(self, base_commit: str, head_commit: str, status: _FileStatus) -> ChangedFile:
        paths = [status.path]
        if status.old_path:
            paths.insert(0, status.old_path)
        patch = self._git(
            "diff",
            "--no-ext-diff",
            "--no-color",
            f"--unified={self.context_lines}",
            "--find-renames",
            base_commit,
            head_commit,
            "--",
            *paths,
        )
        hunks, added, deleted, is_binary = parse_unified_patch(patch)
        return ChangedFile(
            path=status.path,
            old_path=status.old_path,
            change_type=_STATUS_TYPES.get(status.status[0], ChangeType.UNKNOWN),
            language=detect_language(status.path),
            patch=patch,
            hunks=hunks,
            added_lines=added,
            deleted_lines=deleted,
            is_binary=is_binary,
        )


def detect_language(path: str) -> str:
    """Infer a useful language label from a file suffix."""
    filename = Path(path).name.lower()
    if filename in {"dockerfile", "containerfile"}:
        return "dockerfile"
    if filename in {"makefile", "gnumakefile"}:
        return "makefile"
    return _LANGUAGES.get(Path(filename).suffix, "text")


def parse_unified_patch(patch: str) -> tuple[list[DiffHunk], int, int, bool]:
    """Parse hunk line numbers while retaining the original patch."""
    is_binary = "GIT binary patch" in patch or "Binary files " in patch
    hunks: list[DiffHunk] = []
    current: DiffHunk | None = None
    old_line = 0
    new_line = 0
    added = 0
    deleted = 0

    for raw_line in patch.splitlines():
        match = _HUNK_HEADER.match(raw_line)
        if match:
            current = DiffHunk(
                header=raw_line,
                old_start=int(match.group("old_start")),
                old_count=int(match.group("old_count") or "1"),
                new_start=int(match.group("new_start")),
                new_count=int(match.group("new_count") or "1"),
                section=match.group("section").strip(),
            )
            hunks.append(current)
            old_line = current.old_start
            new_line = current.new_start
            continue
        if current is None or not raw_line:
            continue
        if raw_line.startswith("\\ No newline at end of file"):
            continue
        prefix, content = raw_line[0], raw_line[1:]
        if prefix == "+":
            current.lines.append(ChangedLine(change_type=LineChangeType.ADDED, content=content, new_line=new_line))
            new_line += 1
            added += 1
        elif prefix == "-":
            current.lines.append(ChangedLine(change_type=LineChangeType.DELETED, content=content, old_line=old_line))
            old_line += 1
            deleted += 1
        elif prefix == " ":
            current.lines.append(
                ChangedLine(
                    change_type=LineChangeType.CONTEXT,
                    content=content,
                    old_line=old_line,
                    new_line=new_line,
                ))
            old_line += 1
            new_line += 1
    return hunks, added, deleted, is_binary
