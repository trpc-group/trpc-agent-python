#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Validate optimization report schema and persisted evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


REQUIRED_FROZEN_KEYS = {
    "champion_sha256",
    "challenger_sha256",
    "train_sha256",
    "val_sha256",
    "metric_config_sha256",
    "run_config_sha256",
    "optimizer_config_sha256",
    "seed",
    "started_at",
    "mode",
    "candidate_source",
    "gate_config",
    "model_info",
    "evaluator_info",
    "optimizer_info",
}

REQUIRED_PER_CASE_KEYS = {
    "eval_id",
    "split",
    "slice",
    "risk_level",
    "protected",
    "champion_status",
    "challenger_status",
    "champion_score",
    "challenger_score",
    "delta",
    "category",
    "transition",
    "failure_kind",
    "failure_reason",
    "evidence",
}

REQUIRED_AUDIT_KEYS = {
    "applied",
    "duration_seconds",
    "candidate_source",
    "artifact_dir",
    "before_apply_sha256",
    "after_apply_sha256",
    "repro_cmd",
    "artifacts",
}


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    for k in ("OPENAI_API_KEY", "TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"):
        monkeypatch.delenv(k, raising=False)


async def test_report_has_all_required_fields(loop_root: Path) -> None:
    import pipeline

    await pipeline.amain(["--mode", "fake", "--scenario", "no_effect"])
    report = json.loads((loop_root / "optimization_report.json").read_text(encoding="utf-8"))

    assert report["version"] == "2.0"
    assert REQUIRED_FROZEN_KEYS <= set(report["frozen"].keys())
    assert {"train", "val"} <= set(report["results"].keys())
    for k in ("train", "val"):
        assert {"champion_avg", "challenger_avg", "delta"} <= set(report["results"][k].keys())
    assert "train_delta" in report and "val_delta" in report
    assert "cost_status" in report
    assert "cost" in report
    assert {"accepted", "violated", "reasons"} <= set(report["decision"].keys())
    assert REQUIRED_AUDIT_KEYS <= set(report["audit"].keys())

    assert len(report["per_case"]) == 6
    for c in report["per_case"]:
        assert REQUIRED_PER_CASE_KEYS <= set(c.keys())
        assert c["evidence"]["actual_text"] is not None
        assert c["evidence"]["expected_text"] is not None
        assert c["evidence"]["trace_ref"]
        assert "metric_results" in c["evidence"]

    assert report["frozen"]["mode"] == "fake"
    assert report["frozen"]["candidate_source"] == "candidate_file"


async def test_train_val_delta_consistent(loop_root: Path) -> None:
    import pipeline

    await pipeline.amain(["--mode", "fake", "--scenario", "no_effect"])
    report = json.loads((loop_root / "optimization_report.json").read_text(encoding="utf-8"))

    # report.results.train.delta == report.train_delta
    assert abs(report["results"]["train"]["delta"] - report["train_delta"]) < 1e-9
    assert abs(report["results"]["val"]["delta"] - report["val_delta"]) < 1e-9


async def test_markdown_presented_and_aligned(loop_root: Path) -> None:
    import pipeline

    await pipeline.amain(["--mode", "fake", "--scenario", "no_effect"])
    md = (loop_root / "optimization_report.md").read_text(encoding="utf-8")
    # 必须含核心章节
    assert "决策" in md
    assert "Gate 决策" in md
    assert "逐 case 明细" in md
    assert "审计" in md
    assert "REJECT" in md or "ACCEPT" in md


async def test_artifacts_files_exist(loop_root: Path) -> None:
    import pipeline

    await pipeline.amain(["--mode", "fake", "--scenario", "no_effect"])
    report = json.loads((loop_root / "optimization_report.json").read_text(encoding="utf-8"))
    artifacts = report["audit"]["artifacts"]
    for k, p in artifacts.items():
        path = Path(p)
        assert path.exists(), f"{k} artifact 文件不存在：{p}"


async def test_checked_in_example_report_matches_v2_schema(loop_root: Path) -> None:
    payload = json.loads(
        (loop_root / "optimization_report.example.json").read_text(encoding="utf-8")
    )
    assert payload["version"] == "2.0"
    assert REQUIRED_FROZEN_KEYS <= set(payload["frozen"])
    assert REQUIRED_AUDIT_KEYS <= set(payload["audit"])
    assert REQUIRED_PER_CASE_KEYS <= set(payload["per_case"][0])
    assert payload["optimizer"]["rounds"][0]["artifact_path"]
    categories = {case["category"] for case in payload["per_case"]}
    assert {"tool_call_error", "param_error", "rubric_fail"} <= categories
