"""JSON report writer for replay consistency comparisons."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any

from .comparator import DiffEntry


def _diff_to_dict(diff: DiffEntry) -> dict[str, Any]:
    return asdict(diff)


def _backend_statuses(backend_pairs: list[str], backend_statuses: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if backend_statuses is not None:
        return backend_statuses

    active_backends = sorted({backend for pair in backend_pairs for backend in pair.split("_vs_")})
    ordered_backends = ["inmemory", "sqlite"]
    ordered_backends.extend(backend for backend in active_backends if backend not in ordered_backends)
    statuses = [{"name": backend, "status": "ok", "reason": ""} for backend in ordered_backends]
    optional_backends = [
        ("external_sql", "TRPC_AGENT_REPLAY_SQL_URL"),
        ("redis", "TRPC_AGENT_REPLAY_REDIS_URL"),
    ]
    for name, env_var in optional_backends:
        if name not in active_backends and not os.environ.get(env_var):
            statuses.append({"name": name, "status": "skipped", "reason": f"{env_var} is not set"})
    return statuses


def _serialize_diffs(diffs: list[DiffEntry], mutation: str | None = None) -> list[dict[str, Any]]:
    serialized = []
    for diff in diffs:
        item = _diff_to_dict(diff)
        if mutation is not None:
            item["mutation"] = mutation
        serialized.append(item)
    return serialized


def write_report(
    path: Path,
    comparison_results: list[dict[str, Any]],
    *,
    backend_statuses: list[dict[str, Any]] | None = None,
    mutation_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    backend_pairs = []
    cases: list[dict[str, Any]] = []
    diffs: list[dict[str, Any]] = []
    allowed_diffs: list[dict[str, Any]] = []
    unallowed_diffs: list[dict[str, Any]] = []
    normal_unexpected_count = 0

    for result in comparison_results:
        left_backend = result["left_backend"]
        right_backend = result["right_backend"]
        pair_name = f"{left_backend}_vs_{right_backend}"
        if pair_name not in backend_pairs:
            backend_pairs.append(pair_name)

        result_diffs: list[DiffEntry] = result.get("diffs", [])
        allowed_count = sum(1 for diff in result_diffs if diff.allowed)
        unallowed_count = sum(1 for diff in result_diffs if not diff.allowed)
        normal_unexpected_count += unallowed_count
        case_name = result["case_name"]
        cases.append(
            {
                "name": case_name,
                "backend_pair": pair_name,
                "unexpected_diff_count": unallowed_count,
                "allowed_diff_count": allowed_count,
                "unallowed_diff_count": unallowed_count,
                "elapsed_ms": result.get("elapsed_ms", 0),
            }
        )
        for serialized, diff in zip(_serialize_diffs(result_diffs), result_diffs):
            diffs.append(serialized)
            if diff.allowed:
                allowed_diffs.append(serialized)
            else:
                unallowed_diffs.append(serialized)

    mutation_results = mutation_results or []
    undetected_mutations = []
    detected_count = 0
    for result in mutation_results:
        result_diffs = result.get("diffs", [])
        mutation = result["mutation"]
        detected = bool(result.get("detected"))
        if detected:
            detected_count += 1
        else:
            undetected_mutations.append({"case_name": result["case_name"], "mutation": mutation})

        for serialized, diff in zip(_serialize_diffs(result_diffs, mutation), result_diffs):
            diffs.append(serialized)
            if diff.allowed:
                allowed_diffs.append(serialized)
            else:
                unallowed_diffs.append(serialized)

    report = {
        "schema_version": 1,
        "generated_at": "deterministic",
        "generated_by": "tests/sessions/test_replay_consistency.py",
        "backend_pairs": backend_pairs,
        "backend_statuses": _backend_statuses(backend_pairs, backend_statuses),
        "case_count": len(cases),
        "cases": cases,
        "allowed_diff_count": len(allowed_diffs),
        "unallowed_diff_count": len(unallowed_diffs),
        "allowed_diffs": allowed_diffs,
        "unallowed_diffs": unallowed_diffs,
        "diffs": diffs,
        "mutation_summary": {
            "mutation_count": len(mutation_results),
            "detected_count": detected_count,
            "undetected_mutations": undetected_mutations,
        },
        "false_positive_summary": {
            "normal_case_count": len(comparison_results),
            "unexpected_diff_count": normal_unexpected_count,
        },
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report
