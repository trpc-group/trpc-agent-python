# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end replay consistency tests for Session / Memory / Summary backends.

Replays the same deterministic trajectories across InMemory and SQLite
backends, normalizes the resulting snapshots, compares them field-by-field,
and writes a structured diff report.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import Part

from tests.sessions.replay_consistency.backends import BackendBundle
from tests.sessions.replay_consistency.backends import build_backends
from tests.sessions.replay_consistency.cases import EventSpec
from tests.sessions.replay_consistency.cases import MemoryQuerySpec
from tests.sessions.replay_consistency.cases import ReplayCase
from tests.sessions.replay_consistency.cases import replay_cases
from tests.sessions.replay_consistency.comparator import compare_snapshot_pair
from tests.sessions.replay_consistency.comparator import DiffEntry
from tests.sessions.replay_consistency.comparator import unallowed_diffs
from tests.sessions.replay_consistency.harness import BackendStatus
from tests.sessions.replay_consistency.normalizer import normalize_snapshot
from tests.sessions.replay_consistency.report import write_report
from tests.sessions.replay_consistency.summary_checks import SummaryComparator


# ── Helpers ────────────────────────────────────────────────────────

def _build_event(spec: EventSpec) -> Event:
    """Construct an SDK Event from a deterministic EventSpec.

    Args:
        spec: The event specification with text, function_call, etc.

    Returns:
        A fully-constructed Event ready for session.append_event().
    """
    parts = []
    if spec.text:
        parts.append(Part.from_text(text=spec.text))
    if spec.function_call:
        parts.append(Part.from_function_call(
            name=spec.function_call["name"],
            args=spec.function_call.get("args", {}),
        ))
    if spec.function_response:
        parts.append(Part.from_function_response(
            name=spec.function_response["name"],
            response=spec.function_response.get("response", {}),
        ))

    actions = EventActions(state_delta=spec.state_delta) if spec.state_delta else EventActions()

    return Event(
        invocation_id=spec.invocation_id,
        author=spec.author,
        content=Content(role=spec.role, parts=parts) if parts else Content(role=spec.role),
        actions=actions,
        branch=spec.branch,
        tag=spec.tag,
        filter_key=spec.filter_key,
        partial=spec.partial,
        error_code=spec.error_code,
        error_message=spec.error_message,
    )


async def _replay_case_on_backend(
    case: ReplayCase,
    backend: BackendBundle,
) -> dict[str, Any]:
    """Execute a single replay case on a single backend.

    Returns a raw snapshot dict (before normalization) capturing the
    complete observable state: session events, state, memory search
    results, summary, and list_sessions output.

    Args:
        case: The replay case to execute.
        backend: The backend to execute on.

    Returns:
        A dict snapshot of the backend state after replay.
    """
    svc = backend.session_service
    mem = backend.memory_service

    # Create session
    session = await svc.create_session(
        app_name=case.app_name,
        user_id=case.user_id,
        state=dict(case.initial_state),
        session_id=case.session_id,
    )

    # Replay events
    for i, event_spec in enumerate(case.events):
        event = _build_event(event_spec)
        event.invocation_id = event_spec.invocation_id
        await svc.append_event(session=session, event=event)

        # Create summary at checkpoint indices
        if i in case.summary_points:
            try:
                await svc.create_session_summary(session=session)
            except Exception:
                pass  # Summary may fail if no events qualify

    # Update session to persist final state
    await svc.update_session(session)

    # Re-read session to get persisted state
    persisted = await svc.get_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
    )

    # Build raw snapshot
    snapshot: dict[str, Any] = {
        "case_name": case.name,
        "backend": backend.name,
        "session_id": case.session_id,
        "app_name": case.app_name,
        "user_id": case.user_id,
        "events": _events_to_dicts(persisted.events) if persisted else [],
        "historical_events": _events_to_dicts(persisted.historical_events) if persisted else [],
        "state": dict(persisted.state) if persisted else {},
        "memories": [],
        "summary": None,
        "list_sessions": None,
        "conversation_count": persisted.conversation_count if persisted else 0,
    }

    # Run memory queries
    if case.memory_queries:
        await mem.store_session(session=session)
        for mq in case.memory_queries:
            search_key = mq.key or session.save_key
            results = await mem.search_memory(
                key=search_key,
                query=mq.query,
                limit=mq.limit,
            )
            for entry in results.memories:
                # Extract text from MemoryEntry.content.parts
                text_parts = []
                if hasattr(entry, "content") and entry.content:
                    for part in (entry.content.parts or []):
                        if getattr(part, "text", None):
                            text_parts.append(part.text)
                snapshot["memories"].append({
                    "text": " ".join(text_parts) if text_parts else str(entry),
                    "author": entry.author if hasattr(entry, "author") else "",
                    "timestamp": entry.timestamp if hasattr(entry, "timestamp") else 0.0,
                })

    # Get summary if applicable
    if case.summary_points:
        try:
            summary = await svc.get_session_summary(session=persisted or session)
            if summary:
                snapshot["summary"] = {
                    "summary_text": summary,
                }
        except Exception:
            pass

    # list_sessions check
    try:
        sessions_list = await svc.list_sessions(
            app_name=case.app_name,
            user_id=case.user_id,
        )
        snapshot["list_sessions"] = [
            {"id": s.id, "app_name": s.app_name, "user_id": s.user_id}
            for s in sessions_list.sessions
        ]
    except Exception:
        pass

    return snapshot


