"""Normalize a diff, git worktree, or fixture into a ReviewInput."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from .models import ChangedFile, ChangedLine, ReviewHunk, ReviewInput

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _safe_path(path: Path, roots: list[Path] | None) -> Path:
    resolved = path.expanduser().resolve()
    if roots and not any(resolved == root.resolve() or resolved.is_relative_to(root.resolve()) for root in roots):
        raise ValueError(f"path is outside the allowed roots: {path}")
    return resolved


def _read_text(path: Path, max_input_bytes: int) -> str:
    data = path.read_bytes()
    if len(data) > max_input_bytes:
        raise ValueError(f"review input exceeds {max_input_bytes} bytes")
    if b"\0" in data:
        raise ValueError("binary review input is not supported")
    return data.decode("utf-8")


def _clean_diff_path(raw: str) -> str:
    value = raw.split("\t", 1)[0].strip()
    if value == "/dev/null":
        return value
    if value.startswith(("a/", "b/")):
        value = value[2:]
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe path in diff: {raw}")
    return candidate.as_posix()


def _parse_diff(text: str) -> list[ChangedFile]:
    if "GIT binary patch" in text or re.search(r"(?m)^Binary files .+ differ$", text):
        raise ValueError("binary diff is not supported")
    files: list[ChangedFile] = []
    current: ChangedFile | None = None
    hunk: ReviewHunk | None = None
    new_line = 0
    lines = text.splitlines()
    for raw in lines:
        if raw.startswith("diff --git "):
            parts = raw.split()
            if len(parts) < 4:
                raise ValueError("malformed diff header")
            old_path, new_path = _clean_diff_path(parts[2]), _clean_diff_path(parts[3])
            current = ChangedFile(path=new_path, old_path=old_path)
            files.append(current)
            hunk = None
        elif raw.startswith("--- "):
            old_path = _clean_diff_path(raw[4:])
            if current is None:
                current = ChangedFile(path="", old_path=old_path)
                files.append(current)
            current.old_path = None if old_path == "/dev/null" else old_path
            current.is_new = old_path == "/dev/null"
        elif raw.startswith("+++ "):
            new_path = _clean_diff_path(raw[4:])
            if current is None:
                current = ChangedFile(path=new_path)
                files.append(current)
            current.is_deleted = new_path == "/dev/null"
            if not current.is_deleted:
                current.path = new_path
        elif raw.startswith("@@ "):
            if current is None:
                raise ValueError("hunk encountered before file header")
            match = _HUNK_RE.match(raw)
            if not match:
                raise ValueError(f"malformed hunk header: {raw}")
            old_start, old_count, new_start, new_count = (
                int(match.group(1)),
                int(match.group(2) or 1),
                int(match.group(3)),
                int(match.group(4) or 1),
            )
            hunk = ReviewHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                header=raw,
            )
            current.hunks.append(hunk)
            new_line = new_start
        elif hunk is not None:
            hunk.lines.append(raw)
            if raw.startswith("+") and not raw.startswith("+++"):
                current.candidate_lines.append(ChangedLine(number=new_line, content=raw[1:]))
                new_line += 1
            elif raw.startswith("-") and not raw.startswith("---"):
                continue
            elif not raw.startswith("\\"):
                new_line += 1
    return [item for item in files if item.path and item.path != "/dev/null"]


def parse_review_input(
    *,
    diff_file: str | Path | None = None,
    repo_path: str | Path | None = None,
    fixture_path: str | Path | None = None,
    file_list: str | Path | None = None,
    max_input_bytes: int = 2_000_000,
    allowed_roots: list[str | Path] | None = None,
) -> ReviewInput:
    """Parse exactly one input source. Empty diffs are valid."""
    supplied = [
        diff_file is not None,
        repo_path is not None,
        fixture_path is not None,
        file_list is not None,
    ]
    if sum(supplied) != 1:
        raise ValueError(
            "exactly one of diff_file, repo_path, fixture_path, or file_list is required"
        )
    roots = [Path(item) for item in allowed_roots] if allowed_roots else None
    source_type: str
    source: Path
    if file_list is not None:
        source_type, source = "file_list", _safe_path(Path(file_list), roots)
        if not source.is_file():
            raise ValueError(f"file list is not a file: {source}")
        manifest = _read_text(source, max_input_bytes)
        project_root = source.parent.resolve()
        files: list[ChangedFile] = []
        total = len(manifest.encode())
        digest_builder = hashlib.sha256(manifest.encode())
        for raw in manifest.splitlines():
            value = raw.strip()
            if not value or value.startswith("#"):
                continue
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe path in file list: {value}")
            path = (project_root / relative).resolve()
            if not path.is_relative_to(project_root):
                raise ValueError(f"unsafe path in file list: {value}")
            content = _read_text(path, max_input_bytes)
            total += len(content.encode())
            if total > max_input_bytes:
                raise ValueError(f"review input exceeds {max_input_bytes} bytes")
            digest_builder.update(relative.as_posix().encode())
            digest_builder.update(b"\0")
            digest_builder.update(content.encode())
            changed = ChangedFile(path=relative.as_posix(), is_new=True)
            changed.candidate_lines = [
                ChangedLine(number=index, content=line)
                for index, line in enumerate(content.splitlines(), 1)
            ]
            files.append(changed)
        candidates = {
            item.path: [line.number for line in item.candidate_lines] for item in files
        }
        changed_lines = sum(len(item.candidate_lines) for item in files)
        return ReviewInput(
            files=files,
            context="",
            candidate_lines=candidates,
            digest=digest_builder.hexdigest(),
            summary=f"{len(files)} listed file(s), {changed_lines} candidate line(s)",
            source_type=source_type,
            source_path=str(project_root),
        )
    if repo_path is not None:
        source_type, source = "repo", _safe_path(Path(repo_path), roots)
        if not source.is_dir():
            raise ValueError(f"repository path is not a directory: {source}")
        result = subprocess.run(
            ["git", "-C", str(source), "diff", "--no-ext-diff", "HEAD"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise ValueError(result.stderr.decode("utf-8", "replace").strip() or "unable to read git diff")
        if len(result.stdout) > max_input_bytes:
            raise ValueError(f"review input exceeds {max_input_bytes} bytes")
        if b"\0" in result.stdout:
            raise ValueError("binary diff is not supported")
        text = result.stdout.decode("utf-8")
    else:
        source_type = "fixture" if fixture_path is not None else "diff"
        source = _safe_path(Path(fixture_path or diff_file), roots)
        if source.is_dir() and fixture_path is not None:
            candidates = [source / "change.diff", source / "input.diff"]
            source = next((item for item in candidates if item.is_file()), source)
        if not source.is_file():
            raise ValueError(f"review input is not a file: {source}")
        text = _read_text(source, max_input_bytes)
    files = _parse_diff(text)
    if text.strip() and not files:
        raise ValueError("review input is not a valid unified diff")
    digest = hashlib.sha256(text.encode()).hexdigest()
    candidates = {item.path: [line.number for line in item.candidate_lines] for item in files}
    changed_lines = sum(len(item.candidate_lines) for item in files)
    summary = f"{len(files)} changed file(s), {changed_lines} added line(s)"
    return ReviewInput(
        files=files,
        context=text,
        candidate_lines=candidates,
        digest=digest,
        summary=summary,
        source_type=source_type,
        source_path=str(source),
    )
