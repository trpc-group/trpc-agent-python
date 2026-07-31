# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Bounded, fail-safe redaction helpers shared by every safety sink."""

from __future__ import annotations

import hashlib
import re
from dataclasses import fields
from dataclasses import is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from typing import Mapping

from pydantic import BaseModel

REDACTED = "<redacted>"
TRUNCATED = "<truncated>"
UNSUPPORTED = "<unsupported>"
CYCLE = "<cycle>"

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|api[-_]?key|access[-_]?token|"
    r"refresh[-_]?token|token|password|passwd|secret|private[-_]?key|credential)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+\-/=]+", re.IGNORECASE)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
_SECRET_CANARY = re.compile(r"\bTEST_SECRET_DO_NOT_LEAK_[A-Za-z0-9_-]+\b")
_ASSIGNMENT = re.compile(r"(?i)\b(authorization|cookie|api[-_]?key|access[-_]?token|refresh[-_]?token|token|"
                         r"password|passwd|secret|private[-_]?key|credential)\b\s*[:=]\s*([^\s,;]+)")


def sha256_text(value: str) -> str:
    """Return a deterministic identifier; a hash is not encryption."""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def redact_text(value: str, *, max_length: int = 512) -> str:
    """Redact common secret material and enforce a strict output bound."""
    try:
        text = _PRIVATE_KEY.sub(REDACTED, value)
        text = _SECRET_CANARY.sub(REDACTED, text)
        text = _BEARER.sub(REDACTED, text)
        text = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
        if len(text) > max_length:
            text = text[:max_length] + TRUNCATED
        return text
    except Exception:  # pragma: no cover - fail-safe guard
        return REDACTED


def sanitize(
    value: Any,
    *,
    max_depth: int = 6,
    max_items: int = 64,
    max_string: int = 512,
    max_fields: int = 64,
) -> Any:
    """Convert a controlled object graph to redacted JSON-safe values.

    Unknown objects never have their ``repr`` or ``str`` methods invoked.
    """
    seen: set[int] = set()

    def walk(item: Any, depth: int, field_name: str = "") -> Any:
        if _SENSITIVE_KEY.search(field_name):
            return REDACTED
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                return UNSUPPORTED
            return item
        if isinstance(item, str):
            return redact_text(item, max_length=max_string)
        if isinstance(item, bytes):
            return f"<bytes:{len(item)} sha256:{hashlib.sha256(item).hexdigest()}>"
        if isinstance(item, Path):
            return redact_text(item.as_posix(), max_length=max_string)
        if isinstance(item, Enum):
            return walk(item.value, depth, field_name)
        if isinstance(item, BaseException):
            return {"type": type(item).__name__, "message": redact_text("exception redacted", max_length=max_string)}
        if depth >= max_depth:
            return TRUNCATED

        identity = id(item)
        if identity in seen:
            return CYCLE

        if isinstance(item, BaseModel):
            seen.add(identity)
            try:
                dumped = item.model_dump(mode="python", exclude_none=True)
                return walk(dumped, depth + 1, field_name)
            except Exception:
                return REDACTED
            finally:
                seen.discard(identity)

        if is_dataclass(item) and not isinstance(item, type):
            seen.add(identity)
            try:
                result: dict[str, Any] = {}
                for field in fields(item)[:max_fields]:
                    try:
                        result[field.name] = walk(getattr(item, field.name), depth + 1, field.name)
                    except Exception:
                        result[field.name] = REDACTED
                return result
            finally:
                seen.discard(identity)

        if isinstance(item, Mapping):
            seen.add(identity)
            try:
                result = {}
                for index, (key, child) in enumerate(item.items()):
                    if index >= min(max_items, max_fields):
                        result[TRUNCATED] = True
                        break
                    if isinstance(key, (str, int, float, bool, Enum)):
                        key_text = redact_text(str(key), max_length=128)
                    else:
                        key_text = UNSUPPORTED
                    result[key_text] = walk(child, depth + 1, key_text)
                return result
            except Exception:
                return REDACTED
            finally:
                seen.discard(identity)

        if isinstance(item, (list, tuple, set, frozenset)):
            seen.add(identity)
            try:
                values = list(item) if not isinstance(item,
                                                      (set, frozenset)) else sorted(item,
                                                                                    key=lambda _: type(_).__name__)
                result = [walk(child, depth + 1, field_name) for child in values[:max_items]]
                if len(values) > max_items:
                    result.append(TRUNCATED)
                return result
            except Exception:
                return REDACTED
            finally:
                seen.discard(identity)

        return UNSUPPORTED

    try:
        return walk(value, 0)
    except Exception:  # pragma: no cover - final fail-safe
        return REDACTED
