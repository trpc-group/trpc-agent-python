# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared extraction helpers for script-like tool safety inputs."""

from __future__ import annotations

import shlex
from typing import Any

ScanEntry = tuple[str, str, list[str]]

_COMMAND_KEYS = ("command", "cmd")
_CODE_KEYS = ("script", "code")
_LANGUAGE_CODE_KEYS = (
    ("python_code", "python"),
    ("bash_code", "bash"),
    ("bash", "bash"),
)
_SCRIPT_LIKE_KEYS = (
    "python_code",
    "bash_code",
    "bash",
    "command",
    "cmd",
    "script",
    "code",
    "code_blocks",
)


def extract_scan_entries(payload: Any, default_language: str | None = None) -> list[ScanEntry]:
    """Extract script and argv scan entries from dict-like or object-like payloads."""
    language = default_language or "unknown"
    entries: list[ScanEntry] = []
    for candidate in _iter_payloads(payload):
        entries.extend(_entries_from_payload(candidate, language))
    return _dedupe_entries(entries)


def extract_call_scan_entries(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    default_language: str | None = None,
) -> list[ScanEntry]:
    """Extract scan entries from callable positional and keyword inputs."""
    language = default_language or "unknown"
    entries = extract_scan_entries(kwargs, language)

    if args and isinstance(args[0], str):
        command_args = extract_command_args(kwargs)
        positional_command_args = _coerce_command_args(args[1]) if len(args) > 1 else []
        entries.append((args[0], language, command_args or positional_command_args))

    for arg in args:
        if isinstance(arg, (dict, list, tuple)):
            entries.extend(extract_scan_entries(arg, language))

    return _dedupe_entries(entries)


def request_value(req: Any, key: str, default: Any = None) -> Any:
    """Read a key from dict-like or object-like inputs."""
    if isinstance(req, dict):
        return req.get(key, default)
    return getattr(req, key, default)


def extract_command_args(payload: Any) -> list[str]:
    """Extract argv-style command arguments from common tool payload fields."""
    for key in ("command_args", "argv", "args"):
        coerced = _coerce_command_args(request_value(payload, key, None))
        if coerced:
            return coerced
    return []


def _entries_from_payload(payload: Any, default_language: str) -> list[ScanEntry]:
    entries: list[ScanEntry] = []
    command_args = extract_command_args(payload)

    code_blocks = request_value(payload, "code_blocks", None)
    if code_blocks:
        for block in code_blocks:
            code = request_value(block, "code", "")
            language = request_value(block, "language", "unknown") or "unknown"
            if code:
                entries.append((str(code), str(language), []))

    for key, language in _LANGUAGE_CODE_KEYS:
        value = request_value(payload, key, "")
        if value:
            entries.append((str(value), language, []))

    for key in _COMMAND_KEYS:
        value = request_value(payload, key, "")
        if value:
            entries.append((str(value), "bash", command_args))

    for key in _CODE_KEYS:
        value = request_value(payload, key, "")
        if value:
            language = request_value(payload, "language", default_language) or default_language
            entries.append((str(value), str(language), command_args))

    if command_args and not _has_script_like_field(payload):
        entries.append(("", default_language if default_language != "unknown" else "bash", command_args))

    return entries


def _has_script_like_field(payload: Any) -> bool:
    return any(request_value(payload, key, "") for key in _SCRIPT_LIKE_KEYS)


def _coerce_command_args(value: Any) -> list[str]:
    if value is None or isinstance(value, dict):
        return []
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError:
            return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _iter_payloads(req: Any):
    seen: set[int] = set()

    def walk(value: Any):
        marker = id(value)
        if marker in seen:
            return
        seen.add(marker)
        yield value
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, (dict, list, tuple)):
                    yield from walk(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                if isinstance(nested, (dict, list, tuple)):
                    yield from walk(nested)

    yield from walk(req)


def _dedupe_entries(entries: list[ScanEntry]) -> list[ScanEntry]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    deduped: list[ScanEntry] = []
    for entry in entries:
        key = (entry[0], entry[1], tuple(entry[2]))
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped
