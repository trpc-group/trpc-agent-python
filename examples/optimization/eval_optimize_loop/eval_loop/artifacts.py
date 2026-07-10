"""Cross-platform validation for values used as artifact path components."""

from __future__ import annotations

import re
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

    if (
        not isinstance(value, str)
        or value in {"", ".", ".."}
        or len(value) > MAX_ARTIFACT_COMPONENT_LENGTH
        or value.endswith((".", " "))
        or not ARTIFACT_COMPONENT_RE.fullmatch(value)
    ):
        raise ValueError(f"unsafe {context}: {value!r}")
    windows_stem = value.split(".", 1)[0].upper()
    if windows_stem in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"unsafe {context}: {value!r} is reserved on Windows")
    return value
