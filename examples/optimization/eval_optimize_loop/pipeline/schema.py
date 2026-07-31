"""Shared strict-schema primitives with no pipeline dependencies."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SECRET_ASSIGNMENT = re.compile(r"(?i)(?P<prefix>(?P<key_quote>[\"']?)[A-Za-z0-9_.-]*"
                                r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|authorization|"
                                r"cookie|secret|credential|private[_-]?key|secret[_-]?access[_-]?key)"
                                r"(?P=key_quote)\s*[:=]\s*)"
                                r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|"
                                r"(?:bearer\s+)?[^\s,;}\]]+)")
_BEARER_TOKEN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_SECRET_SUFFIXES = (
    "apikey",
    "token",
    "password",
    "authorization",
    "cookie",
    "secret",
    "credential",
    "accesstoken",
    "refreshtoken",
    "privatekey",
    "secretaccesskey",
)


def parse_strict_json(text: str) -> dict[str, Any]:
    """Parse a JSON object while rejecting duplicates and non-finite values."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key!r}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite JSON number: {value}")
        return parsed

    payload = json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_constant=invalid_constant,
        parse_float=finite_float,
    )
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def add_exception_note(error: BaseException, note: str) -> None:
    """Attach diagnostics without dropping Python 3.10 compatibility."""

    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", ()))
    notes.append(note)
    try:
        error.__notes__ = notes
    except (AttributeError, TypeError):
        pass


def validate_safe_component(value: str, *, name: str = "component") -> str:
    """Validate one portable path component."""

    if not value or value != value.strip() or not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"{name} must be a non-empty portable path component")
    if ".." in value or value.endswith((".", " ")):
        raise ValueError(f"{name} contains an unsafe path component")
    stem = value.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        raise ValueError(f"{name} is reserved on Windows")
    return value


def _is_secret_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return normalized in _SECRET_SUFFIXES or normalized.endswith(_SECRET_SUFFIXES)


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    quote = value[0] if value and value[0] in "\"'" else ""
    return f"{match.group('prefix')}{quote}[REDACTED]{quote}"


def sanitize(value: Any, *, max_text_chars: int | None) -> Any:
    """Recursively redact credentials and optionally bound individual strings."""

    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if _is_secret_key(str(key)) else sanitize(item, max_text_chars=max_text_chars))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, str):
        text = _SECRET_ASSIGNMENT.sub(_redact_assignment, value)
        text = _BEARER_TOKEN.sub(lambda match: match.group(1) + "[REDACTED]", text)
        if max_text_chars is None or len(text) <= max_text_chars:
            return text
        return text[:max_text_chars] + "...[truncated]"
    return value


def validate_secret_free_text(value: str, *, name: str) -> str:
    """Reject prompt content that would be changed by audit redaction."""

    if sanitize(value, max_text_chars=None) != value:
        raise ValueError(f"{name} contains credential-shaped content")
    return value


def sanitized_text(value: Any, *, max_text_chars: int) -> str:
    """Render one bounded text value after structured recursive sanitization."""

    if isinstance(value, BaseException):
        return sanitized_exception_message(value, max_text_chars=max_text_chars)
    if isinstance(value, str) and len(value) > max_text_chars:
        marker = "...[truncated]"
        value = value[:max(0, max_text_chars - len(marker))] + marker
    clean = sanitize(value, max_text_chars=max_text_chars)
    if isinstance(clean, str):
        text = clean
    elif isinstance(clean, (dict, list)):
        text = json.dumps(clean, ensure_ascii=False, sort_keys=True, default=str)
    else:
        text = str(clean)
        text = str(sanitize(text, max_text_chars=max_text_chars))
    return text if len(text) <= max_text_chars else text[:max_text_chars] + "...[truncated]"


def sanitized_exception_message(
    error: BaseException,
    *,
    max_text_chars: int,
    max_parts: int = 8,
) -> str:
    """Render an exception chain with bounded count, text and total size."""

    notes = tuple(getattr(error, "__notes__", ()))
    all_parts = [("primary", str(error))]
    all_parts.extend((f"diagnostic-{index}", str(note)) for index, note in enumerate(notes, start=1))
    omitted = 0
    if len(all_parts) > max_parts:
        keep_notes = max_parts - 1
        first = max(1, keep_notes // 2)
        last = keep_notes - first
        selected = [all_parts[0], *all_parts[1:1 + first]]
        if last:
            selected.extend(all_parts[-last:])
        omitted = len(all_parts) - len(selected)
        all_parts = selected
    omitted_text = f"; {omitted} diagnostics omitted" if omitted else ""
    overhead = sum(len(label) + 2 for label, _ in all_parts) + len(omitted_text)
    part_budget = max(1, (max_text_chars - overhead) // max(1, len(all_parts)))
    rendered: list[str] = []
    for label, raw in all_parts:
        marker = "...[truncated]"
        bounded = raw if len(raw) <= part_budget else raw[:max(0, part_budget - len(marker))] + marker
        clean = sanitize(bounded, max_text_chars=part_budget)
        rendered.append(f"{label}: {clean if isinstance(clean, str) else str(clean)}")
    result = "\n".join(rendered) + omitted_text
    return result[:max_text_chars]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        alias_generator=to_camel,
        arbitrary_types_allowed=True,
    )


def finite_number(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def non_negative_number(value: float, name: str) -> float:
    value = finite_number(value, name)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value
