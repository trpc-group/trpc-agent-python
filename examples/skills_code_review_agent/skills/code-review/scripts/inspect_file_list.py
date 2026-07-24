#!/usr/bin/env python3
"""Validate and emit a newline-delimited repository-relative file list."""

import argparse
import json
import re
import sys
from pathlib import Path

MAX_LIST_BYTES = 5 * 1024 * 1024
MAX_PATHS = 1000
MAX_PATH_CHARS = 1024
MAX_PAGE_SIZE = 12
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


def is_likely_secret_path(value: str) -> bool:
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


def parse_file_list(path: Path) -> list[str]:
    """Return safe relative paths from a file-list input."""
    if path.is_symlink():
        raise ValueError("file list must not be a symbolic link")
    with path.open("rb") as source:
        data = source.read(MAX_LIST_BYTES + 1)
    if len(data) > MAX_LIST_BYTES:
        raise ValueError(f"file list exceeds {MAX_LIST_BYTES} bytes")
    files = []
    for raw_line in data.decode("utf-8", errors="replace").splitlines():
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        candidate = Path(value)
        if (
            len(value) > MAX_PATH_CHARS
            or any(ord(character) < 32 for character in value)
            or candidate.is_absolute()
            or ".." in candidate.parts
        ):
            raise ValueError(f"unsafe path: {value}")
        normalized = candidate.as_posix()
        if is_likely_secret_path(normalized):
            raise ValueError(f"likely secret path: {value}")
        files.append(normalized)
        if len(files) > MAX_PATHS:
            raise ValueError(f"file list exceeds {MAX_PATHS} entries")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file_list", type=Path)
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument("--limit", type=int, default=MAX_PAGE_SIZE)
    args = parser.parse_args()
    try:
        if args.cursor < 0 or not 1 <= args.limit <= MAX_PAGE_SIZE:
            raise ValueError("pagination is outside the allowed range")
        files = parse_file_list(args.file_list)
        end = min(len(files), args.cursor + args.limit)
        result = {
            "cursor": args.cursor,
            "next_cursor": end if end < len(files) else None,
            "total_files": len(files),
            "files": files[args.cursor:end],
        }
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
