# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Host-side input parsing: turn any supported input into a review payload.

Supported inputs:
* a unified diff / PR patch file (``--diff-file``);
* a git working tree (``--repo-path``): ``git diff HEAD`` plus untracked
  files are collected via subprocess;
* a plain list of file paths (``--files``): treated as fully-added files.

The unified-diff grammar itself lives in the skill's
``scripts/parse_diff.py`` and is loaded here via importlib so host and
sandbox can never disagree about how a diff is parsed (single source of
truth).

Unparsable or binary files never raise: they are downgraded to "skipped and
recorded" entries so one odd file cannot kill a review task.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "code-review" / "scripts"


def _load_parse_diff():
    spec = importlib.util.spec_from_file_location("cr_parse_diff", _SKILL_SCRIPTS / "parse_diff.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


parse_diff = _load_parse_diff()

_LANG_BY_EXT = {
    ".py": "python",
    ".pyw": "python",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
}


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return _LANG_BY_EXT.get(suffix, "other")


MAX_FILE_BYTES = 1_000_000  # single-file cap for embedded content
MAX_TOTAL_BYTES = 8_000_000  # payload cap across all files


@dataclass
class ParsedInput:
    """Structured result handed to the pipeline."""

    mode: str  # repo | diff_only
    input_type: str  # diff_file | repo_path | files
    input_ref: str
    diff_digest: str
    payload: dict  # review_input.json content (files with embedded post-image)
    file_summaries: list[dict] = field(default_factory=list)  # for the diff_file table
    errors: list[str] = field(default_factory=list)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _summarize(entry: dict, candidate_count: int, skipped: bool, skip_reason: str) -> dict:
    return {
        "path": entry.get("path") or entry.get("old_path") or "",
        "change_type": entry.get("change_type", "modified"),
        "is_binary": bool(entry.get("is_binary")),
        "is_rename": entry.get("change_type") == "renamed",
        "old_path": entry.get("old_path"),
        "hunk_count": len(entry.get("hunks", [])),
        "candidate_line_count": candidate_count,
        "skipped": skipped,
        "skip_reason": skip_reason,
    }


def _collect_repo_context(repo_path: Path) -> dict:
    """Scan the repo for existing test files (bounded walk)."""
    test_files: list[str] = []
    seen = 0
    for path in repo_path.rglob("*.py"):
        seen += 1
        if seen > 20000:  # hard bound on pathological repos
            break
        rel = path.relative_to(repo_path).as_posix()
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py" \
                or "/tests/" in f"/{rel}" or rel.startswith("tests/"):
            test_files.append(rel)
    return {"test_files": test_files[:2000], "has_tests_dir": bool(test_files)}


def _build_payload(parsed: dict, mode: str, task_id: str, repo_path: Optional[Path],
                   errors: list[str]) -> tuple[list[dict], list[dict]]:
    """Turn parse_unified_diff output into payload files + DB summaries."""
    files: list[dict] = []
    summaries: list[dict] = []
    total_bytes = 0

    for entry in parsed["files"]:
        path = entry.get("path") or entry.get("old_path") or ""
        if not path:
            errors.append("file entry without any path, skipped")
            continue
        cand = parse_diff.candidate_lines(entry)

        if entry.get("is_binary") or entry.get("change_type") == "binary":
            summaries.append(_summarize(entry, 0, True, "binary file"))
            files.append({
                "path": path,
                "change_type": "binary",
                "old_path": entry.get("old_path"),
                "language": "binary",
                "candidate_lines": [],
                "content": None,
                "content_complete": False,
            })
            continue
        if entry.get("change_type") == "deleted":
            summaries.append(_summarize(entry, 0, True, "deleted file"))
            files.append({
                "path": path,
                "change_type": "deleted",
                "old_path": entry.get("old_path"),
                "language": _language(path),
                "candidate_lines": [],
                "content": None,
                "content_complete": False,
            })
            continue

        content: Optional[str] = None
        complete = False
        skip_reason = ""
        if mode == "repo" and repo_path is not None:
            real = repo_path / path
            if real.is_file():
                try:
                    raw = real.read_bytes()
                    if len(raw) > MAX_FILE_BYTES:
                        skip_reason = f"file too large ({len(raw)} bytes), diff-only fallback"
                    else:
                        content = raw.decode("utf-8", errors="replace")
                        complete = True
                except OSError as ex:
                    skip_reason = f"unreadable: {ex}"
            else:
                skip_reason = "missing from repo, diff-only fallback"
        if content is None:
            content, complete = parse_diff.reconstruct_post_image(entry)
            if skip_reason:
                errors.append(f"{path}: {skip_reason}")
        if content is not None:
            total_bytes += len(content)
            if total_bytes > MAX_TOTAL_BYTES:
                summaries.append(_summarize(entry, len(cand), True, "payload budget exceeded"))
                errors.append(f"{path}: dropped from payload, total size budget exceeded")
                continue

        summaries.append(_summarize(entry, len(cand), content is None, skip_reason if content is None else ""))
        files.append({
            "path": path,
            "change_type": entry.get("change_type", "modified"),
            "old_path": entry.get("old_path"),
            "language": _language(path),
            "candidate_lines": cand,
            "content": content,
            "content_complete": complete,
        })
    return files, summaries


def parse_diff_file(diff_path: str, task_id: str = "", repo_path: Optional[str] = None) -> ParsedInput:
    """Parse a unified diff file; if repo_path is also given, use repo mode."""
    raw = Path(diff_path).read_text(encoding="utf-8", errors="replace")
    return parse_diff_text(raw, task_id=task_id, repo_path=repo_path, input_type="diff_file", input_ref=str(diff_path))


def parse_diff_text(diff_text: str,
                    task_id: str = "",
                    repo_path: Optional[str] = None,
                    input_type: str = "diff_file",
                    input_ref: str = "<text>") -> ParsedInput:
    """Parse unified diff text into a ParsedInput."""
    parsed = parse_diff.parse_unified_diff(diff_text)
    errors = list(parsed.get("errors") or [])
    mode = "repo" if repo_path else "diff_only"
    repo = Path(repo_path).resolve() if repo_path else None
    files, summaries = _build_payload(parsed, mode, task_id, repo, errors)
    payload = {
        "version": 1,
        "mode": mode,
        "task_id": task_id,
        "files": files,
        "parse_errors": errors,
    }
    if repo is not None:
        payload["repo_context"] = _collect_repo_context(repo)
    return ParsedInput(mode=mode,
                       input_type=input_type,
                       input_ref=input_ref,
                       diff_digest=_digest(diff_text),
                       payload=payload,
                       file_summaries=summaries,
                       errors=errors)


def parse_repo_workspace(repo_path: str, task_id: str = "") -> ParsedInput:
    """Collect working-tree changes (vs HEAD) of a git repository."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise FileNotFoundError(f"repo path not found: {repo}")

    def _git(*args: str) -> str:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
        return proc.stdout

    diff_text = _git("diff", "HEAD", "--no-color")
    # untracked files are part of the working-tree change set
    untracked = [line for line in _git("ls-files", "--others", "--exclude-standard").splitlines() if line.strip()]
    extra_chunks: list[str] = []
    for rel in untracked:
        real = repo / rel
        try:
            raw = real.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8000]:
            extra_chunks.append(f"diff --git a/{rel} b/{rel}\nBinary files a/{rel} and b/{rel} differ\n")
            continue
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        body = "".join(f"+{line}\n" for line in lines)
        extra_chunks.append(f"diff --git a/{rel} b/{rel}\n"
                            f"new file mode 100644\n"
                            f"--- /dev/null\n"
                            f"+++ b/{rel}\n"
                            f"@@ -0,0 +1,{max(len(lines), 1)} @@\n{body}")
    full_diff = diff_text + "".join(extra_chunks)
    return parse_diff_text(full_diff, task_id=task_id, repo_path=str(repo), input_type="repo_path", input_ref=str(repo))


def parse_file_list(paths: list[str], task_id: str = "") -> ParsedInput:
    """Treat a plain list of files as fully-added changes (repo-quality text)."""
    chunks: list[str] = []
    for path_str in paths:
        path = Path(path_str)
        try:
            raw = path.read_bytes()
        except OSError:
            # header-only entry: parses to a hunk-less file, recorded as skipped
            chunks.append(f"diff --git a/{path} b/{path}\n")
            continue
        if b"\x00" in raw[:8000]:
            chunks.append(f"diff --git a/{path} b/{path}\nBinary files a/{path} and b/{path} differ\n")
            continue
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        body = "".join(f"+{line}\n" for line in lines)
        chunks.append(f"diff --git a/{path} b/{path}\n"
                      f"new file mode 100644\n"
                      f"--- /dev/null\n"
                      f"+++ b/{path}\n"
                      f"@@ -0,0 +1,{max(len(lines), 1)} @@\n{body}")
    return parse_diff_text("".join(chunks), task_id=task_id, input_type="files", input_ref=",".join(paths)[:500])
