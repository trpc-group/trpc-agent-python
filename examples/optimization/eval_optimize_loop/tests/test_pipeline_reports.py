# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end report tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pipeline import EvalOptimizePipeline


@pytest.mark.asyncio
async def test_fake_pipeline_writes_business_reports_with_gate_delta_and_audit(example_root: Any) -> None:
    output_dir = example_root / "output"
    system_before = (example_root / "prompts" / "system.md").read_text(encoding="utf-8")
    skill_before = (example_root / "prompts" / "skill.md").read_text(encoding="utf-8")

    report_paths = await EvalOptimizePipeline(
        train_evalset_path=example_root / "train.evalset.json",
        val_evalset_path=example_root / "val.evalset.json",
        optimizer_config_path=example_root / "optimizer.json",
        gate_config_path=example_root / "gate.json",
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
    assert _contains_no_absolute_workspace_path(report)
    assert report["metadata"]["example_root"] == "."
    assert report["metadata"]["reproduction_command"] == "python run_pipeline.py --mode fake"
    assert report["metadata"]["output_paths"] == {
        "json": "output/optimization_report.json",
        "markdown": "output/optimization_report.md",
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


def _contains_no_absolute_workspace_path(value: Any) -> bool:
    return "/home/kazenke/" not in json.dumps(value, ensure_ascii=False)
