# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Report schema and acceptance matrix tests."""

from __future__ import annotations

import json
from pathlib import Path

from .capabilities import capabilities_for
from .cases import standard_cases
from .diff import DiffEntry
from .reporting import ReplayReport
from .reporting import build_metrics


def test_acceptance_matrix_matches_fixtures():
    matrix = _load_json("acceptance_matrix.json")
    case_ids = [case.case_id for case in standard_cases()]

    assert len(matrix["required_cases"]) == 10
    assert set(matrix["required_cases"]) <= set(case_ids)
    assert set(matrix["extended_cases"]) <= set(case_ids)
    assert not (set(matrix["required_cases"]) & set(matrix["extended_cases"]))
    assert matrix["requirements"]["p0_mutation_score"]["mutation_detection_rate"] == 1.0


def test_report_conforms_to_local_schema():
    diff = DiffEntry(
        case_id="schema",
        backend_pair=("reference", "actual"),
        session_id="s1",
        entity_type="event",
        entity_id="e1",
        index=0,
        field_path="$.sessions[0].events[0].content",
        reference_value="left",
        actual_value="right",
        allowed=False,
        category="content_mismatch",
        reason="",
    )
    report = ReplayReport(
        case_id="schema",
        backend_pair=("reference", "actual"),
        metrics=build_metrics(normal_case_count=1, normal_case_pass_count=0, diffs=[diff]),
        diffs=[diff.to_dict()],
        capabilities=capabilities_for("in_memory", "sqlite"),
    ).to_dict()
    schema = _load_json("report_schema.json")

    _assert_required_keys(report, schema["required"])
    _assert_required_keys(report["diffs"][0], schema["$defs"]["diff"]["required"])
    assert report["schema_version"] == "1.0"
    assert report["unexpected_diffs"] == report["diffs"]
    assert report["allowed_diffs"] == []


def test_report_is_deterministic_without_runtime_fields():
    metrics = build_metrics(normal_case_count=1, normal_case_pass_count=1, diffs=[])
    first = ReplayReport(case_id="deterministic", backend_pair=("a", "b"), metrics=metrics, diffs=[]).to_dict()
    second = ReplayReport(case_id="deterministic", backend_pair=("a", "b"), metrics=metrics, diffs=[]).to_dict()

    assert _strip_runtime_fields(first) == _strip_runtime_fields(second)


def _load_json(name: str):
    return json.loads((Path(__file__).with_name(name)).read_text(encoding="utf-8"))


def _assert_required_keys(value: dict, required: list[str]) -> None:
    assert set(required) <= set(value)


def _strip_runtime_fields(value: dict) -> dict:
    stripped = dict(value)
    stripped.pop("generated_at", None)
    return stripped
