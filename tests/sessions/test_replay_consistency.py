# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency tests for cross-backend Session/Memory/Summary verification.

This module replays standardized operation sequences from JSONL case files
against multiple backends and compares their outputs to detect inconsistencies
in events, state, memory entries, and session summaries.

Test modes:
    - Lightweight (default): InMemory only, runs in < 30s.
    - SQL: Enable with --run-sql or --run-integration (uses SQLite).
    - Redis: Enable with --run-redis or --run-integration (requires Redis).

Usage:
    pytest tests/sessions/test_replay_consistency.py -v
    pytest tests/sessions/test_replay_consistency.py -v --run-sql
    pytest tests/sessions/test_replay_consistency.py -v --run-integration
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from _pytest.fixtures import FixtureRequest

from tests.sessions.harness.backend_executor import BackendExecutor
from tests.sessions.harness.diff_report import DiffReport
from tests.sessions.harness.diff_report import DiffReportGenerator
from tests.sessions.harness.replay_loader import ReplayLoader
from tests.sessions.harness.snapshot import BackendSnapshot


REPORT_OUTPUT_PATH = Path(__file__).parent / "session_memory_summary_diff_report.json"


def _get_available_backend_names(request: FixtureRequest) -> list[str]:
    """Determine which backend names are available based on test configuration."""
    names = ["inmemory"]
    if request.config.getoption("--run-sql") or request.config.getoption("--run-integration"):
        names.append("sql")
    if request.config.getoption("--run-redis") or request.config.getoption("--run-integration"):
        names.append("redis")
    if os.environ.get("TRPC_TEST_REDIS_URL"):
        if "redis" not in names:
            names.append("redis")
    return names


# ---------------------------------------------------------------------------
# Test class: lightweight mode (InMemory only)
# ---------------------------------------------------------------------------


