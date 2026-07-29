# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end report tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from pipeline import EvalOptimizePipeline


@pytest.mark.asyncio
async def test_fake_pipeline_writes_business_reports_with_gate_delta_and_metadata(
    example_root: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    system_before = (example_root / "prompts" / "system.md").read_text(encoding="utf-8")
    skill_before = (example_root / "prompts" / "skill.md").read_text(encoding="utf-8")

    report_paths = await EvalOptimizePipeline(
        train_evalset_path=example_root / "train.evalset.json",
        val_evalset_path=example_root / "val.evalset.json",
        optimizer_config_path=example_root / "optimizer.json",
        gate_config_path=example_root / "gate.json",
        output_dir=output_dir,
        mode="fake",
    ).run()

    assert report_paths.json_path == output_dir / "optimization_report.json"
    assert report_paths.markdown_path == output_dir / "optimization_report.md"
    assert not (output_dir / "baseline_evaluation.json").exists()
    assert not (output_dir / "candidate_evaluation.json").exists()

    report = json.loads(report_paths.json_path.read_text(encoding="utf-8"))
    markdown = report_paths.markdown_path.read_text(encoding="utf-8")
    assert report["schema_version"] == "eval-optimize-loop-v1"
    assert list(report)[:6] == ["schema_version", "run", "inputs", "config", "baseline", "failure_attribution"]
    assert report["config"]["gate"] == report["gate_decision"]["config"]
    assert "failure_attribution" in report
    assert _path_like_values_are_relative(report)
    assert report["metadata"]["example_root"] == "."
    assert report["metadata"]["reproduction_command"] == "python run_pipeline.py --mode fake"
    assert report["metadata"]["output_paths"] == {
        "json": _relative_to_example(output_dir / "optimization_report.json", example_root),
        "markdown": _relative_to_example(output_dir / "optimization_report.md", example_root),
    }

    train = report["baseline"]["train"]
    val = report["baseline"]["val"]
    assert train["failure_attribution_summary"]["final_answer_mismatch"] == 1
    assert val["failure_attribution_summary"]["final_answer_mismatch"] == 2
    assert report["failure_attribution"]["overall_summary"]["final_answer_mismatch"] == 5
    assert {case["variant"] for case in report["failure_attribution"]["failed_cases"]} == {"baseline", "candidate"}

    candidate = report["candidate"]
    assert candidate["train"]["case_count"] == 3
    assert candidate["val"]["case_count"] == 3
    assert candidate["train"]["failed_count"] == 0
    assert candidate["val"]["failed_count"] == 2
    assert set(candidate["prompts"]) == {"system_prompt", "skill"}

    delta = report["delta"]
    assert delta["summary"]["overall_change_type"] == "mixed"
    assert delta["summary"]["new_pass_count"] == 2
    assert delta["summary"]["new_fail_count"] == 1
    assert delta["summary"]["regression_count"] == 1
    assert delta["train"]["score_delta"] > 0
    assert delta["val"]["score_delta"] == 0.0
    val_changes = [case["change_type"] for case in delta["val"]["case_deltas"]]
    assert val_changes.count("new_pass") == 1
    assert val_changes.count("new_fail") == 1
    assert val_changes.count("unchanged") == 1

    gate = report["gate_decision"]
    assert gate["decision"] == "reject"
    assert gate["accepted"] is False
    assert gate["recommended_action"] == "keep_baseline_prompts"
    assert gate["summary"]["critical_regression_count"] == 1

    assert "# Evaluation Optimization Pipeline 报告" in markdown
    assert "## 错误归因汇总" in markdown
    assert "## Gate 决策" in markdown
    assert "REJECT" in markdown
    assert "python run_pipeline.py --mode fake" in markdown
    assert (example_root / "prompts" / "system.md").read_text(encoding="utf-8") == system_before
    assert (example_root / "prompts" / "skill.md").read_text(encoding="utf-8") == skill_before


def test_committed_output_report_is_fake_canonical(example_root: Path) -> None:
    report_path = example_root / "output" / "optimization_report.json"
    markdown_path = example_root / "output" / "optimization_report.md"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")

    assert report["run"]["mode"] == "fake"
    assert "audit" not in report
    assert report["failure_attribution"]["overall_summary"]["final_answer_mismatch"] == 5
    assert report["candidate"]["train"]["failed_count"] == 0
    assert report["delta"]["summary"]["overall_change_type"] == "mixed"
    assert report["delta"]["summary"]["new_pass_count"] == 2
    assert report["delta"]["summary"]["new_fail_count"] == 1
    assert report["gate_decision"]["summary"]["critical_regression_count"] == 1
    assert _path_like_values_are_relative(report)
    assert "python run_pipeline.py --mode fake" in markdown


def test_fake_split_records_empty_actual_when_trace_actual_is_missing(example_root: Path, tmp_path: Path) -> None:
    pipeline = EvalOptimizePipeline(
        train_evalset_path=example_root / "train.evalset.json",
        val_evalset_path=example_root / "val.evalset.json",
        optimizer_config_path=example_root / "optimizer.json",
        gate_config_path=example_root / "gate.json",
        output_dir=tmp_path / "output",
        mode="fake",
    )
    eval_set = {
        "eval_set_id":
        "missing_actual",
        "eval_cases": [{
            "eval_id":
            "case_without_actual",
            "conversation": [{
                "user_content": {
                    "parts": [{
                        "text": "question"
                    }]
                },
                "final_response": {
                    "parts": [{
                        "text": "expected answer"
                    }]
                },
            }],
        }],
    }
    metrics = [{
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {
            "final_response": {
                "text": {
                    "match": "contains"
                }
            }
        },
    }]

    result = pipeline._run_fake_split(eval_set, metrics)
    case = result.cases[0]

    assert case.passed is False
    assert case.trace["expected"] == "expected answer"
    assert case.trace["actual"] == ""
    assert case.evaluator_metadata["actual_final_response"] == ""


def _path_like_values_are_relative(value: Any) -> bool:
    return not list(_absolute_path_like_values(value))


def _absolute_path_like_values(value: Any, key: str = "") -> list[str]:
    if isinstance(value, dict):
        absolute_paths = []
        for child_key, child_value in value.items():
            absolute_paths.extend(_absolute_path_like_values(child_value, str(child_key)))
        return absolute_paths
    if isinstance(value, list):
        absolute_paths = []
        for item in value:
            absolute_paths.extend(_absolute_path_like_values(item, key))
        return absolute_paths
    if isinstance(value, str) and _is_path_like_key(key) and Path(value).is_absolute():
        return [value]
    return []


def _is_path_like_key(key: str) -> bool:
    return key in {"example_root", "output_dir", "optimizer_artifact_dir"} or key.endswith(("_path", "_paths"))


def _relative_to_example(path: Path, example_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(example_root.resolve()))
    except ValueError:
        return os.path.relpath(path.resolve(), example_root.resolve())
