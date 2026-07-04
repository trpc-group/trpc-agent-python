"""End-to-end replay snapshot mutation tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from .replay_consistency.cases import replay_cases
from .replay_consistency.comparator import DiffEntry
from .replay_consistency.comparator import recursive_diff
from .replay_consistency.comparator import unallowed_diffs
from .replay_consistency.mutations import MUTATION_SECTION
from .replay_consistency.mutations import SUMMARY_REQUIRED_MUTATIONS
from .replay_consistency.mutations import mutate_snapshot
from .replay_consistency.mutations import mutations_for_case
from .replay_consistency.report import write_report
from .test_replay_consistency import _run_real_inmemory_snapshot


def _assert_diff_context(
    *,
    case_name: str,
    mutation: str,
    clean_snapshot: dict[str, Any],
    diffs: list[DiffEntry],
) -> None:
    unexpected = unallowed_diffs(diffs)
    assert unexpected, f"{case_name}/{mutation} was not detected"

    expected_section = MUTATION_SECTION[mutation]
    matching = [diff for diff in unexpected if diff.section == expected_section]
    assert matching, f"{case_name}/{mutation} did not report section {expected_section}"
    assert all(diff.session_id == clean_snapshot["session_id"] for diff in unexpected)
    assert all(diff.path for diff in unexpected)

    if expected_section == "events":
        assert any(diff.event_index is not None for diff in matching)
    elif expected_section == "memories":
        assert any(diff.memory_index is not None for diff in matching)
    elif expected_section == "summary":
        assert any(diff.summary_id for diff in matching)


@pytest.mark.asyncio
async def test_real_replay_snapshot_mutation_detection(tmp_path: Path):
    mutation_results: list[dict[str, Any]] = []
    for case in replay_cases():
        clean = await _run_real_inmemory_snapshot(tmp_path / f"real-mutation-{case.name}", case)
        for mutation in mutations_for_case(case):
            mutated = copy.deepcopy(clean)
            mutated["backend"] = "mutated"
            mutate_snapshot(mutation, mutated)
            diffs = recursive_diff(clean, mutated)
            _assert_diff_context(
                case_name=case.name,
                mutation=mutation,
                clean_snapshot=clean,
                diffs=diffs,
            )
            mutation_results.append(
                {
                    "case_name": case.name,
                    "mutation": mutation,
                    "detected": bool(unallowed_diffs(diffs)),
                    "diffs": diffs,
                }
            )

    report = write_report(
        tmp_path / "session_memory_summary_mutation_diff_report.json",
        [],
        mutation_results=mutation_results,
    )
    assert report["mutation_summary"]["undetected_mutations"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", SUMMARY_REQUIRED_MUTATIONS)
async def test_real_summary_required_mutations_detected(tmp_path: Path, mutation: str):
    case = next(case for case in replay_cases() if case.name == "summary_generation")
    clean = await _run_real_inmemory_snapshot(tmp_path / f"summary-required-{mutation}", case)
    mutated = copy.deepcopy(clean)
    mutated["backend"] = "mutated"
    mutate_snapshot(mutation, mutated)
    diffs = recursive_diff(clean, mutated)
    _assert_diff_context(
        case_name=case.name,
        mutation=mutation,
        clean_snapshot=clean,
        diffs=diffs,
    )
    assert all(diff.section == "summary" for diff in unallowed_diffs(diffs) if diff.path.startswith("summary"))
