#!/usr/bin/env python3
"""Enumerate changed or tracked Git files as bounded JSON pages."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

MAX_STATUS_BYTES = 1024 * 1024
MAX_FILES = 10_000
MAX_PATH_CHARS = 1024
MAX_PAGE_SIZE = 12


def _safe_path(data: bytes) -> tuple[str, bool, bool]:
    text = data.decode("utf-8", errors="replace")
    normalized = any(ord(character) < 32 for character in text)
    text = "".join(character if ord(character) >= 32 else "�" for character in text)
    if len(text) <= MAX_PATH_CHARS:
        return text, False, normalized
    return f"{text[: MAX_PATH_CHARS - 1]}…", True, normalized


def collect_files(repository: Path, mode: str) -> list[dict[str, object]]:
    """Run one fixed Git listing command and normalize NUL-separated paths."""
    if mode not in {"changed", "tracked"}:
        raise ValueError(f"unsupported Git file mode: {mode}")
    repository = repository.resolve()
    if not repository.is_dir() or not (repository / ".git").exists():
        raise ValueError(f"not a Git worktree: {repository}")
    if mode == "changed":
        command = [
            "git",
            "-C",
            str(repository),
            "status",
            "--short",
            "-z",
            "--untracked-files=all",
        ]
    else:
        command = ["git", "-C", str(repository), "ls-files", "-z"]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        timeout=20,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace")[:1000]
        raise ValueError(f"Git file listing failed: {message}")
    if len(completed.stdout) > MAX_STATUS_BYTES:
        raise ValueError(f"Git file listing exceeds {MAX_STATUS_BYTES} bytes")

    chunks = [item for item in completed.stdout.split(b"\0") if item]
    records: list[dict[str, object]] = []
    index = 0
    while index < len(chunks):
        raw = chunks[index]
        if mode == "changed":
            if len(raw) < 4:
                raise ValueError("Git status returned a malformed record")
            status = raw[:2].decode("ascii", errors="replace")
            path_bytes = raw[3:]
            # Porcelain -z adds the original path as the next NUL record.
            if "R" in status or "C" in status:
                index += 1
        else:
            status = "tracked"
            path_bytes = raw
        path, truncated, normalized = _safe_path(path_bytes)
        records.append(
            {
                "status": status,
                "path": path,
                "truncated": truncated,
                "normalized": normalized,
            }
        )
        if len(records) > MAX_FILES:
            raise ValueError(f"Git file listing exceeds {MAX_FILES} entries")
        index += 1
    return records


def build_page(
    records: list[dict[str, object]],
    *,
    mode: str,
    cursor: int = 0,
    limit: int = MAX_PAGE_SIZE,
) -> dict[str, object]:
    if cursor < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("pagination is outside the allowed range")
    end = min(len(records), cursor + limit)
    return {
        "mode": mode,
        "cursor": cursor,
        "next_cursor": end if end < len(records) else None,
        "total_files": len(records),
        "records": records[cursor:end],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--mode", choices=("changed", "tracked"), required=True)
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument("--limit", type=int, default=MAX_PAGE_SIZE)
    args = parser.parse_args()
    try:
        records = collect_files(args.repository, args.mode)
        result = build_page(
            records,
            mode=args.mode,
            cursor=args.cursor,
            limit=args.limit,
        )
        result["input_digest"] = hashlib.sha256(
            json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
