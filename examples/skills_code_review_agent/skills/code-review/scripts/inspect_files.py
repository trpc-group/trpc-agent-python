#!/usr/bin/env python3
"""Read listed files safely with per-file and total output limits."""

import argparse
import json
import re
import sys
from pathlib import Path

from inspect_git_files import collect_files

MAX_PAGE_FILES = 3
MAX_FILE_BYTES = 1536
MAX_PATHS = 1000
MAX_DIRECT_PATHS = 12
MAX_PATH_CHARS = 1024
MAX_LIST_BYTES = 5 * 1024 * 1024
SECRET_PATH_TERMS = {
    "credential",
    "credentials",
    "passwd",
    "password",
    "passwords",
    "secret",
    "secrets",
    "token",
    "tokens",
}
SECRET_FILE_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
SOURCE_FILE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".go", ".java", ".js", ".jsx", ".kt",
    ".php", ".py", ".rb", ".rs", ".ts", ".tsx",
}
SECRET_PATTERNS = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*\Z", re.DOTALL),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"), "sk-[REDACTED]"),
    (
        re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9_-]{8,}"),
        "[REDACTED_SERVICE_KEY]",
    ),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "gh_[REDACTED]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "github_pat_[REDACTED]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}\b"), "glpat-[REDACTED]"),
    (
        re.compile(r"\b(?:npm|hf)_[A-Za-z0-9_-]{20,}\b"),
        "[REDACTED_SERVICE_TOKEN]",
    ),
    (re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}\b"), "pypi-[REDACTED]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        "[REDACTED_JWT]",
    ),
    (
        re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/]+:)[^\s@/]+(@)"),
        r"\1[REDACTED]\2",
    ),
    (
        re.compile(
            r"(?i)([\"']?[a-z0-9_.-]*(?:api[_-]?key|access[_-]?token|"
            r"client[_-]?secret|private[_-]?key|authorization|credential|token|"
            r"password|passwd|secret)[a-z0-9_.-]*[\"']?\s*[:=]\s*)"
            r"([\"']?)[^\s,;\"']{4,}\2"
        ),
        r"\1\2[REDACTED]\2",
    ),
    (
        re.compile(
            r"(?i)([\"']?[a-z0-9_.-]*(?:api[_-]?key|access[_-]?token|"
            r"client[_-]?secret|private[_-]?key|authorization|credential|token|"
            r"password|passwd|secret)[a-z0-9_.-]*[\"']?\s*[:=]\s*)"
            r"[\"']?[^\s,;\"']{4,}"
        ),
        r"\1[REDACTED]",
    ),
)


def _redact(value: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _safe_text(value: str) -> str:
    """Keep readable text while preventing JSON expansion from control bytes."""
    return "".join(
        character if character in {"\n", "\t"} or ord(character) >= 32 else "�"
        for character in value
    )


def _is_likely_secret_path(value: str) -> bool:
    parts = [part.lower() for part in value.replace("\\", "/").split("/") if part]
    if not parts:
        return False
    filename = parts[-1]
    if filename == ".env" or filename.startswith(".env."):
        return True
    if filename in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
        return True
    if any(filename.endswith(suffix) for suffix in SECRET_FILE_SUFFIXES):
        return True
    if any(set(re.split(r"[._-]+", part)) & SECRET_PATH_TERMS for part in parts[:-1]):
        return True
    if any(filename.endswith(suffix) for suffix in SOURCE_FILE_SUFFIXES):
        return False
    return bool(set(re.split(r"[._-]+", filename)) & SECRET_PATH_TERMS)


def _safe_candidate(root: Path, relative: str) -> Path:
    """Resolve a regular file without traversing secret paths or symlinks."""
    candidate_path = Path(relative)
    if (
        len(relative) > MAX_PATH_CHARS
        or any(ord(character) < 32 for character in relative)
        or candidate_path.is_absolute()
        or ".." in candidate_path.parts
    ):
        raise ValueError(f"unsafe path: {relative}")
    normalized = candidate_path.as_posix()
    if _is_likely_secret_path(normalized):
        raise ValueError(f"likely secret path: {relative}")
    current = root
    for part in candidate_path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symbolic links are not inspected: {relative}")
    candidate = current.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"path escapes input root: {relative}") from error
    return candidate


