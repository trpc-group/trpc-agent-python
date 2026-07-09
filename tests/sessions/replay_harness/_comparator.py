#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Comparator for replay consistency testing.

Compares two ``NormalizedResult`` objects and produces a list of
``DiffEntry`` items pinpointing every discrepancy with field-level
precision.
"""

from __future__ import annotations

from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import Field

from ._normalizer import NormalizedResult


class DiffEntry(BaseModel):
    """A single discrepancy between two backend results."""

    backend_pair: tuple[str, str]
    """The two backends being compared, e.g. ``("in_memory", "sql")``."""

    category: str
    """Comparison category: ``"events"``, ``"state"``, ``"memory"``, ``"summary"``."""

    session_id: str = ""
    """The session in which the diff was found."""

    event_index: Optional[int] = None
    """Zero-based index within the events list (if applicable)."""

    summary_id: Optional[str] = None
    """The ``session_id`` of the summary (if applicable)."""

    field_path: str = ""
    """Dotted path to the differing field, e.g. ``"events[2].function_calls[0].name"``."""

    value_a: Any = None
    """Value from backend A."""

    value_b: Any = None
    """Value from backend B."""

    allowed: bool = False
    """Whether this diff is covered by an allow-rule."""


class AllowedDiffRule(BaseModel):
    """A single rule that suppresses a known-acceptable difference."""

    field_path_pattern: str
    """Glob-like pattern, e.g. ``"events[*].function_calls[*].args"``."""

    reason: str
    """Human-readable justification."""

    backend_pairs: list[tuple[str, str]] = Field(default_factory=list)
    """Backend pairs this rule applies to.  Empty means all pairs."""


class AllowedDiff(BaseModel):
    """Collection of allow-rules for known backend differences."""

    rules: list[AllowedDiffRule] = Field(default_factory=list)


# ── internal helpers ───────────────────────────────────────────────────


def _matches_pattern(field_path: str, pattern: str) -> bool:
    """Check whether *field_path* matches a pattern with ``[*]`` index wildcards."""
    import re

    escaped = re.escape(pattern)
    escaped = escaped.replace(r"\[\*\]", r"\[\d+\]")
    return bool(re.match("^" + escaped + "$", field_path))


def _is_allowed(entry: DiffEntry, allowed: AllowedDiff) -> bool:
    """Return ``True`` if *entry* is covered by any allow-rule."""
    for rule in allowed.rules:
        if rule.backend_pairs and entry.backend_pair not in rule.backend_pairs:
            continue
        if _matches_pattern(entry.field_path, rule.field_path_pattern):
            return True
    return False


def _deep_diff(a: Any,
               b: Any,
               prefix: str = "",
               session_id: str = "",
               backend_pair: tuple[str, str] = ("a", "b")) -> list[DiffEntry]:
    """Recursively diff two values, yielding ``DiffEntry`` items."""
    diffs: list[DiffEntry] = []

    if type(a) is not type(b):
        diffs.append(
            DiffEntry(
                backend_pair=backend_pair,
                category="state",
                session_id=session_id,
                field_path=prefix or "(root)",
                value_a=str(a),
                value_b=str(b),
            ))
        return diffs

    if isinstance(a, dict):
        all_keys = set(a) | set(b)
        for key in sorted(all_keys):
            child_path = f"{prefix}.{key}" if prefix else key
            va = a.get(key, _MISSING)
            vb = b.get(key, _MISSING)
            if va is _MISSING:
                diffs.append(
                    DiffEntry(
                        backend_pair=backend_pair,
                        category="state",
                        session_id=session_id,
                        field_path=child_path,
                        value_a="<missing>",
                        value_b=vb,
                    ))
            elif vb is _MISSING:
                diffs.append(
                    DiffEntry(
                        backend_pair=backend_pair,
                        category="state",
                        session_id=session_id,
                        field_path=child_path,
                        value_a=va,
                        value_b="<missing>",
                    ))
            else:
                diffs.extend(_deep_diff(va, vb, child_path, session_id, backend_pair))
    elif isinstance(a, list):
        max_len = max(len(a), len(b))
        for i in range(max_len):
            child_path = f"{prefix}[{i}]"
            va = a[i] if i < len(a) else _MISSING
            vb = b[i] if i < len(b) else _MISSING
            if va is _MISSING or vb is _MISSING:
                diffs.append(
                    DiffEntry(
                        backend_pair=backend_pair,
                        category="state",
                        session_id=session_id,
                        field_path=child_path,
                        value_a=va if va is not _MISSING else "<missing>",
                        value_b=vb if vb is not _MISSING else "<missing>",
                    ))
            else:
                diffs.extend(_deep_diff(va, vb, child_path, session_id, backend_pair))
    elif a != b:
        diffs.append(
            DiffEntry(
                backend_pair=backend_pair,
                category="state",
                session_id=session_id,
                field_path=prefix,
                value_a=a,
                value_b=b,
            ))

    return diffs


class _MISSING:
    """Sentinel for missing keys."""

    pass


# ── per-category diff functions ────────────────────────────────────────


def _diff_events(
    events_a: list[dict],
    events_b: list[dict],
    session_id: str = "",
    backend_pair: tuple[str, str] = ("a", "b"),
) -> list[DiffEntry]:
    """Compare two normalized event lists pairwise by index."""
    diffs: list[DiffEntry] = []

    max_len = max(len(events_a), len(events_b))
    if len(events_a) != len(events_b):
        diffs.append(
            DiffEntry(
                backend_pair=backend_pair,
                category="events",
                session_id=session_id,
                field_path="events.length",
                value_a=len(events_a),
                value_b=len(events_b),
            ))

    scalar_fields = [
        "author",
        "text",
        "partial",
        "visible",
        "error_code",
        "error_message",
    ]

    for i in range(max_len):
        ea = events_a[i] if i < len(events_a) else {}
        eb = events_b[i] if i < len(events_b) else {}

        if not ea or not eb:
            diffs.append(
                DiffEntry(
                    backend_pair=backend_pair,
                    category="events",
                    session_id=session_id,
                    event_index=i,
                    field_path=f"events[{i}]",
                    value_a="<missing>" if not ea else "<present>",
                    value_b="<missing>" if not eb else "<present>",
                ))
            continue

        for field in scalar_fields:
            va = ea.get(field)
            vb = eb.get(field)
            if va != vb:
                diffs.append(
                    DiffEntry(
                        backend_pair=backend_pair,
                        category="events",
                        session_id=session_id,
                        event_index=i,
                        field_path=f"events[{i}].{field}",
                        value_a=va,
                        value_b=vb,
                    ))

        fa = ea.get("function_calls") or []
        fb = eb.get("function_calls") or []
        if fa != fb:
            diffs.append(
                DiffEntry(
                    backend_pair=backend_pair,
                    category="events",
                    session_id=session_id,
                    event_index=i,
                    field_path=f"events[{i}].function_calls",
                    value_a=fa,
                    value_b=fb,
                ))

        fra = ea.get("function_responses") or []
        frb = eb.get("function_responses") or []
        if fra != frb:
            diffs.append(
                DiffEntry(
                    backend_pair=backend_pair,
                    category="events",
                    session_id=session_id,
                    event_index=i,
                    field_path=f"events[{i}].function_responses",
                    value_a=fra,
                    value_b=frb,
                ))

        sa = ea.get("state_delta") or {}
        sb = eb.get("state_delta") or {}
        field_prefix = f"events[{i}].state_delta"
        if sa != sb:
            diffs.extend(
                _deep_diff(sa, sb, prefix=field_prefix, session_id=session_id, backend_pair=backend_pair))

    return diffs


def _diff_state(
    state_a: dict,
    state_b: dict,
    session_id: str = "",
    backend_pair: tuple[str, str] = ("a", "b"),
) -> list[DiffEntry]:
    """Deep-diff two state dictionaries."""
    return _deep_diff(state_a, state_b, prefix="state", session_id=session_id, backend_pair=backend_pair)


def _diff_summaries(
    summaries_a: list[dict],
    summaries_b: list[dict],
    backend_pair: tuple[str, str] = ("a", "b"),
) -> list[DiffEntry]:
    """Compare two normalized summary lists.

    Summaries are matched by ``session_id``.  Detects:

    * **summary loss** — present in A, absent in B (or vice versa).
    * **summary overwrite** — same session but different ``summary_text``.
    * **wrong session affiliation** — session_id mismatch between entries.
    """
    diffs: list[DiffEntry] = []

    index_a: dict[str, dict] = {s.get("session_id", ""): s for s in summaries_a}
    index_b: dict[str, dict] = {s.get("session_id", ""): s for s in summaries_b}

    all_ids = set(index_a) | set(index_b)

    for sid in sorted(all_ids):
        sa = index_a.get(sid)
        sb = index_b.get(sid)

        if sa is None:
            diffs.append(
                DiffEntry(
                    backend_pair=backend_pair,
                    category="summary",
                    summary_id=sid,
                    field_path=f"summaries[{sid}]",
                    value_a="<missing>",
                    value_b="<present>",
                ))
            continue
        if sb is None:
            diffs.append(
                DiffEntry(
                    backend_pair=backend_pair,
                    category="summary",
                    summary_id=sid,
                    field_path=f"summaries[{sid}]",
                    value_a="<present>",
                    value_b="<missing>",
                ))
            continue

        for field in ["summary_text", "session_id", "original_event_count", "compressed_event_count"]:
            va = sa.get(field)
            vb = sb.get(field)
            if va != vb:
                diffs.append(
                    DiffEntry(
                        backend_pair=backend_pair,
                        category="summary",
                        summary_id=sid,
                        field_path=f"summaries[{sid}].{field}",
                        value_a=va,
                        value_b=vb,
                    ))

    return diffs


def _diff_memory(
    memory_a: list[dict],
    memory_b: list[dict],
    session_id: str = "",
    backend_pair: tuple[str, str] = ("a", "b"),
) -> list[DiffEntry]:
    """Compare two normalized memory entry lists by index."""
    diffs: list[DiffEntry] = []

    max_len = max(len(memory_a), len(memory_b))
    if len(memory_a) != len(memory_b):
        diffs.append(
            DiffEntry(
                backend_pair=backend_pair,
                category="memory",
                session_id=session_id,
                field_path="memory.length",
                value_a=len(memory_a),
                value_b=len(memory_b),
            ))

    for i in range(max_len):
        ma = memory_a[i] if i < len(memory_a) else {}
        mb = memory_b[i] if i < len(memory_b) else {}

        if not ma or not mb:
            diffs.append(
                DiffEntry(
                    backend_pair=backend_pair,
                    category="memory",
                    session_id=session_id,
                    field_path=f"memory[{i}]",
                    value_a="<missing>" if not ma else "<present>",
                    value_b="<missing>" if not mb else "<present>",
                ))
            continue

        for field in ["content_text", "author"]:
            va = ma.get(field)
            vb = mb.get(field)
            if va != vb:
                diffs.append(
                    DiffEntry(
                        backend_pair=backend_pair,
                        category="memory",
                        session_id=session_id,
                        field_path=f"memory[{i}].{field}",
                        value_a=va,
                        value_b=vb,
                    ))

    return diffs


# ── top-level compare ──────────────────────────────────────────────────


def compare_results(
    result_a: NormalizedResult,
    result_b: NormalizedResult,
    session_id: str = "",
    backend_pair: tuple[str, str] = ("a", "b"),
    allowed: Optional[AllowedDiff] = None,
) -> list[DiffEntry]:
    """Compare two normalized backend results and return all diffs.

    Args:
        result_a: Normalized result from backend A.
        result_b: Normalized result from backend B.
        session_id: Session identifier for diff entries.
        backend_pair: Label for the two backends.
        allowed: Optional allow-list of known-acceptable differences.

    Returns:
        List of ``DiffEntry``.  Entries matching an allow-rule have
        ``allowed=True`` and do not count as failures.
    """
    diffs: list[DiffEntry] = []

    diffs.extend(_diff_events(result_a.events, result_b.events, session_id=session_id, backend_pair=backend_pair))
    diffs.extend(_diff_state(result_a.state, result_b.state, session_id=session_id, backend_pair=backend_pair))
    diffs.extend(_diff_summaries(result_a.summaries, result_b.summaries, backend_pair=backend_pair))
    diffs.extend(_diff_memory(result_a.memory_entries, result_b.memory_entries, session_id=session_id,
                              backend_pair=backend_pair))

    if allowed:
        for entry in diffs:
            entry.allowed = _is_allowed(entry, allowed)

    return diffs