class TestReplayConsistency:
    """Replay consistency tests comparing InMemory against other backends."""

    _all_reports: list[DiffReport] = []

    @pytest.fixture(autouse=True)
    def _setup(self, request, inmemory_services):
        self._request = request
        self._inmemory = inmemory_services
        self._report_generator = DiffReportGenerator()

    @classmethod
    def teardown_class(cls):
        """Save aggregated diff report after all cases run."""
        if cls._all_reports:
            generator = DiffReportGenerator()
            generator.save_aggregated_report(cls._all_reports, REPORT_OUTPUT_PATH)

    def _get_backend_services(self, name: str):
        if name == "inmemory":
            return self._inmemory
        elif name == "sql":
            return self._build_sql_services()
        elif name == "redis":
            return self._build_redis_services()
        raise ValueError(f"Unknown backend: {name}")

    @staticmethod
    def _build_sql_services():
        from trpc_agent_sdk.memory import SqlMemoryService
        from trpc_agent_sdk.sessions import SqlSessionService
        from trpc_agent_sdk.sessions import SessionServiceConfig
        from trpc_agent_sdk.abc import MemoryServiceConfig

        db_url = os.environ.get("TRPC_TEST_SQL_URL", "sqlite:///:memory:")
        session_config = SessionServiceConfig()
        session_config.clean_ttl_config()
        memory_config = MemoryServiceConfig(enabled=True)
        memory_config.clean_ttl_config()
        session_service = SqlSessionService(
            db_url=db_url, session_config=session_config, is_async=False
        )
        memory_service = SqlMemoryService(
            db_url=db_url, memory_service_config=memory_config, is_async=False
        )
        return session_service, memory_service

    @staticmethod
    def _build_redis_services():
        from trpc_agent_sdk.memory import RedisMemoryService
        from trpc_agent_sdk.sessions import RedisSessionService
        from trpc_agent_sdk.sessions import SessionServiceConfig
        from trpc_agent_sdk.abc import MemoryServiceConfig

        redis_url = os.environ.get("TRPC_TEST_REDIS_URL", "redis://localhost:6379")
        session_config = SessionServiceConfig()
        session_config.clean_ttl_config()
        memory_config = MemoryServiceConfig(enabled=True)
        memory_config.clean_ttl_config()
        session_service = RedisSessionService(
            db_url=redis_url, session_config=session_config
        )
        memory_service = RedisMemoryService(
            db_url=redis_url, memory_service_config=memory_config
        )
        return session_service, memory_service

    @pytest.mark.parametrize("case_file", sorted(
        (Path(__file__).parent / "replay_cases").glob("*.jsonl")
    ), ids=lambda f: f.stem)
    async def test_replay_case(self, case_file):
        """Run a single replay case against all available backends."""
        replay_case = ReplayLoader.load(case_file)
        backend_names = _get_available_backend_names(self._request)

        snapshots: dict[str, BackendSnapshot] = {}
        for name in backend_names:
            session_svc, memory_svc = self._get_backend_services(name)
            executor = BackendExecutor(session_svc, memory_svc)
            snapshot = await executor.execute(replay_case.operations)
            snapshot.backend_name = name
            snapshots[name] = snapshot

        baseline = snapshots.get("inmemory")
        if baseline is None:
            pytest.fail("InMemory backend snapshot not available")

        for name in backend_names:
            if name == "inmemory":
                continue
            target = snapshots[name]
            report = self._report_generator.generate(
                case_name=replay_case.name,
                baseline=baseline,
                target=target,
            )
            self._all_reports.append(report)

            unallowed_diffs = [d for d in report.diffs if not d.get("allowed")]
            if unallowed_diffs:
                diff_details = "\n".join(
                    f"  [{d['field_path']}] baseline={d['baseline_value']} "
                    f"target={d['target_value']} (reason: {d['reason']})"
                    for d in unallowed_diffs[:10]
                )
                if len(unallowed_diffs) > 10:
                    diff_details += f"\n  ... and {len(unallowed_diffs) - 10} more diffs"

            assert report.passed, (
                f"Case '{replay_case.name}' has {len(unallowed_diffs)} unallowed diffs "
                f"between {baseline.backend_name} and {target.backend_name}:\n"
                f"{diff_details}"
            )

            if report.summary_issues:
                issue_details = "\n".join(
                    f"  [{i['type']}] session={i['session_id']}: {i['detail']}"
                    for i in report.summary_issues
                )
                assert not report.summary_issues, (
                    f"Case '{replay_case.name}' has {len(report.summary_issues)} "
                    f"summary issues:\n{issue_details}"
                )


# ---------------------------------------------------------------------------
# Test class: injected inconsistency detection
# ---------------------------------------------------------------------------


