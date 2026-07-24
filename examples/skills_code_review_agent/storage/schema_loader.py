"""Safely load trusted storage schema files bundled with this example."""

import os
import stat
from pathlib import Path

MAX_SCHEMA_BYTES = 256 * 1024


def read_trusted_schema(path: Path, storage_directory: Path, label: str) -> str:
    """Read a bounded regular schema file confined to the storage directory."""
    try:
        metadata = os.lstat(path)
    except FileNotFoundError as error:
        raise ValueError(f"{label} schema file does not exist: {path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} schema path must be a regular file, not a link")
    resolved = path.resolve()
    try:
        resolved.relative_to(storage_directory.resolve())
    except ValueError as error:
        raise ValueError(
            f"{label} schema must be located under the example storage directory"
        ) from error
    if metadata.st_size > MAX_SCHEMA_BYTES:
        raise ValueError(f"{label} schema exceeds {MAX_SCHEMA_BYTES} bytes")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(resolved, flags)
    try:
        data = os.read(descriptor, MAX_SCHEMA_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_SCHEMA_BYTES:
        raise ValueError(f"{label} schema exceeds {MAX_SCHEMA_BYTES} bytes")
    return data.decode("utf-8")
