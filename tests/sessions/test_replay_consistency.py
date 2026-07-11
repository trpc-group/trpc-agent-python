"""Replay consistency smoke tests across session and memory backends."""

from __future__ import annotations

import asyncio
import os
from time import perf_counter
from typing import Callable
from typing import Type

import pytest

from .replay_cases import REPLAY_ACCEPTANCE_CASES
from .replay_cases import REPLAY_ALL_CASES
from .replay_cases import REPLAY_EXTRA_CASES
from .replay_cases import REPLAY_TARGETED_CASES
from .replay_harness import DEFAULT_REPORT_PATH
from .replay_harness import build_case_matrix_report
from .replay_harness import InMemoryReplayAdapter
from .replay_harness import RedisReplayAdapter
from .replay_harness import ReplayBackendAdapter
from .replay_harness import SqliteReplayAdapter
from .replay_harness import build_comparison_report
from .replay_harness import diff_backend_snapshots
from .replay_harness import expected_diff_paths_for_backend_pair
from .replay_harness import format_diffs
from .replay_harness import get_replay_clock_metadata
from .replay_harness import write_diff_report
from .replay_models import BackendSnapshot
from .replay_models import DiffEntry
from .replay_models import ReplayCase


ADAPTER_TYPES: tuple[Type[ReplayBackendAdapter], ...] = (
    InMemoryReplayAdapter,
    SqliteReplayAdapter,
)
REDIS_REPLAY_URL_ENV = "TRPC_AGENT_REPLAY_REDIS_URL"
AdapterFactory = Callable[[], ReplayBackendAdapter]


def _find_case(case_id: str) -> ReplayCase:
    for case in REPLAY_ALL_CASES + REPLAY_TARGETED_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(f"Unknown replay case: {case_id}")


async def _run_case_on_backend(
    adapter_factory: AdapterFactory,
    case: ReplayCase,
) -> tuple[BackendSnapshot, dict[str, object], dict[str, object]]:
    adapter = adapter_factory()
    await adapter.setup(case)
    try:
        snapshot = await adapter.run_case(case)
        return snapshot, adapter.get_runtime_metadata(), adapter.get_report_metadata()
    finally:
        await adapter.close()


async def _run_replay_cases(
    adapter_factories: tuple[AdapterFactory, ...],
    *,
    mode_name: str,
    write_report: bool = True,
) -> tuple[list[DiffEntry], list[dict[str, object]], float]:
    all_diffs: list[DiffEntry] = []
    case_reports: list[dict[str, object]] = []
    backend_report_metadata: dict[str, dict[str, object]] = {}
    start_time = perf_counter()

    for case in REPLAY_ALL_CASES:
        backend_runs = [await _run_case_on_backend(adapter_factory, case) for adapter_factory in adapter_factories]
        snapshots = [run[0] for run in backend_runs]
        runtime_metadata = {
            snapshot.backend_name: runtime_info
            for snapshot, runtime_info, _ in backend_runs
        }
        for snapshot, _, report_metadata in backend_runs:
            backend_report_metadata.setdefault(snapshot.backend_name, report_metadata)
        baseline_snapshot = snapshots[0]
        comparisons: list[dict[str, object]] = []
        for other_snapshot in snapshots[1:]:
            diffs = diff_backend_snapshots(case=case, left=baseline_snapshot, right=other_snapshot)
            all_diffs.extend(diffs)
            comparisons.append(
                build_comparison_report(
                    case,
                    backend_a=baseline_snapshot.backend_name,
                    backend_b=other_snapshot.backend_name,
                    diffs=diffs,
                    runtime_context={
                        baseline_snapshot.backend_name: runtime_metadata[baseline_snapshot.backend_name],
                        other_snapshot.backend_name: runtime_metadata[other_snapshot.backend_name],
                    },
                ))
        case_reports.append(build_case_matrix_report(case, comparisons))

    elapsed_seconds = perf_counter() - start_time
    if write_report:
        write_diff_report(
            DEFAULT_REPORT_PATH,
            case_reports,
            metadata={
                "mode": mode_name,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "backend_names": [factory.name for factory in adapter_factories],
                "baseline_backend": adapter_factories[0].name,
                "comparison_mode": "baseline_vs_all",
                "backend_summaries": [backend_report_metadata[name] for name in sorted(backend_report_metadata)],
                "clock_strategy": get_replay_clock_metadata(),
                "acceptance_case_count": len(REPLAY_ACCEPTANCE_CASES),
                "all_case_count": len(REPLAY_ALL_CASES),
            },
        )
    return all_diffs, case_reports, elapsed_seconds


def _make_redis_adapter_factory() -> AdapterFactory:
    redis_url = os.getenv(REDIS_REPLAY_URL_ENV)
    if not redis_url:
        raise RuntimeError("Redis replay URL is not configured.")

    class _ConfiguredRedisReplayAdapter(RedisReplayAdapter):
        name = "redis"

        def __init__(self) -> None:
            super().__init__(redis_url=redis_url)

    return _ConfiguredRedisReplayAdapter


