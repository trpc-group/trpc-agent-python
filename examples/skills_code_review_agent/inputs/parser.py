"""Normalize diff files, fixtures, file lists, and Git worktrees."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from types import ModuleType

from reports.models import ReviewInputSummary
from security import redact_text
from security import is_likely_secret_path

from .models import ParsedReviewInput

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = EXAMPLE_ROOT / "tests" / "fixtures"
DIFF_PARSER_PATH = (
    EXAMPLE_ROOT / "skills" / "code-review" / "scripts" / "parse_unified_diff.py"
)
MAX_INPUT_BYTES = 5 * 1024 * 1024
FIXTURE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@lru_cache(maxsize=1)
def _diff_parser_module() -> ModuleType:
    # Host normalization and sandbox review intentionally share one parser.
    spec = importlib.util.spec_from_file_location("code_review_diff_parser", DIFF_PARSER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load diff parser: {DIFF_PARSER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_limited(path: Path) -> str:
    with path.open("rb") as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes: {path}")
    return data.decode("utf-8", errors="replace")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _from_diff(path: Path, kind: str, source: str) -> ParsedReviewInput:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Diff input does not exist: {path}")
    diff_text = _read_limited(path)
    # Mount only a staged copy, never the source file's potentially sensitive parent.
    staged_root = Path(tempfile.mkdtemp(prefix="code-review-input-"))
    staged_path = staged_root / path.name
    try:
        shutil.copyfile(path, staged_path)
        # Docker uses the caller's UID/GID. Root callers are remapped to the
        # image's unprivileged review user so the staged copy stays private.
        if getattr(os, "geteuid", lambda: -1)() == 0:
            os.chown(staged_path, 65532, 65532)
            os.chown(staged_root, 65532, 65532)
        staged_path.chmod(0o400)
        staged_root.chmod(0o500)
        parsed = parse_diff_text(
            diff_text,
            kind=kind,
            source=source,
            input_root=staged_root,
        )
        parsed.temporary_input_root = staged_root
        return parsed
    except Exception:
        staged_root.chmod(0o700)
        shutil.rmtree(staged_root, ignore_errors=True)
        raise


def cleanup_parsed_input(parsed_input: ParsedReviewInput) -> None:
    """Remove a task-local staged input directory, if one was created."""
    root = parsed_input.temporary_input_root
    if root is not None:
        root.chmod(0o700)
        shutil.rmtree(root, ignore_errors=True)


def parse_diff_text(
    diff_text: str,
    *,
    kind: str,
    source: str,
    input_root: Path,
    repository_path: Path | None = None,
) -> ParsedReviewInput:
    """Parse in-memory diff output returned from a governed sandbox run."""
    # Keep raw lines for analysis; only the persistable preview is redacted below.
    parsed = _diff_parser_module().parse_unified_diff(
        diff_text,
        redact_sensitive=False,
    )
    files = parsed["files"]
    summary_data = parsed["summary"]
    names = []
    for item in files:
        path = item["new_path"]
        if not path or path == "/dev/null":
            path = item["old_path"]
        if path and path != "/dev/null" and path not in names:
            names.append(path)
    summary_data["file_count"] = len(names)
    return ParsedReviewInput(
        summary=ReviewInputSummary(
            kind=kind,
            source=source,
            digest=_digest(diff_text),
            files=names,
            redacted_preview=redact_text(diff_text)[:2000],
            **summary_data,
        ),
        files=files,
        diff_text=diff_text,
        input_root=input_root,
        repository_path=repository_path,
    )


def parse_diff_file(path: Path) -> ParsedReviewInput:
    """Parse an explicit unified diff or PR patch file."""
    return _from_diff(path, "diff_file", path.name)


def parse_fixture(name: str) -> ParsedReviewInput:
    """Parse a named test fixture without allowing path traversal."""
    if not FIXTURE_NAME.fullmatch(name):
        raise ValueError(f"Invalid fixture name: {name}")
    path = FIXTURES_ROOT / f"{name}.diff"
    return _from_diff(path, "fixture", name)


def parse_file_list(
    path: Path,
    repository_path: Path | None = None,
) -> ParsedReviewInput:
    """Parse a newline-delimited list of repository-relative paths."""
    if path.is_symlink():
        raise ValueError("File list must not be a symbolic link")
    path = path.resolve()
    if is_likely_secret_path(path.name):
        raise ValueError(f"File list uses a likely secret path: {path.name}")
    content = _read_limited(path)
    files = []
    for raw_line in content.splitlines():
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        candidate = Path(value)
        if (
            len(value) > 1024
            or any(ord(character) < 32 for character in value)
            or candidate.is_absolute()
            or ".." in candidate.parts
        ):
            raise ValueError(f"File list contains unsafe path: {value}")
        if is_likely_secret_path(candidate.as_posix()):
            raise ValueError(f"File list contains a likely secret path: {value}")
        files.append(candidate.as_posix())
    if len(files) > 1000:
        raise ValueError("File list exceeds 1000 entries")
    input_root = path.parent
    source = path.name
    resolved_repository = None
    if repository_path is not None:
        resolved_repository = repository_path.resolve()
        if not resolved_repository.is_dir() or not (resolved_repository / ".git").exists():
            raise ValueError(f"Not a Git worktree: {resolved_repository}")
        try:
            source = path.relative_to(resolved_repository).as_posix()
        except ValueError as error:
            raise ValueError("File list must be located inside the repository") from error
        input_root = resolved_repository

    return ParsedReviewInput(
        summary=ReviewInputSummary(
            kind="file_list",
            source=source,
            digest=_digest(content),
            file_count=len(files),
            files=files,
            redacted_preview="\n".join(files[:100]),
        ),
        input_root=input_root,
        repository_path=resolved_repository,
    )


def parse_git_worktree(path: Path) -> ParsedReviewInput:
    """Validate a Git worktree without executing repository code on the host."""
    path = path.resolve()
    if not path.is_dir() or not (path / ".git").exists():
        raise ValueError(f"Not a Git worktree: {path}")
    return ParsedReviewInput(
        summary=ReviewInputSummary(
            kind="git_worktree",
            source=str(path),
            digest="pending-sandbox-diff",
        ),
        input_root=path,
        repository_path=path,
    )