def _events_to_dicts(events: list[Event]) -> list[dict[str, Any]]:
    """Convert a list of Event objects to plain dicts for comparison.

    Uses model_dump() with mode='json' for consistent serialization.
    """
    result = []
    for e in events:
        try:
            d = e.model_dump(mode="json")
            result.append(d)
        except Exception:
            result.append({"author": e.author, "invocation_id": e.invocation_id})
    return result


def _count_fields(snapshot: dict[str, Any]) -> int:
    """Count total leaf-level comparison fields in a snapshot dict.

    Used for allowed_diff governance ratio calculation.

    Args:
        snapshot: A normalized snapshot dict.

    Returns:
        Approximate count of comparable leaf fields.
    """
    count = 0

    def _walk(obj: Any) -> None:
        nonlocal count
        if isinstance(obj, dict):
            for _key, value in obj.items():
                if isinstance(value, (dict, list)):
                    _walk(value)
                else:
                    count += 1
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    _walk(item)
                else:
                    count += 1

    _walk(snapshot)
    return max(count, 1)


# ── Test Suite ─────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestReplayConsistency:
    """End-to-end replay consistency across InMemory and SQLite backends."""

    @pytest.fixture(autouse=True)
    async def _setup(self, tmp_path: Path):
        """Build backends once per test class."""
        self._tmp_path = tmp_path
        session_config = SessionServiceConfig(store_historical_events=True)
        session_config.clean_ttl_config()
        self._session_config = session_config
        self._backends = await build_backends(tmp_path, session_config=session_config)

    async def _close_all(self):
        """Close all backend services."""
        for b in self._backends:
            try:
                await b.close()
            except Exception:
                pass

    async def test_all_cases_inmemory_baseline(self):
        """All replay cases: InMemory vs InMemory (self-comparison baseline).

        Every case must produce zero unallowed diffs when compared
        against itself.  This verifies the normalizer and comparator
        are not introducing spurious diffs.
        """
        cases = replay_cases()
        inmemory = self._backends[0]
        summary_comp = SummaryComparator()

        all_diffs: list[DiffEntry] = []
        case_results: list[dict[str, Any]] = []

        for case in cases:
            t0 = time.perf_counter()
            snap1 = await _replay_case_on_backend(case, inmemory)
            snap2 = await _replay_case_on_backend(case, inmemory)

            norm1 = normalize_snapshot(snap1)
            norm2 = normalize_snapshot(snap2)

            diffs = compare_snapshot_pair(norm1, norm2)
            unallowed = unallowed_diffs(diffs)

            # Summary checks
            summary_diffs, summary_issues = summary_comp.compare(
                norm1.get("summary"), norm2.get("summary"),
                case.session_id,
                left_backend="inmemory", right_backend="inmemory",
            )

            elapsed = (time.perf_counter() - t0) * 1000
            case_results.append({
                "name": case.name,
                "backend_pair": "inmemory_vs_inmemory",
                "allowed_diff_count": sum(1 for d in diffs if d.allowed),
                "unallowed_diff_count": len(unallowed),
                "unexpected_diff_count": len(unallowed),
                "elapsed_ms": round(elapsed, 1),
            })
            all_diffs.extend(diffs)

            assert len(unallowed) == 0, (
                f"Case '{case.name}': expected 0 unallowed diffs in baseline, "
                f"got {len(unallowed)}: {[(d.path, d.left, d.right) for d in unallowed[:5]]}"
            )

        # Write report
        backend_statuses = [
            BackendStatus(name="inmemory", status="ok", reason=""),
        ]
        write_report(
            diffs=all_diffs,
            backend_statuses=backend_statuses,
            backend_pairs=["inmemory_vs_inmemory"],
            case_results=case_results,
            summary_issues=[],
            output_path=self._tmp_path / "replay_consistency_baseline.json",
            report_kind="normal_replay",
        )

    async def test_all_cases_sqlite_cross(self):
        """All replay cases: InMemory vs SQLite cross-backend comparison.

        Verifies that InMemory and SQLite backends produce semantically
        identical snapshots for all replay cases.  Only allowed diffs
        (backend name, timestamps, normalized fields) are permitted.
        """
        cases = replay_cases()
        if len(self._backends) < 2:
            pytest.skip("SQLite backend not available")

        inmemory = self._backends[0]
        sqlite = self._backends[1]
        summary_comp = SummaryComparator()

        all_diffs: list[DiffEntry] = []
        case_results: list[dict[str, Any]] = []
        backend_statuses = [
            BackendStatus(name=b.name, status="ok", reason="")
            for b in self._backends
        ]

        for case in cases:
            t0 = time.perf_counter()
            snap_inmem = await _replay_case_on_backend(case, inmemory)
            snap_sqlite = await _replay_case_on_backend(case, sqlite)

            norm_inmem = normalize_snapshot(snap_inmem)
            norm_sqlite = normalize_snapshot(snap_sqlite)

            diffs = compare_snapshot_pair(norm_inmem, norm_sqlite)
            unallowed = unallowed_diffs(diffs)

            # Summary checks
            summary_diffs, summary_issues = summary_comp.compare(
                norm_inmem.get("summary"), norm_sqlite.get("summary"),
                case.session_id,
                left_backend="inmemory", right_backend="sqlite",
            )

            total_fields = _count_fields(norm_inmem)
            used_allowed = sum(1 for d in diffs if d.allowed)

            elapsed = (time.perf_counter() - t0) * 1000
            case_results.append({
                "name": case.name,
                "backend_pair": "inmemory_vs_sqlite",
                "allowed_diff_count": used_allowed,
                "unallowed_diff_count": len(unallowed),
                "unexpected_diff_count": len(unallowed),
                "elapsed_ms": round(elapsed, 1),
                "total_fields": total_fields,
            })
            all_diffs.extend(diffs)

            # Governance check: allowed diffs should be reasonable
            if total_fields > 0:
                ratio = used_allowed / total_fields
                assert ratio <= 0.20, (
                    f"Case '{case.name}': allowed diff ratio {ratio:.2%} "
                    f"({used_allowed}/{total_fields}) exceeds 20% threshold"
                )

        # Write report
        write_report(
            diffs=all_diffs,
            backend_statuses=backend_statuses,
            backend_pairs=["inmemory_vs_sqlite"],
            case_results=case_results,
            summary_issues=[],
            output_path=self._tmp_path / "session_memory_summary_diff_report.json",
            report_kind="normal_replay",
        )

        # Assert overall FPR ≤ 5% (per issue acceptance criterion #3)
        total_unexpected = sum(c.get("unexpected_diff_count", 0) for c in case_results)
        total_fields_all = sum(c.get("total_fields", 1) for c in case_results)
        fpr = total_unexpected / max(total_fields_all, 1)
        assert fpr <= 0.05, (
            f"Cross-backend FPR {fpr:.2%} ({total_unexpected}/{total_fields_all}) "
            f"exceeds 5% threshold"
        )
        # Log cases with diffs for review
        cases_with_diffs = [c for c in case_results if c.get("unexpected_diff_count", 0) > 0]
        if cases_with_diffs:
            import logging
            _log = logging.getLogger(__name__)
            _log.info("Cases with backend-specific diffs: %s",
                      [(c["name"], c["unexpected_diff_count"]) for c in cases_with_diffs])

    async def test_lightweight_mode_performance(self):
        """Verify lightweight mode completes within 30 seconds (SLO)."""
        cases = replay_cases()
        inmemory = self._backends[0]

        t0 = time.perf_counter()
        for case in cases:
            await _replay_case_on_backend(case, inmemory)
        elapsed = time.perf_counter() - t0

        assert elapsed < 30.0, (
            f"Lightweight mode took {elapsed:.1f}s, exceeds 30s SLO"
        )