def _assert_case_expectations(
    all_diffs: list[DiffEntry],
    case_set: tuple[ReplayCase, ...],
    case_reports: list[dict[str, object]],
) -> None:
    case_map = {case.case_id: case for case in case_set}
    report_map = {str(report["case_id"]): report for report in case_reports}

    for case_id, case in case_map.items():
        case_report = report_map[case_id]
        comparisons = case_report.get("comparisons", [])
        for comparison in comparisons:
            backend_a = str(comparison["backend_a"])
            backend_b = str(comparison["backend_b"])
            expected_paths = set(expected_diff_paths_for_backend_pair(case, backend_a=backend_a, backend_b=backend_b))
            case_diffs = [
                diff
                for diff in all_diffs
                if diff.case_id == case_id
                and diff.backend_a == backend_a
                and diff.backend_b == backend_b
                and not diff.allowed
            ]
            detected_paths = {diff.path for diff in case_diffs}

            if expected_paths:
                missing_paths = sorted(expected_paths - detected_paths)
                unexpected_paths = sorted(detected_paths - expected_paths)
                assert not missing_paths, (
                    f"{case_id} missing expected diffs for {backend_a} vs {backend_b}: {missing_paths}"
                )
                assert not unexpected_paths, (
                    f"{case_id} produced unexpected diffs for {backend_a} vs {backend_b}: {unexpected_paths}\n"
                    f"{format_diffs(case_diffs)}"
                )
                continue

            assert not case_diffs, (
                f"{case_id} produced unexpected diffs for {backend_a} vs {backend_b}:\n"
                f"{format_diffs(case_diffs)}"
            )


def test_replay_consistency_smoke_cases() -> None:
    """Ensure acceptance and extended replay cases behave as expected."""

    all_diffs, case_reports, elapsed_seconds = asyncio.run(_run_replay_cases(ADAPTER_TYPES, mode_name="lightweight"))
    _assert_case_expectations(all_diffs, REPLAY_ALL_CASES, case_reports)

    assert elapsed_seconds <= 30.0, f"lightweight replay mode exceeded 30s: {elapsed_seconds:.3f}s"


def test_acceptance_case_count() -> None:
    """Keep the public acceptance suite fixed at 10 cases."""

    assert len(REPLAY_ACCEPTANCE_CASES) == 10
    assert len(REPLAY_ALL_CASES) >= len(REPLAY_ACCEPTANCE_CASES)
    assert len(REPLAY_EXTRA_CASES) >= 1


def test_replay_harness_collects_all_session_alias_snapshots() -> None:
    """Non-active sessions should remain visible in the final snapshot."""

    snapshot, _, _ = asyncio.run(
        _run_case_on_backend(InMemoryReplayAdapter, _find_case("cross_session_memory_aggregation"))
    )

    assert snapshot.active_session_alias == "default"
    assert set(snapshot.sessions_by_alias) == {"source", "default"}
    assert snapshot.sessions_by_alias["source"].session_id == "replay-memory-source"
    assert snapshot.sessions_by_alias["source"].session["events"][0]["text"] == "Please remember that I prefer oolong tea."
    assert snapshot.sessions_by_alias["default"].session_id == "replay-memory-target"


def test_replay_harness_preserves_memory_query_observations_across_restart() -> None:
    """Repeated query names should preserve separate observations before and after restart."""

    snapshot, _, _ = asyncio.run(
        _run_case_on_backend(SqliteReplayAdapter, _find_case("memory_query_observation_survives_restart"))
    )

    observations = sorted(snapshot.memory.values(), key=lambda item: item["step_index"])
    assert [item["query_name"] for item in observations] == ["tea_preference", "tea_preference"]
    assert [item["session_alias"] for item in observations] == ["default", "default"]
    assert len(observations) == 2
    first_texts = {entry["text"] for entry in observations[0]["entries"]}
    second_texts = {entry["text"] for entry in observations[1]["entries"]}
    assert "Please remember that my favorite tea is oolong." in first_texts
    assert "I will remember your oolong preference." in first_texts
    assert "Also remember that I enjoy jasmine tea." in second_texts
    assert "I will remember the jasmine preference too." in second_texts
    assert "Also remember that I enjoy jasmine tea." not in first_texts


def test_replay_harness_keeps_duplicate_query_names_per_session_alias() -> None:
    """Query names may repeat across aliases without overwriting previous observations."""

    snapshot, _, _ = asyncio.run(
        _run_case_on_backend(SqliteReplayAdapter, _find_case("duplicate_memory_query_name_across_sessions"))
    )

    observations = sorted(snapshot.memory.values(), key=lambda item: item["step_index"])
    assert len(observations) == 2
    assert [item["query_name"] for item in observations] == ["shared_preference_search", "shared_preference_search"]
    assert [item["session_alias"] for item in observations] == ["source", "default"]
    assert observations[0]["step_index"] < observations[1]["step_index"]
    assert observations[0]["entries"] != observations[1]["entries"]
    first_texts = {entry["text"] for entry in observations[0]["entries"]}
    second_texts = {entry["text"] for entry in observations[1]["entries"]}
    assert any("dragon well" in text.lower() for text in first_texts)
    assert any("dragon well" in text.lower() for text in second_texts)


def test_replay_consistency_redis_integration_mode() -> None:
    """Run an optional Redis-backed integration comparison when configured."""

    redis_url = os.getenv(REDIS_REPLAY_URL_ENV)
    if not redis_url:
        pytest.skip(f"{REDIS_REPLAY_URL_ENV} is not set")

    redis_adapter_factory = _make_redis_adapter_factory()
    all_diffs, case_reports, _ = asyncio.run(
        _run_replay_cases(
            (InMemoryReplayAdapter, SqliteReplayAdapter, redis_adapter_factory),
            mode_name="integration",
            write_report=True,
        ))
    _assert_case_expectations(all_diffs, REPLAY_ALL_CASES, case_reports)
