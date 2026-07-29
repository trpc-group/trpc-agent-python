# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Real-mode integration seam tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pipeline import EvalOptimizePipeline

from .conftest import install_fake_evaluation_sdk


@pytest.mark.asyncio
async def test_real_mode_uses_agent_optimizer_without_updating_source(
    example_root: Path,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    captured = install_fake_evaluation_sdk(monkeypatch, example_root)
    output_dir = tmp_path / "real"

    report_paths = await EvalOptimizePipeline(
        train_evalset_path=example_root / "train.evalset.json",
        val_evalset_path=example_root / "val.evalset.json",
        optimizer_config_path=example_root / "optimizer.json",
        gate_config_path=example_root / "gate.json",
        output_dir=output_dir,
        mode="real",
    ).run()

    report = json.loads(report_paths.json_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "eval-optimize-loop-v1"
    assert report["candidate"]["val"]["case_count"] == 3
    assert report["delta"]["summary"]["new_pass_count"] == 2
    assert report["gate_decision"]["decision"] == "reject"
    assert report["optimization"]["rounds"][0]["reason"] == "stub accepted final_answer_mismatch fix"
    assert captured["update_source"] is False
    assert sorted(captured["target_prompt"].names()) == ["skill", "system_prompt"]
    assert Path(captured["train_dataset_path"]).parent == output_dir / "optimizer_inputs"
    assert Path(captured["validation_dataset_path"]).parent == output_dir / "optimizer_inputs"
    train_artifact = report["optimization"]["artifacts"]["train_call_agent_evalset_path"]
    val_artifact = report["optimization"]["artifacts"]["val_call_agent_evalset_path"]
    assert not Path(train_artifact).is_absolute()
    assert not Path(val_artifact).is_absolute()
    assert train_artifact.endswith("optimizer_inputs/train.call_agent.evalset.json")
    assert val_artifact.endswith("optimizer_inputs/val.call_agent.evalset.json")
    assert report["metadata"]["reproduction_command"] == "python run_pipeline.py --mode real"
