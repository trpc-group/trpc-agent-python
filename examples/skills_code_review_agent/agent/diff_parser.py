"""Unified diff, fixture, and git working tree input parsing."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .models import ChangedLine
from .models import DiffInput
from .models import Hunk

HUNK_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")
DEFAULT_MAX_DIFF_BYTES = 2_000_000
FILE_LIST_FORBIDDEN_MARKERS = (".env", ".ssh/", "id_rsa", "private_key", ".aws/", "/etc/", "secrets/")


def load_diff(
    *,
    diff_file: Path | None = None,
    patch_file: Path | None = None,
    repo_path: Path | None = None,
    fixture: str | None = None,
    file_list: Path | None = None,
    max_diff_bytes: int = DEFAULT_MAX_DIFF_BYTES,
) -> DiffInput:
    selected = [x is not None for x in (diff_file, patch_file, fixture, file_list)].count(True)
    if selected + (1 if repo_path is not None and file_list is None else 0) != 1:
        raise ValueError(
            "Provide exactly one input source: --diff-file, --patch-file, --repo-path, --file-list, or --fixture. "
            "--repo-path may be combined with --file-list as the file base directory.")
    if diff_file is not None:
        text = sys.stdin.read() if str(diff_file) == "-" else diff_file.read_text(encoding="utf-8")
        return parse_unified_diff(_truncate_diff(text, max_diff_bytes),
                                  source="stdin" if str(diff_file) == "-" else str(diff_file),
                                  max_diff_bytes=max_diff_bytes)
    if patch_file is not None:
        text = patch_file.read_text(encoding="utf-8")
        return parse_unified_diff(_truncate_diff(text, max_diff_bytes),
                                  source=str(patch_file),
                                  max_diff_bytes=max_diff_bytes)
    if fixture is not None:
        fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / f"{fixture}.diff"
        text = fixture_path.read_text(encoding="utf-8")
        return parse_unified_diff(_truncate_diff(text, max_diff_bytes),
                                  source=f"fixture:{fixture}",
                                  max_diff_bytes=max_diff_bytes)
    if file_list is not None:
        return parse_file_list(file_list, repo_path_hint=repo_path or Path.cwd())
    assert repo_path is not None
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--no-ext-diff", "--unified=3"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    diff_text = result.stdout + _untracked_files_diff(repo_path)
    return parse_unified_diff(_truncate_diff(diff_text, max_diff_bytes),
                              source=str(repo_path),
                              max_diff_bytes=max_diff_bytes)


def parse_file_list(file_list: Path, *, repo_path_hint: Path | None = None) -> DiffInput:
    raw_files = [line.strip() for line in file_list.read_text(encoding="utf-8").splitlines() if line.strip()]
    base = (repo_path_hint or file_list.parent).resolve()
    files = [_safe_relative_input_path(path, base) for path in raw_files]
    hunks: list[Hunk] = []
    added_lines: list[ChangedLine] = []
    diff_lines: list[str] = []
    parse_warnings: list[str] = []

    for path in files:
        diff_lines.extend([f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"])
        file_path = base / path
        if _is_forbidden_input_path(path):
            parse_warnings.append(f"sensitive file-list path was not read before Filter evaluation: {path}")
            hunk = Hunk(file=path, old_start=0, old_count=0, new_start=1, new_count=0, header="@@ -0,0 +1,0 @@")
            hunks.append(hunk)
            diff_lines.append(hunk.header)
        elif file_path.exists() and file_path.is_file():
            source_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            hunk = Hunk(file=path,
                        old_start=0,
                        old_count=0,
                        new_start=1,
                        new_count=len(source_lines),
                        header=f"@@ -0,0 +1,{len(source_lines)} @@")
            diff_lines.append(hunk.header)
            for index, content in enumerate(source_lines, start=1):
                line = ChangedLine(file=path,
                                   old_line=None,
                                   new_line=index,
                                   content=content,
                                   kind="add",
                                   hunk_header=hunk.header)
                hunk.lines.append(line)
                added_lines.append(line)
                diff_lines.append(f"+{content}")
            hunks.append(hunk)
        else:
            hunk = Hunk(file=path, old_start=0, old_count=0, new_start=1, new_count=0, header="@@ -0,0 +1,0 @@")
            hunks.append(hunk)
            diff_lines.append(hunk.header)

    for hunk in hunks:
        for index, line in enumerate(hunk.lines):
            line.context_before = [item.content for item in hunk.lines[max(0, index - 3):index]]
            line.context_after = [item.content for item in hunk.lines[index + 1:index + 6]]

    summary = {
        "file_count": len(files),
        "hunk_count": len(hunks),
        "added_line_count": len(added_lines),
        "deleted_line_count": 0,
        "context_line_count": 0,
        "input_mode": "file_list",
        "line_map": _line_map_summary(hunks),
        "change_type_counts": {
            "file_list": len(files)
        },
        "parse_warning_count": len(parse_warnings),
    }
    return DiffInput(
        source=str(file_list),
        diff_text="\n".join(diff_lines) + "\n",
        files=files,
        hunks=hunks,
        added_lines=added_lines,
        summary=summary,
        file_changes=[{
            "old_path": path,
            "new_path": path,
            "change_type": "file_list",
            "is_binary": False
        } for path in files],
        parse_warnings=parse_warnings,
    )


def parse_unified_diff(diff_text: str,
                       *,
                       source: str = "inline",
                       max_diff_bytes: int = DEFAULT_MAX_DIFF_BYTES) -> DiffInput:
    files: list[str] = []
    hunks: list[Hunk] = []
    file_changes: list[dict[str, object]] = []
    parse_warnings: list[str] = []
    current_file = ""
    current_change: dict[str, object] | None = None
    current_hunk: Hunk | None = None
    old_line = 0
    new_line = 0

    if len(diff_text.encode("utf-8")) >= max_diff_bytes:
        parse_warnings.append(f"diff input reached max_diff_bytes={max_diff_bytes}; parsing truncated content")

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            current_hunk = None
            current_change = _new_change_from_header(raw_line)
            file_changes.append(current_change)
            current_file = str(current_change["new_path"])
            if current_file != "/dev/null" and current_file not in files:
                files.append(current_file)
            continue
        if raw_line.startswith("new file mode ") and current_change is not None:
            current_change["change_type"] = "added"
            continue
        if raw_line.startswith("deleted file mode ") and current_change is not None:
            current_change["change_type"] = "deleted"
            continue
        if raw_line.startswith("rename from ") and current_change is not None:
            current_change["old_path"] = raw_line.removeprefix("rename from ").strip()
            current_change["change_type"] = "renamed"
            continue
        if raw_line.startswith("rename to ") and current_change is not None:
            current_change["new_path"] = raw_line.removeprefix("rename to ").strip()
            current_file = str(current_change["new_path"])
            if current_file not in files:
                files.append(current_file)
            continue
        if raw_line.startswith("Binary files ") or raw_line.startswith("GIT binary patch"):
            parse_warnings.append(f"binary diff skipped near {current_file or source}")
            if current_change is not None:
                current_change["is_binary"] = True
            continue
        if raw_line.startswith("+++ "):
            current_file = _normalize_file(raw_line[4:])
            if current_change is not None:
                current_change["new_path"] = current_file
                if current_change.get("old_path") == "/dev/null":
                    current_change["change_type"] = "added"
            if current_file != "/dev/null" and current_file not in files:
                files.append(current_file)
            continue
        if raw_line.startswith("--- ") and current_change is not None:
            old_path = _normalize_file(raw_line[4:])
            current_change["old_path"] = old_path
            if old_path != "/dev/null" and current_change.get("new_path") == "/dev/null":
                current_change["change_type"] = "deleted"
            continue
        match = HUNK_RE.match(raw_line)
        if match and current_file:
            old_line = int(match.group("old_start"))
            new_line = int(match.group("new_start"))
            current_hunk = Hunk(
                file=current_file,
                old_start=old_line,
                old_count=int(match.group("old_count") or 1),
                new_start=new_line,
                new_count=int(match.group("new_count") or 1),
                header=raw_line,
            )
            hunks.append(current_hunk)
            continue
        if current_hunk is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            current_hunk.lines.append(
                ChangedLine(
                    file=current_file,
                    old_line=None,
                    new_line=new_line,
                    content=raw_line[1:],
                    kind="add",
                    hunk_header=current_hunk.header,
                ))
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            current_hunk.lines.append(
                ChangedLine(
                    file=current_file,
                    old_line=old_line,
                    new_line=None,
                    content=raw_line[1:],
                    kind="delete",
                    hunk_header=current_hunk.header,
                ))
            old_line += 1
        else:
            content = raw_line[1:] if raw_line.startswith(" ") else raw_line
            current_hunk.lines.append(
                ChangedLine(
                    file=current_file,
                    old_line=old_line,
                    new_line=new_line,
                    content=content,
                    kind="context",
                    hunk_header=current_hunk.header,
                ))
            old_line += 1
            new_line += 1

    for hunk in hunks:
        for index, line in enumerate(hunk.lines):
            if line.kind != "add":
                continue
            line.context_before = [item.content for item in hunk.lines[max(0, index - 3):index]]
            line.context_after = [item.content for item in hunk.lines[index + 1:index + 6]]

    added_lines = [line for hunk in hunks for line in hunk.lines if line.kind == "add"]
    summary = {
        "file_count": len(files),
        "hunk_count": len(hunks),
        "added_line_count": len(added_lines),
        "deleted_line_count": sum(1 for h in hunks for line in h.lines if line.kind == "delete"),
        "context_line_count": sum(1 for h in hunks for line in h.lines if line.kind == "context"),
        "line_map": _line_map_summary(hunks),
        "change_type_counts": _change_type_counts(file_changes),
        "parse_warning_count": len(parse_warnings),
    }
    return DiffInput(
        source=source,
        diff_text=diff_text,
        files=files,
        hunks=hunks,
        added_lines=added_lines,
        summary=summary,
        file_changes=file_changes,
        parse_warnings=parse_warnings,
    )


def _normalize_file(raw: str) -> str:
    path = raw.strip()
    if "\t" in path:
        path = path.split("\t", 1)[0]
    if path == "/dev/null":
        return path
    if path.startswith("b/"):
        return path[2:]
    if path.startswith("a/"):
        return path[2:]
    return path


def _truncate_diff(text: str, max_diff_bytes: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= max_diff_bytes:
        return text
    return data[:max_diff_bytes].decode("utf-8", errors="ignore")


def _untracked_files_diff(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "--others", "--exclude-standard"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return ""
    hunks: list[str] = []
    base = repo_path.resolve()
    for raw_path in result.stdout.splitlines():
        path = raw_path.strip()
        if not path or _is_forbidden_input_path(path):
            continue
        try:
            safe_path = _safe_relative_input_path(path, base)
        except ValueError:
            continue
        file_path = base / safe_path
        if not file_path.is_file():
            continue
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        hunks.extend(_synthetic_added_file_diff(safe_path, lines))
    if not hunks:
        return ""
    return ("\n" if hunks else "") + "\n".join(hunks) + "\n"


def _synthetic_added_file_diff(path: str, lines: list[str]) -> list[str]:
    out = [
        f"diff --git a/{path} b/{path}",
        "new file mode 100644",
        "--- /dev/null",
        f"+++ b/{path}",
        f"@@ -0,0 +1,{len(lines)} @@",
    ]
    out.extend(f"+{line}" for line in lines)
    return out


def _safe_relative_input_path(raw_path: str, base: Path) -> str:
    requested = Path(raw_path)
    if requested.is_absolute():
        raise ValueError(f"file-list path must be relative to the review base: {raw_path}")
    if not raw_path or "\x00" in raw_path:
        raise ValueError("file-list path must not be empty or contain NUL bytes")
    normalized = requested.as_posix()
    if ".." in requested.parts:
        raise ValueError(f"file-list path must not contain '..': {raw_path}")
    candidate = (base / requested).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"file-list path escapes the review base: {raw_path}") from exc
    return normalized


def _is_forbidden_input_path(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    return any(marker in lowered for marker in FILE_LIST_FORBIDDEN_MARKERS)


def _new_change_from_header(line: str) -> dict[str, object]:
    parts = line.split()
    old_path = _normalize_file(parts[2]) if len(parts) > 2 else ""
    new_path = _normalize_file(parts[3]) if len(parts) > 3 else old_path
    return {"old_path": old_path, "new_path": new_path, "change_type": "modified", "is_binary": False}


def _change_type_counts(file_changes: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for change in file_changes:
        key = str(change.get("change_type") or "modified")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _line_map_summary(hunks: list[Hunk]) -> dict[str, list[dict[str, int | None | str]]]:
    result: dict[str, list[dict[str, int | None | str]]] = {}
    for hunk in hunks:
        entries = result.setdefault(hunk.file, [])
        for line in hunk.lines:
            entries.append({"kind": line.kind, "old_line": line.old_line, "new_line": line.new_line})
    return result
