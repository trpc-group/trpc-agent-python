# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Session / Memory / Summary replay consistency test framework.

This module provides:

- :class:`ReplayHarness`: drives a :class:`ReplayCase` through two
  ``(session_service, memory_service)`` backends and collects raw results.
- :class:`DiffEngine`: normalizes raw results and performs four-dimension
  comparison (events, state, memory, summary), producing a :class:`DiffReport`.
- Parameterised pytest tests that run all 10 replay cases in lightweight mode
  (InMemory vs SQLite).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from trpc_agent_sdk.abc import MemoryServiceABC, SearchMemoryResponse, SessionServiceABC
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session, SessionSummary
from trpc_agent_sdk.types import Content, EventActions, Part

from .conftest import (
    ReplayCase,
    ReplayStep,
    load_replay_case,
    list_replay_cases,
    normalize_memory_response,
    normalize_session_for_compare,
    normalize_summary_for_compare,
)

# ---------------------------------------------------------------------------
# DiffReport data structures
# ---------------------------------------------------------------------------


@dataclass
class DiffEntry:
    """A single inconsistency discovered between two backends.

    Attributes:
        category: One of ``event``, ``state``, ``memory``, ``summary``.
        session_id: The session this entry belongs to.
        event_index: Index of the event within the session (for event diffs).
        summary_id: Summary identifier (for summary diffs).
        field_path: Dot-separated path to the differing field,
            e.g. ``events[2].author``.
        value_a: Value from backend A (InMemory).
        value_b: Value from backend B (SQLite).
        type: ``inconsistency`` or ``allowed_diff``.
        message: Human-readable explanation.
    """

    category: str = ""
    session_id: str = ""
    event_index: Optional[int] = None
    summary_id: Optional[str] = None
    field_path: str = ""
    value_a: Any = None
    value_b: Any = None
    type: str = "inconsistency"
    message: str = ""