def inspect_paths(
    root: Path,
    relative_paths: list[str],
    *,
    cursor: int = 0,
    limit: int = MAX_PAGE_FILES,
    allowed_paths: set[str] | None = None,
) -> dict[str, object]:
    """Read bounded relative paths without following escapes outside root."""
    root = root.resolve()
    results = []
    total_bytes = 0
    if len(relative_paths) > MAX_PATHS:
        raise ValueError(f"file selection exceeds {MAX_PATHS} paths")
    if cursor < 0 or not 1 <= limit <= MAX_PAGE_FILES:
        raise ValueError("pagination is outside the allowed range")
    selected_paths = [
        raw_path.strip()
        for raw_path in relative_paths
        if raw_path.strip() and not raw_path.strip().startswith("#")
    ]
    if allowed_paths is not None:
        outside_scope = [path for path in selected_paths if path not in allowed_paths]
        if outside_scope:
            raise ValueError(
                f"path is outside the selected Git scope: {outside_scope[0]}"
            )
    candidates = [
        _safe_candidate(root, relative)
        for relative in selected_paths
    ]
    end = min(len(selected_paths), cursor + limit)
    for relative, candidate in zip(
        selected_paths[cursor:end],
        candidates[cursor:end],
    ):
        if not candidate.is_file():
            results.append({"path": relative, "error": "not a regular file"})
            continue
        with candidate.open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
        truncated = len(data) > MAX_FILE_BYTES
        data = data[:MAX_FILE_BYTES]
        total_bytes += len(data)
        results.append(
            {
                "path": relative,
                "content": _redact(
                    _safe_text(data.decode("utf-8", errors="replace"))
                ),
                "truncated": truncated,
            }
        )
    return {
        "cursor": cursor,
        "next_cursor": end if end < len(selected_paths) else None,
        "total_files": len(selected_paths),
        "files": results,
        "total_bytes": total_bytes,
    }


def inspect_files(
    root: Path,
    file_list: Path,
    *,
    cursor: int = 0,
    limit: int = MAX_PAGE_FILES,
) -> dict[str, object]:
    """Read safe paths supplied by an existing newline-delimited file list."""
    if file_list.is_symlink():
        raise ValueError("file list must not be a symbolic link")
    with file_list.open("rb") as source:
        data = source.read(MAX_LIST_BYTES + 1)
    if len(data) > MAX_LIST_BYTES:
        raise ValueError(f"file list exceeds {MAX_LIST_BYTES} bytes")
    return inspect_paths(
        root,
        data.decode("utf-8", errors="replace").splitlines(),
        cursor=cursor,
        limit=limit,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("file_list", nargs="?", type=Path)
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="repository-relative path; repeat for a bounded batch",
    )
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument("--limit", type=int, default=MAX_PAGE_FILES)
    parser.add_argument("--scope", choices=("changed", "full"))
    args = parser.parse_args()
    if (args.file_list is None) == (not args.path):
        parser.error("provide either file_list or one or more --path values")
    try:
        if args.path:
            if args.scope is None:
                raise ValueError("direct repository inspection requires --scope")
            if len(args.path) > MAX_DIRECT_PATHS:
                raise ValueError(
                    f"direct selection exceeds {MAX_DIRECT_PATHS} paths"
                )
            mode = "tracked" if args.scope == "full" else "changed"
            allowed_paths = {
                str(item["path"])
                for item in collect_files(args.root, mode)
                if item.get("path")
                and not item.get("truncated")
                and not item.get("normalized")
            }
            result = inspect_paths(
                args.root,
                args.path,
                cursor=args.cursor,
                limit=args.limit,
                allowed_paths=allowed_paths,
            )
        else:
            if args.scope is not None:
                raise ValueError("file-list inspection does not accept --scope")
            result = inspect_files(
                args.root,
                args.file_list,
                cursor=args.cursor,
                limit=args.limit,
            )
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
