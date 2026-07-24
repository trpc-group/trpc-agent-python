# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Contract-aware diffing for canonical replay snapshots."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AllowedDiffRule:
    """An explicit allowed backend difference rule."""

    backend_pair: tuple[str, str]
    field_path: str
    comparator: str
    reason: str
    rule_id: str = ""
    still_validate: str = ""


@dataclass
class DiffEntry:
    """One structured replay diff."""

    case_id: str
    backend_pair: tuple[str, str]
    session_id: str | None
    entity_type: str
    entity_id: str | None
    index: int | None
    field_path: str
    reference_value: Any
    actual_value: Any
    allowed: bool
    category: str
    reason: str
    allowed_rule_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_snapshots(
    reference: dict[str, Any],
    actual: dict[str, Any],
    *,
    case_id: str,
    backend_pair: tuple[str, str],
    allowed_diff_rules: list[AllowedDiffRule] | None = None,
) -> list[DiffEntry]:
    """Compare two canonical snapshots and return structured diffs."""

    rules = allowed_diff_rules or []
    validate_allowed_diff_rules(rules)
    diffs: list[DiffEntry] = []
    _compare_value(reference, actual, "$", diffs, case_id, backend_pair, rules)
    diffs.extend(_semantic_diffs(actual, case_id, backend_pair))
    return diffs


def classify_diff(field_path: str, reference_value: Any, actual_value: Any) -> str:
    """Classify a low-level value mismatch into a replay category."""

    if reference_value is _MISSING:
        return "unexpected_entity"
    if actual_value is _MISSING:
        if ".summaries" in field_path:
            return "summary_missing"
        return "missing_entity"
    if ".events" in field_path and ".event_id" in field_path:
        return "event_order_mismatch"
    if ".state" in field_path:
        return "state_mismatch"
    if ".memory" in field_path:
        return "memory_scope_violation" if "session_key" in field_path else "missing_entity"
    if ".summaries" in field_path:
        if ".session_id" in field_path:
            return "summary_owner_mismatch"
        if ".version" in field_path or ".active" in field_path:
            return "summary_version_mismatch"
        if ".covered_event_ids" in field_path:
            return "summary_coverage_mismatch"
        return "summary_missing"
    if "function_response" in field_path or "function_call" in field_path:
        return "tool_link_mismatch"
    if "timestamp" in field_path:
        return "invalid_timestamp"
    return "content_mismatch"


def validate_allowed_diff_rules(rules: list[AllowedDiffRule]) -> None:
    """Reject stale-prone or under-specified allowed-diff rules."""

    for rule in rules:
        if not rule.reason:
            raise ValueError("allowed_diff rule must include a reason")
        if not rule.field_path.startswith("$"):
            raise ValueError(f"allowed_diff rule must use an absolute field path: {rule.field_path}")
        if "*" in rule.field_path:
            raise ValueError(f"allowed_diff wildcard paths are too broad: {rule.field_path}")
        if rule.field_path in {"$", "$.sessions", "$.memory", "$.summaries"}:
            raise ValueError(f"allowed_diff path is too broad: {rule.field_path}")
        if rule.comparator not in {"exact_path", "prefix"}:
            raise ValueError(f"unsupported allowed_diff comparator: {rule.comparator}")


def unused_allowed_diff_rules(diffs: list[DiffEntry], rules: list[AllowedDiffRule]) -> list[str]:
    """Return configured allowed-diff rule ids that did not match any diff."""

    used_rule_ids = {diff.allowed_rule_id for diff in diffs if diff.allowed_rule_id}
    unused = []
    for idx, rule in enumerate(rules):
        rule_id = rule.rule_id or f"{rule.backend_pair}:{rule.field_path}:{idx}"
        if rule_id not in used_rule_ids:
            unused.append(rule_id)
    return unused


class _Missing:
    pass


_MISSING = _Missing()


