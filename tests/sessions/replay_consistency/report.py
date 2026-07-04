"""JSON report writer for replay consistency comparisons."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .comparator import DiffEntry


def _diff_to_dict(diff: DiffEntry) -> dict[str, Any]:
    return asdict(diff)


def write_report(path: Path, comparison_results: list[dict[str, Any]]) -> dict[str, Any]:
    backend_pairs = []
    case_diff_counts: dict[str, int] = {}
    diffs: list[dict[str, Any]] = []

    for result in comparison_results:
        left_backend = result["left_backend"]
        right_backend = result["right_backend"]
        pair_name = f"{left_backend}_vs_{right_backend}"
        if pair_name not in backend_pairs:
            backend_pairs.append(pair_name)

        result_diffs: list[DiffEntry] = result.get("diffs", [])
        unallowed_count = sum(1 for diff in result_diffs if not diff.allowed)
        case_name = result["case_name"]
        case_diff_counts[case_name] = case_diff_counts.get(case_name, 0) + unallowed_count
        diffs.extend(_diff_to_dict(diff) for diff in result_diffs if not diff.allowed)

    cases = [
        {
            "name": case_name,
            "unallowed_diff_count": unallowed_count,
        }
        for case_name, unallowed_count in case_diff_counts.items()
    ]

    report = {
        "schema_version": 1,
        "generated_by": "tests/sessions/test_replay_consistency.py",
        "backend_pairs": backend_pairs,
        "case_count": len(cases),
        "cases": cases,
        "diffs": diffs,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