class TestInjectedInconsistencies:
    """Tests that the harness correctly detects intentionally injected inconsistencies."""

    async def test_detect_event_content_mismatch(self, inmemory_services):
        """Verify that event content mismatch is detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        from tests.sessions.harness.replay_loader import Operation

        correct_ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        wrong_ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "WRONG TEXT"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(correct_ops)
        snapshot_a.backend_name = "correct"

        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(wrong_ops)
        snapshot_b.backend_name = "wrong"

        generator = DiffReportGenerator()
        report = generator.generate("injected_content", snapshot_a, snapshot_b)

        unallowed = [d for d in report.diffs if not d.get("allowed")]
        assert len(unallowed) > 0, "Should detect content mismatch"
        assert any("WRONG" in str(d.get("target_value", "")) for d in unallowed), (
            "Should detect the injected wrong text"
        )

    async def test_detect_state_value_mismatch(self, inmemory_services):
        """Verify that state value mismatch is detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        from tests.sessions.harness.replay_loader import Operation

        correct_ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {"key": "correct"}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        wrong_ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {"key": "wrong"}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(correct_ops)
        snapshot_a.backend_name = "correct"
        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(wrong_ops)
        snapshot_b.backend_name = "wrong"

        generator = DiffReportGenerator()
        report = generator.generate("injected_state", snapshot_a, snapshot_b)

        unallowed = [d for d in report.diffs if not d.get("allowed")]
        assert len(unallowed) > 0, "Should detect state mismatch"

    async def test_detect_event_count_mismatch(self, inmemory_services):
        """Verify that event count mismatch is detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        from tests.sessions.harness.replay_loader import Operation

        correct_ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "A"}]}}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "assistant", "content": {"parts": [{"text": "B"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]
        wrong_ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "A"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(correct_ops)
        snapshot_a.backend_name = "correct"
        executor2 = BackendExecutor(session_svc, memory_svc)
        snapshot_b = await executor2.execute(wrong_ops)
        snapshot_b.backend_name = "wrong"

        generator = DiffReportGenerator()
        report = generator.generate("injected_count", snapshot_a, snapshot_b)

        unallowed = [d for d in report.diffs if not d.get("allowed")]
        assert len(unallowed) > 0, "Should detect event count mismatch"

    async def test_detect_summary_loss(self, inmemory_services):
        """Verify that summary loss is detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        from tests.sessions.harness.replay_loader import Operation
        from trpc_agent_sdk.sessions import SessionSummary

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        snapshot_a = await executor.execute(ops)
        snapshot_a.backend_name = "with_summary"

        snapshot_a.summaries["s"] = SessionSummary(
            session_id="s",
            summary_text="Test summary",
            original_event_count=1,
            compressed_event_count=1,
            summary_timestamp=1000.0,
        )

        target = BackendSnapshot(backend_name="without_summary")
        target.sessions = snapshot_a.sessions

        generator = DiffReportGenerator()
        report = generator.generate("injected_summary_loss", snapshot_a, target)

        loss_issues = [i for i in report.summary_issues if i["type"] == "summary_loss"]
        assert len(loss_issues) >= 1, (
            f"Should detect summary loss, got {len(loss_issues)} issues"
        )

    async def test_detect_summary_ownership_error(self, inmemory_services):
        """Verify that summary session ownership error is detected."""
        session_svc, memory_svc = inmemory_services
        executor = BackendExecutor(session_svc, memory_svc)

        from tests.sessions.harness.replay_loader import Operation
        from trpc_agent_sdk.sessions import SessionSummary

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s1", "state": {}}),
            Operation(op="append_event", params={"session_id": "s1", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="get_session", params={"session_id": "s1"}),
        ]

        snapshot_a = await executor.execute(ops)
        snapshot_a.backend_name = "correct"

        snapshot_a.summaries["s1"] = SessionSummary(
            session_id="s1",
            summary_text="Summary for s1",
            original_event_count=1,
            compressed_event_count=1,
            summary_timestamp=1000.0,
        )

        target = BackendSnapshot(backend_name="wrong_owner")
        target.sessions = snapshot_a.sessions

        target.summaries["s1"] = SessionSummary(
            session_id="s99",
            summary_text="Summary for s1",
            original_event_count=1,
            compressed_event_count=1,
            summary_timestamp=1000.0,
        )

        generator = DiffReportGenerator()
        report = generator.generate("injected_ownership", snapshot_a, target)

        ownership_issues = [
            i for i in report.summary_issues
            if i["type"] == "summary_ownership_error"
        ]
        assert len(ownership_issues) >= 1, (
            f"Should detect summary ownership error, got {len(ownership_issues)} issues"
        )


# ---------------------------------------------------------------------------
# Test class: summary issue detection accuracy
# ---------------------------------------------------------------------------