@dataclass
class DiffReport:
    """Aggregated result of comparing two backends on a single replay case.

    Attributes:
        case_name: Name of the replayed case.
        passed: ``True`` when no inconsistencies were found.
        backend_a_label: Human-readable label for backend A.
        backend_b_label: Human-readable label for backend B.
        diffs: All discovered differences (inconsistencies + allowed diffs).
        allowed_diffs: Differences that are explicitly allowed by policy.
    """

    case_name: str = ""
    passed: bool = True
    backend_a_label: str = "InMemory"
    backend_b_label: str = "SQLite"
    diffs: List[DiffEntry] = field(default_factory=list)
    allowed_diffs: List[DiffEntry] = field(default_factory=list)

    @property
    def inconsistencies(self) -> List[DiffEntry]:
        """Return only the entries classified as real inconsistencies."""
        return [d for d in self.diffs if d.type == "inconsistency"]

    def summary(self) -> str:
        """Return a one-line summary of the report."""
        n_issues = len(self.inconsistencies)
        n_allowed = len(self.allowed_diffs)
        status = "PASSED" if self.passed else "FAILED"
        return (f"[{status}] {self.case_name}: "
                f"{n_issues} inconsistency(ies), {n_allowed} allowed diff(s)")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dict."""

        def _entry_dict(e: DiffEntry) -> Dict[str, Any]:
            d = {
                "category": e.category,
                "session_id": e.session_id,
                "field_path": e.field_path,
                "value_a": _serialize_value(e.value_a),
                "value_b": _serialize_value(e.value_b),
                "type": e.type,
                "message": e.message,
            }
            if e.event_index is not None:
                d["event_index"] = e.event_index
            if e.summary_id is not None:
                d["summary_id"] = e.summary_id
            return d

        return {
            "case_name": self.case_name,
            "passed": self.passed,
            "backend_a": self.backend_a_label,
            "backend_b": self.backend_b_label,
            "inconsistencies": [_entry_dict(d) for d in self.inconsistencies],
            "allowed_diffs": [_entry_dict(d) for d in self.allowed_diffs],
        }


def _serialize_value(val: Any) -> Any:
    """Convert non-serializable values (e.g. Pydantic models) to plain types."""
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, (list, tuple)):
        return [_serialize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _serialize_value(v) for k, v in val.items()}
    # Pydantic model or other object: try to dump
    if hasattr(val, "model_dump"):
        return val.model_dump()
    return str(val)


# ---------------------------------------------------------------------------
# DiffEngine
# ---------------------------------------------------------------------------


class DiffEngine:
    """Four-dimension comparison engine for session replay results.

    Usage::

        engine = DiffEngine()
        report = engine.compare(case_name, result_a, result_b)
    """

    # Fields that may legitimately differ between backends
    ALLOWED_FIELD_PREFIXES = (
        "invocation_id",
        "save_key",
        "long_running_tool_ids",
    )

    def compare(
        self,
        case_name: str,
        result_a: RawResult,
        result_b: RawResult,
    ) -> DiffReport:
        """Compare two raw results and produce a DiffReport."""
        report = DiffReport(case_name=case_name)
        report.backend_a_label = result_a.label
        report.backend_b_label = result_b.label

        # 1. Events comparison
        self._compare_events(result_a, result_b, report)

        # 2. State comparison
        self._compare_state(result_a, result_b, report)

        # 3. Memory comparison
        self._compare_memory(result_a, result_b, report)

        # 4. Summary comparison
        self._compare_summary(result_a, result_b, report)

        report.passed = len(report.inconsistencies) == 0
        return report

    # ------------------------------------------------------------------
    # Event comparison
    # ------------------------------------------------------------------

    def _compare_events(self, a: RawResult, b: RawResult, report: DiffReport) -> None:
        session_id = a.session.id if a.session else (b.session.id if b.session else "")
        events_a = a.normalized_session.get("events", []) if a.normalized_session else []
        events_b = b.normalized_session.get("events", []) if b.normalized_session else []

        # If either side has a summary anchor, align comparison at the summary
        # boundary. Events before the summary are "covered by summary" and may
        # differ in count across backends (InMemory stores compressed events,
        # SQL re-reads from event table). Only events at/after the anchor are
        # compared 1:1.
        summary_idx_a = self._find_summary_anchor(events_a)
        summary_idx_b = self._find_summary_anchor(events_b)

        if summary_idx_a >= 0 and summary_idx_b >= 0:
            # Both sides have summary anchors — align at the later anchor
            # to avoid comparing retained events (pre-anchor) against new
            # events (post-anchor), which produces misleading diffs.
            anchor_at = max(summary_idx_a, summary_idx_b)
            post_summary_len = min(len(events_a) - anchor_at, len(events_b) - anchor_at)
            for i in range(post_summary_len):
                self._compare_event_dicts(
                    anchor_at + i,
                    session_id,
                    events_a[anchor_at + i],
                    events_b[anchor_at + i],
                    report,
                )
            return
        elif summary_idx_a >= 0 or summary_idx_b >= 0:
            # Only one side was summarised — this is expected when comparing
            # InMemory (in-memory compression) vs SQL (re-reads from event table).
            # Skip event-by-event comparison and rely on summary metadata check.
            return

        if len(events_a) != len(events_b):
            report.diffs.append(
                DiffEntry(
                    category="event",
                    session_id=session_id,
                    field_path="events.count",
                    value_a=len(events_a),
                    value_b=len(events_b),
                    message=f"Event count mismatch: {len(events_a)} vs {len(events_b)}",
                ))
            min_len = min(len(events_a), len(events_b))
        else:
            min_len = len(events_a)

        for i in range(min_len):
            self._compare_event_dicts(
                i,
                session_id,
                events_a[i],
                events_b[i],
                report,
            )

    @staticmethod
    def _find_summary_anchor(events: List[Dict[str, Any]]) -> int:
        """Find the index of the first summary anchor event, or -1."""
        for i, evt in enumerate(events):
            if evt.get("author") == "system":
                for p in evt.get("parts", []):
                    if p.get("text", "").startswith("Previous conversation summary:"):
                        return i
        return -1

    def _compare_event_dicts(
        self,
        idx: int,
        session_id: str,
        evt_a: Dict[str, Any],
        evt_b: Dict[str, Any],
        report: DiffReport,
    ) -> None:
        """Compare two normalized event dicts field by field."""
        # Compare top-level scalar fields (is_final_response stripped by normalizer)
        for fname in ("author", "partial", "branch"):
            va = evt_a.get(fname)
            vb = evt_b.get(fname)
            if va != vb:
                self._add_diff(
                    report,
                    "event",
                    session_id,
                    f"events[{idx}].{fname}",
                    va,
                    vb,
                )

        # Detect summary anchor events: both sides produce a summary event
        # after compression. The exact summary text is compared in the summary
        # dimension; skip parts-level comparison for summary events here.
        is_summary_a = self._is_summary_event(evt_a)
        is_summary_b = self._is_summary_event(evt_b)
        if is_summary_a and is_summary_b:
            return  # skip parts comparison for summary events
        if is_summary_a or is_summary_b:
            self._add_diff(
                report,
                "event",
                session_id,
                f"events[{idx}].author",
                evt_a.get("author"),
                evt_b.get("author"),
                message="Summary event exists in one backend but not the other",
            )

        # Compare parts
        parts_a = evt_a.get("parts", [])
        parts_b = evt_b.get("parts", [])
        if len(parts_a) != len(parts_b):
            self._add_diff(
                report,
                "event",
                session_id,
                f"events[{idx}].parts.count",
                len(parts_a),
                len(parts_b),
            )

        for pi in range(min(len(parts_a), len(parts_b))):
            self._compare_parts(idx, pi, session_id, parts_a[pi], parts_b[pi], report)

        # Compare state_delta
        sda = evt_a.get("state_delta", {}) or {}
        sdb = evt_b.get("state_delta", {}) or {}
        if sda != sdb:
            self._add_diff(
                report,
                "event",
                session_id,
                f"events[{idx}].state_delta",
                sda,
                sdb,
            )

    @staticmethod
    def _is_summary_event(evt: Dict[str, Any]) -> bool:
        """Check if a normalized event dict represents a summary anchor."""
        if evt.get("author") != "system":
            return False
        for p in evt.get("parts", []):
            text = p.get("text", "")
            if text.startswith("Previous conversation summary:"):
                return True
        return False

    def _compare_parts(
        self,
        event_idx: int,
        part_idx: int,
        session_id: str,
        pa: Dict[str, Any],
        pb: Dict[str, Any],
        report: DiffReport,
    ) -> None:
        """Compare two event parts."""
        prefix = f"events[{event_idx}].parts[{part_idx}]"

        # Type
        ta = pa.get("type", "")
        tb = pb.get("type", "")
        if ta != tb:
            self._add_diff(report, "event", session_id, f"{prefix}.type", ta, tb)
            return

        # Type-specific comparison
        if ta == "text":
            if pa.get("text") != pb.get("text"):
                self._add_diff(report, "event", session_id, f"{prefix}.text", pa.get("text"), pb.get("text"))
        elif ta == "function_call":
            if pa.get("name") != pb.get("name"):
                self._add_diff(report, "event", session_id, f"{prefix}.name", pa.get("name"), pb.get("name"))
            if pa.get("args") != pb.get("args"):
                self._add_diff(report, "event", session_id, f"{prefix}.args", pa.get("args"), pb.get("args"))
        elif ta == "function_response":
            if pa.get("name") != pb.get("name"):
                self._add_diff(report, "event", session_id, f"{prefix}.name", pa.get("name"), pb.get("name"))
            if pa.get("response") != pb.get("response"):
                self._add_diff(report, "event", session_id, f"{prefix}.response", pa.get("response"),
                               pb.get("response"))

    # ------------------------------------------------------------------
    # State comparison
    # ------------------------------------------------------------------

    def _compare_state(self, a: RawResult, b: RawResult, report: DiffReport) -> None:
        session_id = a.session.id if a.session else (b.session.id if b.session else "")
        state_a = (a.normalized_session or {}).get("state", {}) or {}
        state_b = (b.normalized_session or {}).get("state", {}) or {}

        all_keys = set(state_a.keys()) | set(state_b.keys())
        for key in sorted(all_keys):
            va = state_a.get(key)
            vb = state_b.get(key)
            if va != vb:
                self._add_diff(report, "state", session_id, f"state.{key}", va, vb)

    # ------------------------------------------------------------------
    # Memory comparison
    # ------------------------------------------------------------------

    def _compare_memory(self, a: RawResult, b: RawResult, report: DiffReport) -> None:
        session_id = a.session.id if a.session else (b.session.id if b.session else "")
        mem_a = a.normalized_memory or []
        mem_b = b.normalized_memory or []

        if len(mem_a) != len(mem_b):
            self._add_diff(report, "memory", session_id, "memory.count", len(mem_a), len(mem_b))

        for i in range(min(len(mem_a), len(mem_b))):
            # Compare author and text content in memory entries
            author_a = mem_a[i].get("author", "")
            author_b = mem_b[i].get("author", "")
            if author_a != author_b:
                self._add_diff(report, "memory", session_id, f"memory[{i}].author", author_a, author_b)

            parts_a = mem_a[i].get("parts", [])
            parts_b = mem_b[i].get("parts", [])
            for pi in range(min(len(parts_a), len(parts_b))):
                if parts_a[pi].get("text") != parts_b[pi].get("text"):
                    self._add_diff(
                        report,
                        "memory",
                        session_id,
                        f"memory[{i}].parts[{pi}].text",
                        parts_a[pi].get("text"),
                        parts_b[pi].get("text"),
                    )

    # ------------------------------------------------------------------
    # Summary comparison
    # ------------------------------------------------------------------

    def _compare_summary(self, a: RawResult, b: RawResult, report: DiffReport) -> None:
        sum_a = a.normalized_summary
        sum_b = b.normalized_summary

        if sum_a is None and sum_b is None:
            return  # Both have no summary — consistent
        if sum_a is None or sum_b is None:
            sid = (sum_a or sum_b or {}).get("session_id", "")
            self._add_diff(report,
                           "summary",
                           sid,
                           "summary.exists",
                           sum_a is not None,
                           sum_b is not None,
                           message="Summary exists in one backend but not the other")
            return

        sid = sum_a.get("session_id", "")
        # session_id must match exactly (summary ownership is critical)
        if sum_a["session_id"] != sum_b["session_id"]:
            self._add_diff(report,
                           "summary",
                           sid,
                           "summary.session_id",
                           sum_a["session_id"],
                           sum_b["session_id"],
                           message="SUMMARY_OWNERSHIP: summary session_id mismatch")

        # event counts
        if sum_a["original_event_count"] != sum_b["original_event_count"]:
            self._add_diff(report, "summary", sid, "summary.original_event_count", sum_a["original_event_count"],
                           sum_b["original_event_count"])
        if sum_a["compressed_event_count"] != sum_b["compressed_event_count"]:
            self._add_diff(report, "summary", sid, "summary.compressed_event_count", sum_a["compressed_event_count"],
                           sum_b["compressed_event_count"])

        # summary_text: semantic comparison (strip and compare)
        text_a = (sum_a["summary_text"] or "").strip()
        text_b = (sum_b["summary_text"] or "").strip()
        if text_a != text_b:
            self._add_diff(report,
                           "summary",
                           sid,
                           "summary.summary_text",
                           text_a,
                           text_b,
                           message="Summary text differs between backends")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _add_diff(
        self,
        report: DiffReport,
        category: str,
        session_id: str,
        field_path: str,
        value_a: Any,
        value_b: Any,
        *,
        message: str = "",
    ) -> None:
        """Add a diff entry, classifying it as inconsistency or allowed diff."""
        if any(field_path.startswith(prefix) for prefix in self.ALLOWED_FIELD_PREFIXES):
            entry = DiffEntry(
                category=category,
                session_id=session_id,
                field_path=field_path,
                value_a=value_a,
                value_b=value_b,
                type="allowed_diff",
                message=message or f"Allowed difference in {field_path}",
            )
            report.allowed_diffs.append(entry)
        else:
            entry = DiffEntry(
                category=category,
                session_id=session_id,
                field_path=field_path,
                value_a=value_a,
                value_b=value_b,
                message=message or f"Field {field_path} differs",
            )
            report.diffs.append(entry)


# ---------------------------------------------------------------------------
# RawResult: per-backend result of a single replay case
# ---------------------------------------------------------------------------


@dataclass
class RawResult:
    """Raw output collected from one backend after executing a replay case.

    Attributes:
        label: Human-readable label, e.g. ``InMemory`` or ``SQLite``.
        session: The final :class:`Session` object retrieved from the backend.
        normalized_session: Normalized dict of the session for comparison.
        memory_responses: List of ``(query, SearchMemoryResponse)`` tuples.
        normalized_memory: Normalized memory search results.
        summary: Optional :class:`SessionSummary`.
        normalized_summary: Normalized summary dict.
    """

    label: str = ""
    session: Optional[Session] = None
    normalized_session: Optional[Dict[str, Any]] = None
    memory_responses: List[Tuple[str, SearchMemoryResponse]] = field(default_factory=list)
    normalized_memory: Optional[List[Dict[str, Any]]] = None
    summary: Optional[SessionSummary] = None
    normalized_summary: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# ReplayHarness
# ---------------------------------------------------------------------------


class ReplayHarness:
    """Drives a :class:`ReplayCase` through two backends for comparison.

    Usage::

        harness = ReplayHarness()
        result_a, result_b = await harness.run_case(
            case=load_replay_case("case_01_single_turn"),
            backend_a=(session_svc_a, memory_svc_a),
            backend_b=(session_svc_b, memory_svc_b),
        )
        report = DiffEngine().compare("case_01_single_turn", result_a, result_b)
    """

    # Mapping from step action names to handler methods
    _ACTION_HANDLERS = {
        "create_session": "_handle_create_session",
        "append_event": "_handle_append_event",
        "get_session": "_handle_get_session",
        "store_memory": "_handle_store_memory",
        "search_memory": "_handle_search_memory",
        "create_session_summary": "_handle_create_session_summary",
        "get_session_summary": "_handle_get_session_summary",
        "inject_reorder_events": "_handle_inject_reorder_events",
        "inject_summary_session_id": "_handle_inject_summary_session_id",
        "inject_skip_append": "_handle_inject_skip_append",
    }

    def __init__(self):
        self._sessions: Dict[str, Session] = {}  # label -> current session

    async def run_case(
        self,
        case: ReplayCase,
        backend_a: Tuple[SessionServiceABC, MemoryServiceABC],
        backend_b: Tuple[SessionServiceABC, MemoryServiceABC],
    ) -> Tuple[RawResult, RawResult]:
        """Execute every step of *case* on both backends.

        Returns:
            Raw results from both backends.
        """
        svc_a, mem_a = backend_a
        svc_b, mem_b = backend_b

        result_a = RawResult(label=type(svc_a).__name__.replace("SessionService", ""))
        result_b = RawResult(label=type(svc_b).__name__.replace("SessionService", ""))

        self._sessions = {}

        for step in case.steps:
            handler_name = self._ACTION_HANDLERS.get(step.action)
            if handler_name is None:
                raise ValueError(f"Unknown step action: {step.action}")

            handler = getattr(self, handler_name)

            # Inject steps: apply to backend B only so the injected backend
            # differs from the reference backend (A). This creates a detectable
            # cross-backend inconsistency.
            if step.action.startswith("inject_"):
                await handler(step, svc_b, mem_b, result_b)
            else:
                await handler(step, svc_a, mem_a, result_a)
                await handler(step, svc_b, mem_b, result_b)

        # Normalize results for comparison
        result_a.normalized_session = normalize_session_for_compare(result_a.session) if result_a.session else None
        result_b.normalized_session = normalize_session_for_compare(result_b.session) if result_b.session else None
        result_a.normalized_summary = normalize_summary_for_compare(result_a.summary) if result_a.summary else None
        result_b.normalized_summary = normalize_summary_for_compare(result_b.summary) if result_b.summary else None

        # Aggregate memory results
        if result_a.memory_responses:
            all_mem = []
            for _, resp in result_a.memory_responses:
                all_mem.extend(normalize_memory_response(resp))
            result_a.normalized_memory = all_mem
        if result_b.memory_responses:
            all_mem = []
            for _, resp in result_b.memory_responses:
                all_mem.extend(normalize_memory_response(resp))
            result_b.normalized_memory = all_mem

        return result_a, result_b

    # ------------------------------------------------------------------
    # Step handlers
    # ------------------------------------------------------------------

    async def _handle_create_session(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        kw = step.kwargs
        session = await svc.create_session(
            app_name=kw.get("app_name", "test_app"),
            user_id=kw.get("user_id", "test_user"),
            session_id=kw.get("session_id", f"session_{result.label}"),
            state=kw.get("initial_state"),
        )
        self._sessions[result.label] = session

    async def _handle_append_event(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        kw = step.kwargs
        session = self._sessions.get(result.label)
        if session is None:
            raise RuntimeError(f"No session created for {result.label} before append_event")

        parts_list = kw.get("parts", [])
        parts = []
        for p in parts_list:
            ptype = p.get("type", "")
            if ptype == "text":
                parts.append(Part.from_text(text=p.get("text", "")))
            elif ptype == "function_call":
                parts.append(Part.from_function_call(
                    name=p.get("name", ""),
                    args=p.get("args", {}),
                ))
            elif ptype == "function_response":
                parts.append(Part.from_function_response(
                    name=p.get("name", ""),
                    response=p.get("response", {}),
                ))

        state_delta = kw.get("state_delta", {})
        event = Event(
            invocation_id=f"inv_{result.label}",
            author=kw.get("author", "user"),
            content=Content(parts=parts, role=kw.get("author", "user")),
            actions=EventActions(state_delta=state_delta) if state_delta else EventActions(),
        )
        await svc.append_event(session, event)
        self._sessions[result.label] = session

    async def _handle_get_session(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        kw = step.kwargs
        # Prefer self._sessions (always current) over result.session (potentially stale)
        session_id = kw.get("session_id")
        if session_id is None and result.label in self._sessions:
            session_id = self._sessions[result.label].id
        if session_id is None and result.session:
            session_id = result.session.id
        if session_id is None:
            # Derive from the stored session key
            for label, s in self._sessions.items():
                if label == result.label:
                    session_id = s.id
                    break

        app_name = kw.get("app_name", "test_app")
        user_id = kw.get("user_id", "test_user")
        session = await svc.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        if session:
            result.session = session
            self._sessions[result.label] = session

    async def _handle_store_memory(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        session = self._sessions.get(result.label)
        if session is None:
            raise RuntimeError(f"No session for {result.label} in store_memory")
        if mem.enabled:
            await mem.store_session(session)

    async def _handle_search_memory(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        kw = step.kwargs
        key = kw.get("key", "")
        query = kw.get("query", "")
        if not key or not query:
            return
        if mem.enabled:
            response = await mem.search_memory(key=key, query=query)
            result.memory_responses.append((query, response))

    async def _handle_create_session_summary(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        session = self._sessions.get(result.label)
        if session is None:
            return
        # The summarizer manager is attached to the session service
        mgr = getattr(svc, "summarizer_manager", None) if hasattr(svc, "summarizer_manager") else None
        if mgr:
            kw = step.kwargs
            await mgr.create_session_summary(session, force=kw.get("force", False))

    async def _handle_get_session_summary(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        session = self._sessions.get(result.label)
        if session is None:
            return
        mgr = getattr(svc, "summarizer_manager", None) if hasattr(svc, "summarizer_manager") else None
        if mgr:
            summary = await mgr.get_session_summary(session)
            result.summary = summary

    # ------------------------------------------------------------------
    # Injection handlers (for injected inconsistency cases)
    # ------------------------------------------------------------------

    async def _handle_inject_reorder_events(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        """Swap two events in the session's event list to simulate ordering bug."""
        session = self._sessions.get(result.label)
        if session is None or not session.events:
            return
        kw = step.kwargs
        indices = kw.get("indices", [0, 1])
        if len(indices) >= 2 and max(indices) < len(session.events):
            i, j = indices[0], indices[1]
            session.events[i], session.events[j] = session.events[j], session.events[i]
            await svc.update_session(session)

    async def _handle_inject_summary_session_id(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        """Override the summary's session_id to point to a wrong session."""
        session = self._sessions.get(result.label)
        if session is None:
            return
        mgr = getattr(svc, "summarizer_manager", None)
        if mgr:
            kw = step.kwargs
            wrong_id = kw.get("wrong_session_id", "session_wrong")
            cache = getattr(mgr, "_summarizer_cache", {})
            app_name = session.app_name
            user_id = session.user_id
            sid = session.id
            if app_name in cache and user_id in cache[app_name] and sid in cache[app_name][user_id]:
                cache[app_name][user_id][sid].session_id = wrong_id
                result.summary = cache[app_name][user_id][sid]

    async def _handle_inject_skip_append(
        self,
        step: ReplayStep,
        svc: SessionServiceABC,
        mem: MemoryServiceABC,
        result: RawResult,
    ) -> None:
        """Skip the last append on this backend (simulate write failure).

        Removes the most recently appended event from the session.
        This creates a cross-backend inconsistency when only one backend
        receives the injection.
        """
        session = self._sessions.get(result.label)
        if session is None or not session.events:
            return
        removed = session.events.pop()
        logger = logging.getLogger(__name__)
        logger.info("inject_skip_append removed event author=%s on %s", removed.author, result.label)
        await svc.update_session(session)


# ---------------------------------------------------------------------------
# Pytest tests
# ---------------------------------------------------------------------------

# List of normal (non-injected) cases to verify consistency
_NORMAL_CASES = [
    "case_01_single_turn",
    "case_02_multi_turn",
    "case_03_tool_call",
    "case_04_state_update",
]

# Cases that require memory service enabled
_MEMORY_CASES = [
    "case_05_memory_rw",
]

# Cases that require summarizer manager (note: case_07 is expected to produce
# event-level divergences because summary compression creates different event
# boundaries across backends — it is tracked as a "known inconsistency" case).
_SUMMARY_CASES = [
    "case_06_summary_gen",
]

_KNOWN_INCONSISTENCY_CASES = [
    "case_07_summary_truncate",
]

# Injected inconsistency cases
_INJECTED_CASES = [
    "case_08_exception_recovery",
    "case_09_injected_event_order",
    "case_10_injected_summary_session",
]


@pytest.mark.parametrize("case_name", _NORMAL_CASES)
async def test_replay_normal(case_name: str, full_backend_pair) -> None:
    """Verify that normal (non-injected) cases produce identical results
    across InMemory and SQLite backends."""
    case = load_replay_case(case_name)
    backend_a, backend_b = full_backend_pair

    harness = ReplayHarness()
    result_a, result_b = await harness.run_case(case, backend_a, backend_b)
    report = DiffEngine().compare(case_name, result_a, result_b)

    assert report.passed, report.summary()


@pytest.mark.parametrize("case_name", _MEMORY_CASES)
async def test_replay_with_memory(case_name: str, full_backend_pair) -> None:
    """Verify replay cases that exercise memory store and search."""
    case = load_replay_case(case_name)
    backend_a, backend_b = full_backend_pair

    harness = ReplayHarness()
    result_a, result_b = await harness.run_case(case, backend_a, backend_b)
    report = DiffEngine().compare(case_name, result_a, result_b)

    assert report.passed, report.summary()


@pytest.mark.parametrize("case_name", _SUMMARY_CASES)
async def test_replay_with_summary(case_name: str, full_backend_pair_with_summary) -> None:
    """Verify replay cases that exercise session summarization."""
    case = load_replay_case(case_name)
    backend_a, backend_b = full_backend_pair_with_summary

    harness = ReplayHarness()
    result_a, result_b = await harness.run_case(case, backend_a, backend_b)
    report = DiffEngine().compare(case_name, result_a, result_b)

    assert report.passed, report.summary()


@pytest.mark.parametrize("case_name", _INJECTED_CASES)
async def test_replay_injected_detection(case_name: str, full_backend_pair_with_summary) -> None:
    """Verify that injected inconsistencies are detected (must report failure).

    For injected cases the report *must* flag inconsistencies.
    """
    case = load_replay_case(case_name)
    backend_a, backend_b = full_backend_pair_with_summary

    harness = ReplayHarness()
    result_a, result_b = await harness.run_case(case, backend_a, backend_b)
    report = DiffEngine().compare(case_name, result_a, result_b)

    # Injected cases MUST have inconsistencies
    assert not report.passed, (f"Injected case {case_name} should have been detected as inconsistent, "
                               f"but passed. Diffs: {report.diffs}")


@pytest.mark.parametrize("case_name", _NORMAL_CASES + _MEMORY_CASES)
async def test_replay_false_positive_check(case_name: str, full_backend_pair) -> None:
    """Verify that normal (non-summary) cases have zero or very few
    inconsistencies (≤5%)."""
    backend_a, backend_b = full_backend_pair
    case = load_replay_case(case_name)
    harness = ReplayHarness()
    result_a, result_b = await harness.run_case(case, backend_a, backend_b)
    report = DiffEngine().compare(case_name, result_a, result_b)

    inconsistencies = report.inconsistencies
    total_checks = max(len(report.diffs) + len(report.allowed_diffs), 1)
    false_positive_rate = len(inconsistencies) / total_checks

    assert false_positive_rate <= 0.05, (f"False positive rate {false_positive_rate:.2%} exceeds 5% "
                                         f"for case {case_name}. Inconsistencies: {inconsistencies}")


@pytest.mark.parametrize("case_name", _SUMMARY_CASES)
async def test_replay_false_positive_check_summary(case_name: str, full_backend_pair_with_summary) -> None:
    """Verify that summary-involved cases have zero or very few
    inconsistencies (≤5%)."""
    backend_a, backend_b = full_backend_pair_with_summary
    case = load_replay_case(case_name)
    harness = ReplayHarness()
    result_a, result_b = await harness.run_case(case, backend_a, backend_b)
    report = DiffEngine().compare(case_name, result_a, result_b)

    inconsistencies = report.inconsistencies
    total_checks = max(len(report.diffs) + len(report.allowed_diffs), 1)
    false_positive_rate = len(inconsistencies) / total_checks

    assert false_positive_rate <= 0.05, (f"False positive rate {false_positive_rate:.2%} exceeds 5% "
                                         f"for case {case_name}. Inconsistencies: {inconsistencies}")


@pytest.mark.parametrize("case_name", _KNOWN_INCONSISTENCY_CASES)
async def test_replay_summary_truncation(case_name: str, full_backend_pair_with_summary) -> None:
    """Verify summary truncation with two-layer validation.

    Layer 1 — Cross-backend consistency (strict):
      - Summary metadata (session_id, summary_text, counts) must match
      - Session state must match
      - Event count divergence is recorded as allowed_diff (expected due
        to different storage models)

    Layer 2 — Per-backend semantic validation:
      - Summary was generated (non-empty text)
      - Compression happened (compressed_count < original_count)
      - New events appended after summary are present
      - Summary + retained events + new events together cover context
    """
    case = load_replay_case(case_name)
    backend_a, backend_b = full_backend_pair_with_summary

    harness = ReplayHarness()
    result_a, result_b = await harness.run_case(case, backend_a, backend_b)
    report = DiffEngine().compare(case_name, result_a, result_b)

    # ── Layer 1: Cross-backend comparison ──────────────────────────────

    # Summary metadata must be consistent across backends
    sum_a = result_a.normalized_summary
    sum_b = result_b.normalized_summary
    assert sum_a is not None, f"Backend A missing summary for {case_name}"
    assert sum_b is not None, f"Backend B missing summary for {case_name}"
    assert sum_a["session_id"] == sum_b["session_id"], "Summary session_id mismatch"
    assert sum_a["original_event_count"] == sum_b["original_event_count"]
    assert sum_a["compressed_event_count"] == sum_b["compressed_event_count"]
    assert sum_a["summary_text"].strip() == sum_b["summary_text"].strip()

    # State must be consistent (only the last get_session state is captured)
    state_a = (result_a.normalized_session or {}).get("state", {}) or {}
    state_b = (result_b.normalized_session or {}).get("state", {}) or {}
    assert state_a == state_b, f"State mismatch between backends: {state_a} vs {state_b}"

    # Event count divergence is allowed (known storage-model difference)
    events_a = (result_a.normalized_session or {}).get("events", []) or []
    events_b = (result_b.normalized_session or {}).get("events", []) or []
    if len(events_a) != len(events_b):
        report.allowed_diffs.append(
            DiffEntry(
                category="event",
                session_id=sum_a["session_id"],
                field_path="events.count",
                value_a=len(events_a),
                value_b=len(events_b),
                message=("Event count differs because InMemory stores the compressed "
                         "event list while SQL re-reads all events from the event table. "
                         "This is a known backend storage-model difference."),
            ))

    # ── Layer 2: Per-backend semantic validation ───────────────────────

    for label, result in [("InMemory", result_a), ("SQLite", result_b)]:
        summary = result.normalized_summary
        events = (result.normalized_session or {}).get("events", []) or []
        assert summary is not None, f"{label}: no summary generated"
        assert summary["summary_text"].strip(), f"{label}: empty summary text"
        assert summary["compressed_event_count"] < summary["original_event_count"], (
            f"{label}: compression did not reduce event count "
            f"({summary['compressed_event_count']} >= {summary['original_event_count']})")

        # Check that new events after summary are preserved
        # The mock summary text contains "Mock session summary" — verify
        # it appears in the event list as a summary anchor
        summary_text_found = any("Previous conversation summary:" in (p.get("text", "") or "") for evt in events
                                 for p in evt.get("parts", []))
        assert summary_text_found, f"{label}: summary event not found in session events"

        # Verify new events (cherry blossom questions) are present
        all_text = " ".join(p.get("text", "") for evt in events for p in evt.get("parts", []))
        assert "cherry blossom" in all_text.lower(), (f"{label}: new events after summary are missing. "
                                                      f"Events text: {all_text[:200]}")

    # Report should reflect expected divergences
    report.passed = len(report.inconsistencies) == 0
    assert report.passed, (
        f"{case_name}: expected cross-backend consistency but found inconsistencies:\n"
        f"{report.summary()}"
    )
    assert report.case_name == case_name
    logger = logging.getLogger(__name__)
    logger.info(
        "case_07 summary: %s (allowed diffs: %d)",
        report.summary(),
        len(report.allowed_diffs),
    )


async def test_replay_all_summary_issues_detected(full_backend_pair_with_summary) -> None:
    """Dedicated test for summary problem detection: summary loss, override,
    and wrong-session ownership must all be caught."""
    backend_a, backend_b = full_backend_pair_with_summary
    cases = load_replay_case("case_10_injected_summary_session")

    harness = ReplayHarness()
    result_a, result_b = await harness.run_case(cases, backend_a, backend_b)
    report = DiffEngine().compare("case_10_injected_summary_session", result_a, result_b)

    summary_diffs = [d for d in report.inconsistencies if d.category == "summary"]
    summary_session_issues = [d for d in summary_diffs if "session_id" in d.field_path]

    # Must detect the wrong-session-id injection
    assert len(summary_session_issues) >= 1, (
        f"Summary session_id mismatch was not detected. All summary diffs: {summary_diffs}")


async def test_diff_report_json_output(full_backend_pair, tmp_path) -> None:
    """Verify that the diff report can be serialized to JSON with all
    required fields."""
    case = load_replay_case("case_01_single_turn")
    backend_a, backend_b = full_backend_pair

    harness = ReplayHarness()
    result_a, result_b = await harness.run_case(case, backend_a, backend_b)
    report = DiffEngine().compare("case_01_single_turn", result_a, result_b)

    json_data = report.to_dict()

    # Verify required fields
    assert "case_name" in json_data
    assert "passed" in json_data
    assert "inconsistencies" in json_data
    assert "allowed_diffs" in json_data
    assert json_data["case_name"] == "case_01_single_turn"

    # Verify entry fields (if any diffs exist)
    if json_data["inconsistencies"]:
        entry = json_data["inconsistencies"][0]
        assert "session_id" in entry
        assert "field_path" in entry
        assert "value_a" in entry
        assert "value_b" in entry

    # Write to temp path for verification
    output_path = tmp_path / "session_memory_summary_diff_report.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    assert output_path.exists()


@pytest.mark.parametrize("case_name", list_replay_cases())
async def test_replay_all_cases_load_and_validate(case_name: str) -> None:
    """Quick validation that every replay case JSONL can be loaded correctly."""
    case = load_replay_case(case_name)
    assert case.name == case_name
    assert len(case.steps) >= 1, f"Case {case_name} has no steps"
    # Verify first step is always create_session (for consistency)
    if case_name not in ("case_09_injected_event_order", "case_10_injected_summary_session"):
        assert case.steps[0].action == "create_session", (f"Case {case_name} should start with create_session")


async def test_generate_aggregated_diff_report(
    full_backend_pair,
    full_backend_pair_with_summary,
    tmp_path,
) -> None:
    """Generate the aggregated diff report and verify key fields.

    Runs all 10 replay cases against both backends and aggregates the
    per-case DiffReport into a single JSON file written to tmp_path.
    Summary-involved cases use ``full_backend_pair_with_summary``; others
    use ``full_backend_pair``.
    """
    _SUMMARY_OR_INJECTED = (set(_SUMMARY_CASES) | set(_KNOWN_INCONSISTENCY_CASES) | set(_INJECTED_CASES))

    normal_a, normal_b = full_backend_pair
    summary_a, summary_b = full_backend_pair_with_summary

    all_reports = []

    for case_name in list_replay_cases():
        # Choose the right backend pair
        if case_name in _SUMMARY_OR_INJECTED:
            bp_a, bp_b = summary_a, summary_b
        else:
            bp_a, bp_b = normal_a, normal_b

        case = load_replay_case(case_name)
        harness = ReplayHarness()
        result_a, result_b = await harness.run_case(case, bp_a, bp_b)
        report = DiffEngine().compare(case_name, result_a, result_b)
        all_reports.append(report.to_dict())

    output = {
        "generated_at": time.time(),
        "total_cases": len(all_reports),
        "cases_passed": sum(1 for r in all_reports if r["passed"]),
        "backends": {
            "a": "InMemorySessionService",
            "b": "SqlSessionService (SQLite :memory:)",
        },
        "reports": all_reports,
    }

    # Write to tmp_path instead of repo source directory
    output_path = tmp_path / "session_memory_summary_diff_report.json"
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Assertions ──
    assert output["total_cases"] == len(_ALL_CASES), (
        f"Expected {len(_ALL_CASES)} cases, got {output['total_cases']}"
    )
    # Normal cases (1-6) should pass
    for r in all_reports:
        case_name = r.get("case_name", "unknown")
        if case_name in _NORMAL_CASES:
            assert r["passed"], (
                f"Normal case {case_name} should pass but got inconsistencies: "
                f"{[d['message'] for d in r.get('inconsistencies', [])]}"
            )

    print(f"\nAggregated diff report written to: {output_path}")
    print(f"Total cases: {output['total_cases']}, Passed: {output['cases_passed']}")
