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
    case_diff_counts: dict[str, dict[str, int]] = {}
    diffs: list[dict[str, Any]] = []
    allowed_diffs: list[dict[str, Any]] = []
    unallowed_diffs: list[dict[str, Any]] = []

    for result in comparison_results:
        left_backend = result["left_backend"]
        right_backend = result["right_backend"]
        pair_name = f"{left_backend}_vs_{right_backend}"
        if pair_name not in backend_pairs:
            backend_pairs.append(pair_name)

        result_diffs: list[DiffEntry] = result.get("diffs", [])
        case_name = result["case_name"]
        if case_name not in case_diff_counts:
            case_diff_counts[case_name] = {"allowed": 0, "unallowed": 0}
        for diff in result_diffs:
            serialized = _diff_to_dict(diff)
            diffs.append(serialized)
            if diff.allowed:
                case_diff_counts[case_name]["allowed"] += 1
                allowed_diffs.append(serialized)
            else:
                case_diff_counts[case_name]["unallowed"] += 1
                unallowed_diffs.append(serialized)

    cases = [
        {
            "name": case_name,
            "allowed_diff_count": counts["allowed"],
            "unallowed_diff_count": counts["unallowed"],
        }
        for case_name, counts in case_diff_counts.items()
    ]

    report = {
        "schema_version": 1,
        "generated_by": "tests/sessions/test_replay_consistency.py",
        "backend_pairs": backend_pairs,
        "case_count": len(cases),
        "cases": cases,
        "allowed_diff_count": len(allowed_diffs),
        "unallowed_diff_count": len(unallowed_diffs),
        "allowed_diffs": allowed_diffs,
        "unallowed_diffs": unallowed_diffs,
        "diffs": diffs,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
