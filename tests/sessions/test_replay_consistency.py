#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""End-to-end replay consistency tests.

Runs all 10 replay cases through backend pairs (InMemory vs SQL),
normalizes results, compares them with field-level precision, and
generates a structured diff report.
"""

from __future__ import annotations

import os
import time

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.memory._in_memory_memory_service import InMemoryMemoryService
from trpc_agent_sdk.memory._sql_memory_service import SqlMemoryService
from trpc_agent_sdk.sessions._in_memory_session_service import InMemorySessionService
from trpc_agent_sdk.sessions._sql_session_service import SqlSessionService
from trpc_agent_sdk.sessions._types import SessionServiceConfig

from .replay_cases._base import load_replay_cases
from .replay_harness._comparator import compare_results
from .replay_harness._engine import ReplayEngine
from .replay_harness._normalizer import normalize_backend_result
from .replay_harness._report import CaseResult
from .replay_harness._report import generate_report
from .replay_harness._report import write_report

# ── helpers ────────────────────────────────────────────────────────────


def _make_session_config():
    config = SessionServiceConfig()
    config.clean_ttl_config()
    return config


def _make_memory_config():
    return MemoryServiceConfig(enabled=True)


async def _create_inmem_pair():
    session_svc = InMemorySessionService(session_config=_make_session_config())
    mem_svc = InMemoryMemoryService(
        memory_service_config=_make_memory_config(), enabled=True)
    return session_svc, mem_svc


async def _create_sql_pair():
    session_svc = SqlSessionService(
        db_url="sqlite:///:memory:",
        session_config=_make_session_config(),
        is_async=False,
    )
    await session_svc._sql_storage.create_sql_engine()

    mem_svc = SqlMemoryService(
        db_url="sqlite:///:memory:",
        enabled=True,
        memory_service_config=_make_memory_config(),
        is_async=False,
    )
    await mem_svc._sql_storage.create_sql_engine()

    return session_svc, mem_svc


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
async def inmem_pair():
    svc, mem = await _create_inmem_pair()
    yield svc, mem
    await svc.close()
    await mem.close()


@pytest.fixture
async def inmem_sql_pair():
    svc_inmem, mem_inmem = await _create_inmem_pair()
    svc_sql, mem_sql = await _create_sql_pair()
    yield (svc_inmem, mem_inmem), (svc_sql, mem_sql)
    await svc_inmem.close()
    await mem_inmem.close()
    await svc_sql.close()
    await mem_sql.close()


# ── parametrized E2E tests ─────────────────────────────────────────────


ALL_CASES = load_replay_cases()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ALL_CASES, ids=[c.case_id for c in ALL_CASES])
class TestReplayConsistency:

    async def test_inmem_vs_sql_consistency(self, case, inmem_sql_pair):
        (svc_a, mem_a), (svc_b, mem_b) = inmem_sql_pair

        engine_a = ReplayEngine(svc_a, mem_a)
        engine_b = ReplayEngine(svc_b, mem_b)

        result_a = await engine_a.run_case(case)
        result_b = await engine_b.run_case(case)

        if case.inject_anomaly:
            self._apply_anomaly(result_b, case.inject_anomaly)

        norm_a = normalize_backend_result(
            result_a.events, result_a.state,
            result_a.summaries, result_a.memory_entries, result_a.errors,
        )
        norm_b = normalize_backend_result(
            result_b.events, result_b.state,
            result_b.summaries, result_b.memory_entries, result_b.errors,
        )

        diffs = compare_results(
            norm_a, norm_b,
            session_id=case.session_setup.get("session_id", case.case_id),
            backend_pair=("in_memory", "sql"),
        )
        unallowed = [d for d in diffs if not d.allowed]

        if case.expect_pass:
            max_diffs = max(1, int(len(case.operations) * 0.05))
            assert len(unallowed) <= max_diffs, (
                f"Too many unallowed diffs ({len(unallowed)}) for {case.case_id}"
            )
        else:
            assert len(diffs) > 0, (
                f"Injected anomaly NOT detected for {case.case_id}"
            )

    @staticmethod
    def _apply_anomaly(result, anomaly_spec):
        category = anomaly_spec.get("category")
        action = anomaly_spec.get("action")

        if category == "events" and action == "insert_extra":
            extra = anomaly_spec.get("extra_event", {})
            idx = anomaly_spec.get("event_index", len(result.events))
            result.events.insert(idx, {
                "author": extra.get("author", "intruder"),
                "content": {"parts": [{"text": extra.get("text", "")}],
                            "role": "user"},
                "invocation_id": "anomaly",
                "actions": {"state_delta": {}},
                "partial": False,
                "visible": True,
            })

        if category == "state" and action == "mutate_value":
            field_path = anomaly_spec.get("field_path", "")
            corrupted_value = anomaly_spec.get("corrupted_value")
            if field_path in result.state:
                result.state[field_path] = corrupted_value


# ── diff report generation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_diff_report(inmem_sql_pair):
    (svc_a, mem_a), (svc_b, mem_b) = inmem_sql_pair

    engine_a = ReplayEngine(svc_a, mem_a)
    engine_b = ReplayEngine(svc_b, mem_b)

    case_results: list[CaseResult] = []

    for case in ALL_CASES:
        result_a = await engine_a.run_case(case)
        result_b = await engine_b.run_case(case)

        if case.inject_anomaly:
            TestReplayConsistency._apply_anomaly(result_b, case.inject_anomaly)

        norm_a = normalize_backend_result(
            result_a.events, result_a.state,
            result_a.summaries, result_a.memory_entries, result_a.errors,
        )
        norm_b = normalize_backend_result(
            result_b.events, result_b.state,
            result_b.summaries, result_b.memory_entries, result_b.errors,
        )

        diffs = compare_results(
            norm_a, norm_b,
            session_id=case.session_setup.get("session_id", case.case_id),
            backend_pair=("in_memory", "sql"),
        )
        unallowed = [d for d in diffs if not d.allowed]

        if case.expect_pass:
            max_diffs = max(1, int(len(case.operations) * 0.05))
            status = "pass" if len(unallowed) <= max_diffs else "fail"
        else:
            status = "pass" if len(diffs) > 0 else "fail"

        case_results.append(CaseResult(
            case_id=case.case_id,
            description=case.description,
            status=status,
            diffs=diffs,
            is_anomaly_case=not case.expect_pass,
        ))

    report = generate_report(case_results, backends=["in_memory", "sql"])

    report_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "build")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, "session_memory_summary_diff_report.json")
    write_report(report, report_path)

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"\nDiff report written to {report_path} at {timestamp}")
    print(f"  Total: {report.summary.total}  Passed: {report.summary.passed}"
          f"  Failed: {report.summary.failed}")
