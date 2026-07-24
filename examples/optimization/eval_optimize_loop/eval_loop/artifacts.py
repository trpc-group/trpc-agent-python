"""Cross-platform validation for values used as artifact path components."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ARTIFACT_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
MAX_ARTIFACT_COMPONENT_LENGTH = 128


def validate_artifact_component(value: Any, *, context: str) -> str:
    """Return a portable artifact component or fail before filesystem access."""

    if (not isinstance(value, str) or value in {"", ".", ".."} or len(value) > MAX_ARTIFACT_COMPONENT_LENGTH
            or value.endswith((".", " ")) or not ARTIFACT_COMPONENT_RE.fullmatch(value)):
        raise ValueError(f"unsafe {context}: {value!r}")
    windows_stem = value.split(".", 1)[0].upper()
    if windows_stem in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"unsafe {context}: {value!r} is reserved on Windows")
    return value


def validate_distinct_file_paths(
    paths: Mapping[str, str | Path],
    *,
    context: str,
) -> None:
    """Reject aliases using portable case-folding and physical file identity."""

    resolved_keys: dict[str, str] = {}
    physical_keys: dict[tuple[int, int], str] = {}
    observed_paths: list[tuple[str, Path, bool]] = []
    for label, raw_path in paths.items():
        path = Path(raw_path)
        try:
            resolved = path.resolve(strict=False)
        except OSError as error:
            raise ValueError(f"{context} {label!r} cannot be resolved: {error}") from error

        portable_key = str(resolved).casefold()
        previous_label = resolved_keys.get(portable_key)
        if previous_label is not None:
            raise ValueError(f"{context} must be different physical files; "
                             f"{previous_label!r} and {label!r} collide case-insensitively")

        try:
            metadata = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            metadata = None
        except OSError as error:
            raise ValueError(f"{context} {label!r} is unavailable: {error}") from error

        physical_key = ((int(metadata.st_dev),
                         int(metadata.st_ino)) if metadata is not None and int(metadata.st_ino) != 0 else None)
        if physical_key is not None and physical_key in physical_keys:
            raise ValueError(f"{context} must be different physical files; "
                             f"{physical_keys[physical_key]!r} and {label!r} are aliases")

        for observed_label, observed_path, observed_exists in observed_paths:
            if metadata is None or not observed_exists:
                continue
            try:
                same_file = path.samefile(observed_path)
            except (FileNotFoundError, NotADirectoryError):
                same_file = False
            except OSError as error:
                raise ValueError(f"{context} {label!r} is unavailable: {error}") from error
            if same_file:
                raise ValueError(f"{context} must be different physical files; "
                                 f"{observed_label!r} and {label!r} are aliases")

        resolved_keys[portable_key] = label
        if physical_key is not None:
            physical_keys[physical_key] = label
        observed_paths.append((label, path, metadata is not None))