def _compare_value(
    reference: Any,
    actual: Any,
    path: str,
    diffs: list[DiffEntry],
    case_id: str,
    backend_pair: tuple[str, str],
    rules: list[AllowedDiffRule],
) -> None:
    if isinstance(reference, dict) and isinstance(actual, dict):
        for key in sorted(set(reference) | set(actual)):
            _compare_value(
                reference.get(key, _MISSING),
                actual.get(key, _MISSING),
                f"{path}.{key}",
                diffs,
                case_id,
                backend_pair,
                rules,
            )
        return
    if isinstance(reference, list) and isinstance(actual, list):
        max_len = max(len(reference), len(actual))
        for idx in range(max_len):
            _compare_value(
                reference[idx] if idx < len(reference) else _MISSING,
                actual[idx] if idx < len(actual) else _MISSING,
                f"{path}[{idx}]",
                diffs,
                case_id,
                backend_pair,
                rules,
            )
        return
    if reference != actual:
        allowed, reason, rule_id = _is_allowed(path, backend_pair, rules)
        diffs.append(
            DiffEntry(
                case_id=case_id,
                backend_pair=backend_pair,
                session_id=_extract_session_id(path, reference, actual),
                entity_type=_entity_type(path),
                entity_id=_extract_entity_id(reference, actual),
                index=_extract_index(path),
                field_path=path,
                reference_value=None if reference is _MISSING else reference,
                actual_value=None if actual is _MISSING else actual,
                allowed=allowed,
                category="allowed_backend_difference" if allowed else classify_diff(path, reference, actual),
                reason=reason,
                allowed_rule_id=rule_id,
            )
        )


def _semantic_diffs(snapshot: dict[str, Any], case_id: str, backend_pair: tuple[str, str]) -> list[DiffEntry]:
    diffs: list[DiffEntry] = []
    for session in snapshot.get("sessions", []):
        seen = set()
        for idx, event in enumerate(session.get("events", [])):
            event_id = event.get("event_id")
            if event_id in seen:
                diffs.append(
                    DiffEntry(
                        case_id=case_id,
                        backend_pair=backend_pair,
                        session_id=session.get("session_id"),
                        entity_type="event",
                        entity_id=event_id,
                        index=idx,
                        field_path=f"$.sessions.{session.get('session_id')}.events[{idx}].event_id",
                        reference_value="unique",
                        actual_value=event_id,
                        allowed=False,
                        category="duplicate_event",
                        reason="event id appears more than once in one session",
                    )
                )
            seen.add(event_id)
    return diffs


def _is_allowed(path: str, backend_pair: tuple[str, str], rules: list[AllowedDiffRule]) -> tuple[bool, str, str]:
    for idx, rule in enumerate(rules):
        if rule.backend_pair != backend_pair:
            continue
        if rule.comparator == "exact_path" and path != rule.field_path:
            continue
        if rule.comparator == "prefix" and not path.startswith(rule.field_path):
            continue
        rule_id = rule.rule_id or f"{rule.backend_pair}:{rule.field_path}:{idx}"
        return True, rule.reason, rule_id
    return False, "", ""


def _entity_type(path: str) -> str:
    if ".summaries" in path:
        return "summary"
    if ".memory" in path:
        return "memory"
    if ".events" in path or ".historical_events" in path:
        return "event"
    if ".state" in path:
        return "state"
    return "snapshot"


def _extract_index(path: str) -> int | None:
    marker = path.rfind("[")
    end = path.rfind("]")
    if marker == -1 or end == -1 or end < marker:
        return None
    try:
        return int(path[marker + 1:end])
    except ValueError:
        return None


def _extract_entity_id(reference: Any, actual: Any) -> str | None:
    for value in (actual, reference):
        if isinstance(value, dict):
            for key in ("event_id", "client_summary_id", "probe_id"):
                if key in value:
                    return str(value[key])
    return None


def _extract_session_id(path: str, reference: Any, actual: Any) -> str | None:
    for value in (actual, reference):
        if isinstance(value, dict) and "session_id" in value:
            return str(value["session_id"])
    return None
