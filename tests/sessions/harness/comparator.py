# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Comparator for cross-backend diff analysis of sessions, events, memory, and summaries."""

from __future__ import annotations

from typing import Any

from .allowed_diff import is_allowed_diff
from .normalizer import Normalizer
from .snapshot import BackendSnapshot


class Comparator:
    """Compares two backend snapshots and produces a list of field-level diffs.

    Comparison is performed on three dimensions:
    1. Events: per-session event list content, order, and count.
    2. State: per-session state dictionary values.
    3. Memory: memory entry content and count per key.
    4. Summary: summary text, session ownership, original/compressed event counts.
    """

    def __init__(self):
        self._normalizer = Normalizer()

    def compare(
        self,
        baseline: BackendSnapshot,
        target: BackendSnapshot,
    ) -> list[dict[str, Any]]:
        """Compare two snapshots and return all diffs.

        Args:
            baseline: The reference snapshot (typically InMemory).
            target: The snapshot to compare against the baseline.

        Returns:
            List of diff dicts, each with keys:
                session_id, event_index, summary_id, field_path,
                baseline_value, target_value, allowed, reason
        """
        diffs: list[dict[str, Any]] = []

        diffs.extend(self._compare_sessions(baseline, target))
        diffs.extend(self._compare_memory(baseline, target))
        diffs.extend(self._compare_summaries(baseline, target))

        return diffs

    def _compare_sessions(
        self,
        baseline: BackendSnapshot,
        target: BackendSnapshot,
    ) -> list[dict[str, Any]]:
        """Compare all sessions between two snapshots."""
        diffs: list[dict[str, Any]] = []
        all_session_ids = set(baseline.sessions.keys()) | set(target.sessions.keys())

        for sid in sorted(all_session_ids):
            b_session = baseline.sessions.get(sid)
            t_session = target.sessions.get(sid)

            if b_session is None and t_session is None:
                continue
            if b_session is None:
                diffs.append(self._make_diff(
                    session_id=sid,
                    field_path="session",
                    baseline_value=None,
                    target_value="present",
                    reason="Session missing in baseline",
                ))
                continue
            if t_session is None:
                diffs.append(self._make_diff(
                    session_id=sid,
                    field_path="session",
                    baseline_value="present",
                    target_value=None,
                    reason="Session missing in target",
                ))
                continue

            b_dict = b_session.model_dump()
            t_dict = t_session.model_dump()

            b_dict = self._normalizer.normalize_session(b_dict)
            t_dict = self._normalizer.normalize_session(t_dict)

            b_events = b_dict.get("events", [])
            t_events = t_dict.get("events", [])

            if len(b_events) != len(t_events):
                diffs.append(self._make_diff(
                    session_id=sid,
                    field_path="events.length",
                    baseline_value=len(b_events),
                    target_value=len(t_events),
                    reason="Event count mismatch",
                ))

            max_len = max(len(b_events), len(t_events))
            for i in range(max_len):
                b_ev = b_events[i] if i < len(b_events) else None
                t_ev = t_events[i] if i < len(t_events) else None
                if b_ev is None or t_ev is None:
                    diffs.append(self._make_diff(
                        session_id=sid,
                        event_index=i,
                        field_path=f"events[{i}]",
                        baseline_value="present" if b_ev else None,
                        target_value="present" if t_ev else None,
                        reason="Event presence mismatch",
                    ))
                    continue
                diffs.extend(self._compare_dicts(
                    b_ev, t_ev, session_id=sid, event_index=i, prefix=f"events[{i}]"
                ))

            b_state = b_dict.get("state", {})
            t_state = t_dict.get("state", {})
            all_state_keys = set(b_state.keys()) | set(t_state.keys())
            for key in sorted(all_state_keys):
                b_val = b_state.get(key)
                t_val = t_state.get(key)
                if b_val != t_val:
                    allowed, reason = is_allowed_diff(f"state.{key}")
                    diffs.append(self._make_diff(
                        session_id=sid,
                        field_path=f"state.{key}",
                        baseline_value=b_val,
                        target_value=t_val,
                        allowed=allowed,
                        reason=reason if allowed else "State value mismatch",
                    ))

        return diffs

    def _compare_memory(
        self,
        baseline: BackendSnapshot,
        target: BackendSnapshot,
    ) -> list[dict[str, Any]]:
        """Compare memory entries between two snapshots."""
        diffs: list[dict[str, Any]] = []
        all_keys = set(baseline.memory_entries.keys()) | set(target.memory_entries.keys())

        for key in sorted(all_keys):
            b_entries = baseline.memory_entries.get(key, [])
            t_entries = target.memory_entries.get(key, [])

            if len(b_entries) != len(t_entries):
                diffs.append(self._make_diff(
                    session_id="",
                    field_path=f"memory[{key}].length",
                    baseline_value=len(b_entries),
                    target_value=len(t_entries),
                    reason="Memory entry count mismatch",
                ))

            max_len = max(len(b_entries), len(t_entries))
            for i in range(max_len):
                b_entry = b_entries[i] if i < len(b_entries) else None
                t_entry = t_entries[i] if i < len(t_entries) else None
                if b_entry is None or t_entry is None:
                    diffs.append(self._make_diff(
                        session_id="",
                        field_path=f"memory[{key}][{i}]",
                        baseline_value="present" if b_entry else None,
                        target_value="present" if t_entry else None,
                        reason="Memory entry presence mismatch",
                    ))
                    continue
                b_author = getattr(b_entry, "author", None)
                t_author = getattr(t_entry, "author", None)
                if b_author != t_author:
                    diffs.append(self._make_diff(
                        session_id="",
                        field_path=f"memory[{key}][{i}].author",
                        baseline_value=b_author,
                        target_value=t_author,
                        reason="Memory entry author mismatch",
                    ))

        return diffs

    def _compare_summaries(
        self,
        baseline: BackendSnapshot,
        target: BackendSnapshot,
    ) -> list[dict[str, Any]]:
        """Compare session summaries between two snapshots.

        Detects three critical summary issues:
        1. Summary loss: baseline has a summary but target does not.
        2. Summary overwrite error: new summary should replace old, not coexist.
        3. Summary session ownership error: summary belongs to wrong session.
        """
        diffs: list[dict[str, Any]] = []
        all_ids = set(baseline.summaries.keys()) | set(target.summaries.keys())

        for sid in sorted(all_ids):
            b_summary = baseline.summaries.get(sid)
            t_summary = target.summaries.get(sid)

            if b_summary is not None and t_summary is None:
                diffs.append(self._make_diff(
                    session_id=sid,
                    field_path="summary",
                    baseline_value="present",
                    target_value=None,
                    allowed=False,
                    reason="SUMMARY LOSS: baseline has summary but target does not",
                ))
                continue
            if b_summary is None and t_summary is not None:
                diffs.append(self._make_diff(
                    session_id=sid,
                    field_path="summary",
                    baseline_value=None,
                    target_value="present",
                    allowed=False,
                    reason="SUMMARY LOSS: target has summary but baseline does not",
                ))
                continue

            b_dict = b_summary.model_dump()
            t_dict = t_summary.model_dump()
            b_dict = self._normalizer.normalize_summary(b_dict)
            t_dict = self._normalizer.normalize_summary(t_dict)

            if b_dict.get("session_id") != t_dict.get("session_id"):
                diffs.append(self._make_diff(
                    session_id=sid,
                    field_path="summary.session_id",
                    baseline_value=b_dict.get("session_id"),
                    target_value=t_dict.get("session_id"),
                    allowed=False,
                    reason="SUMMARY OWNERSHIP ERROR: summary belongs to wrong session",
                ))

            if b_dict.get("summary_text") != t_dict.get("summary_text"):
                diffs.append(self._make_diff(
                    session_id=sid,
                    field_path="summary.summary_text",
                    baseline_value=b_dict.get("summary_text"),
                    target_value=t_dict.get("summary_text"),
                    allowed=False,
                    reason="Summary text mismatch",
                ))

            if b_dict.get("original_event_count") != t_dict.get("original_event_count"):
                diffs.append(self._make_diff(
                    session_id=sid,
                    field_path="summary.original_event_count",
                    baseline_value=b_dict.get("original_event_count"),
                    target_value=t_dict.get("original_event_count"),
                    allowed=False,
                    reason="Summary original_event_count mismatch",
                ))

            if b_dict.get("compressed_event_count") != t_dict.get("compressed_event_count"):
                diffs.append(self._make_diff(
                    session_id=sid,
                    field_path="summary.compressed_event_count",
                    baseline_value=b_dict.get("compressed_event_count"),
                    target_value=t_dict.get("compressed_event_count"),
                    allowed=False,
                    reason="Summary compressed_event_count mismatch",
                ))

        return diffs

    def _compare_dicts(
        self,
        baseline: dict[str, Any],
        target: dict[str, Any],
        session_id: str = "",
        event_index: int | None = None,
        prefix: str = "",
    ) -> list[dict[str, Any]]:
        """Recursively compare two dicts and return field-level diffs."""
        diffs: list[dict[str, Any]] = []
        all_keys = set(baseline.keys()) | set(target.keys())

        for key in sorted(all_keys):
            field_path = f"{prefix}.{key}" if prefix else key
            b_val = baseline.get(key)
            t_val = target.get(key)

            if isinstance(b_val, dict) and isinstance(t_val, dict):
                diffs.extend(self._compare_dicts(
                    b_val, t_val, session_id=session_id,
                    event_index=event_index, prefix=field_path,
                ))
            elif isinstance(b_val, list) and isinstance(t_val, list):
                if len(b_val) != len(t_val):
                    diffs.append(self._make_diff(
                        session_id=session_id,
                        event_index=event_index,
                        field_path=f"{field_path}.length",
                        baseline_value=len(b_val),
                        target_value=len(t_val),
                        reason="List length mismatch",
                    ))
                max_len = max(len(b_val), len(t_val))
                for i in range(max_len):
                    bi = b_val[i] if i < len(b_val) else None
                    ti = t_val[i] if i < len(t_val) else None
                    if isinstance(bi, dict) and isinstance(ti, dict):
                        diffs.extend(self._compare_dicts(
                            bi, ti, session_id=session_id,
                            event_index=event_index, prefix=f"{field_path}[{i}]",
                        ))
                    elif bi != ti:
                        allowed, reason = is_allowed_diff(f"{field_path}[{i}]")
                        if not allowed:
                            reason = reason or "Value mismatch"
                        diffs.append(self._make_diff(
                            session_id=session_id,
                            event_index=event_index,
                            field_path=f"{field_path}[{i}]",
                            baseline_value=bi,
                            target_value=ti,
                            allowed=allowed,
                            reason=reason,
                        ))
            elif b_val != t_val:
                allowed, reason = is_allowed_diff(field_path)
                if not allowed:
                    reason = reason or "Value mismatch"
                diffs.append(self._make_diff(
                    session_id=session_id,
                    event_index=event_index,
                    field_path=field_path,
                    baseline_value=b_val,
                    target_value=t_val,
                    allowed=allowed,
                    reason=reason,
                ))

        return diffs

    def check_summary_issues(
        self,
        baseline: BackendSnapshot,
        target: BackendSnapshot,
    ) -> list[dict[str, Any]]:
        """Check specifically for the three critical summary issue categories.

        Args:
            baseline: Reference snapshot.
            target: Snapshot to compare.

        Returns:
            List of issue dicts with keys: type, session_id, detail.
        """
        issues: list[dict[str, Any]] = []
        all_ids = set(baseline.summaries.keys()) | set(target.summaries.keys())

        for sid in sorted(all_ids):
            b_summary = baseline.summaries.get(sid)
            t_summary = target.summaries.get(sid)

            if b_summary is not None and t_summary is None:
                issues.append({
                    "type": "summary_loss",
                    "session_id": sid,
                    "detail": "Baseline has summary, target does not",
                })
            elif b_summary is None and t_summary is not None:
                issues.append({
                    "type": "summary_loss",
                    "session_id": sid,
                    "detail": "Target has summary, baseline does not",
                })
            elif b_summary is not None and t_summary is not None:
                if b_summary.session_id != t_summary.session_id:
                    issues.append({
                        "type": "summary_ownership_error",
                        "session_id": sid,
                        "detail": (
                            f"Summary session_id mismatch: "
                            f"{b_summary.session_id} vs {t_summary.session_id}"
                        ),
                    })
                if b_summary.original_event_count != t_summary.original_event_count:
                    issues.append({
                        "type": "summary_overwrite_error",
                        "session_id": sid,
                        "detail": (
                            "Summary may not have been properly overwritten: "
                            f"original_event_count {b_summary.original_event_count} "
                            f"vs {t_summary.original_event_count}"
                        ),
                    })

        return issues

    @staticmethod
    def _make_diff(
        session_id: str = "",
        event_index: int | None = None,
        summary_id: str | None = None,
        field_path: str = "",
        baseline_value: Any = None,
        target_value: Any = None,
        allowed: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        """Create a standardized diff dict."""
        return {
            "session_id": session_id,
            "event_index": event_index,
            "summary_id": summary_id,
            "field_path": field_path,
            "baseline_value": str(baseline_value) if baseline_value is not None else None,
            "target_value": str(target_value) if target_value is not None else None,
            "allowed": allowed,
            "reason": reason,
        }