class TestSummaryIssueDetection:
    """Tests that verify summary loss, overwrite, and ownership error detection."""

    async def test_summary_loss_detection(self, inmemory_services):
        """Verify 100% detection rate of summary loss."""
        session_svc, memory_svc = inmemory_services
        from tests.sessions.harness.replay_loader import Operation
        from trpc_agent_sdk.sessions import SessionSummary

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "Test"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        executor = BackendExecutor(session_svc, memory_svc)
        baseline = await executor.execute(ops)
        baseline.backend_name = "has_summary"

        baseline.summaries["s"] = SessionSummary(
            session_id="s",
            summary_text="This summary should be detected as lost",
            original_event_count=1,
            compressed_event_count=1,
            summary_timestamp=1000.0,
        )

        target = BackendSnapshot(backend_name="no_summary")
        target.sessions = baseline.sessions

        generator = DiffReportGenerator()
        report = generator.generate("summary_loss_test", baseline, target)

        summary_loss_issues = [i for i in report.summary_issues if i["type"] == "summary_loss"]
        assert len(summary_loss_issues) >= 1, (
            f"Should detect summary loss, got {len(summary_loss_issues)} issues"
        )

    async def test_summary_overwrite_detection(self, inmemory_services):
        """Verify 100% detection rate of summary overwrite errors."""
        session_svc, memory_svc = inmemory_services
        from tests.sessions.harness.replay_loader import Operation

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s", "state": {}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "user", "content": {"parts": [{"text": "First"}]}}}),
            Operation(op="append_event", params={"session_id": "s", "event": {"author": "assistant", "content": {"parts": [{"text": "Second"}]}}}),
            Operation(op="get_session", params={"session_id": "s"}),
        ]

        executor = BackendExecutor(session_svc, memory_svc)
        baseline = await executor.execute(ops)
        baseline.backend_name = "correct"

        executor2 = BackendExecutor(session_svc, memory_svc)
        target = await executor2.execute(ops)
        target.backend_name = "target"

        from trpc_agent_sdk.sessions import SessionSummary
        if baseline.sessions:
            sid = list(baseline.sessions.keys())[0]
            baseline.summaries[sid] = SessionSummary(
                session_id=sid,
                summary_text="Summary 1",
                original_event_count=4,
                compressed_event_count=2,
                summary_timestamp=1000.0,
            )
            target.summaries[sid] = SessionSummary(
                session_id=sid,
                summary_text="Summary 2",
                original_event_count=2,
                compressed_event_count=2,
                summary_timestamp=2000.0,
            )

        generator = DiffReportGenerator()
        report = generator.generate("summary_overwrite_test", baseline, target)

        overwrite_issues = [
            i for i in report.summary_issues
            if i["type"] == "summary_overwrite_error"
        ]
        assert len(overwrite_issues) >= 1, (
            f"Should detect summary overwrite error, got {len(overwrite_issues)} issues"
        )

    async def test_summary_ownership_detection(self, inmemory_services):
        """Verify 100% detection rate of summary session ownership errors."""
        session_svc, memory_svc = inmemory_services
        from tests.sessions.harness.replay_loader import Operation

        ops = [
            Operation(op="create_session", params={"app": "test", "user": "u", "session_id": "s1", "state": {}}),
            Operation(op="append_event", params={"session_id": "s1", "event": {"author": "user", "content": {"parts": [{"text": "Hello"}]}}}),
            Operation(op="get_session", params={"session_id": "s1"}),
        ]

        executor = BackendExecutor(session_svc, memory_svc)
        baseline = await executor.execute(ops)
        baseline.backend_name = "correct"

        executor2 = BackendExecutor(session_svc, memory_svc)
        target = await executor2.execute(ops)
        target.backend_name = "wrong_owner"

        from trpc_agent_sdk.sessions import SessionSummary
        baseline.summaries["s1"] = SessionSummary(
            session_id="s1",
            summary_text="Summary for s1",
            original_event_count=2,
            compressed_event_count=2,
            summary_timestamp=1000.0,
        )
        target.summaries["s1"] = SessionSummary(
            session_id="s99",
            summary_text="Summary for s1",
            original_event_count=2,
            compressed_event_count=2,
            summary_timestamp=1000.0,
        )

        generator = DiffReportGenerator()
        report = generator.generate("summary_ownership_test", baseline, target)

        ownership_issues = [
            i for i in report.summary_issues
            if i["type"] == "summary_ownership_error"
        ]
        assert len(ownership_issues) >= 1, (
            f"Should detect summary ownership error, got {len(ownership_issues)} issues"
        )