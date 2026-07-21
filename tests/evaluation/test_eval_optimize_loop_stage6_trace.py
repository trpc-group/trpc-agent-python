# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Stage 6 tests for deterministic SDK Trace replay."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop import pipeline as pipeline_module
from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.pipeline import run_trace_stage
from examples.optimization.eval_optimize_loop.schemas import ArtifactIndex
from examples.optimization.eval_optimize_loop.schemas import OptimizationReport


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"


def _copy_example(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(
        _EXAMPLE,
        target,
        ignore=shutil.ignore_patterns("runs", "__pycache__"),
    )
    return target


@pytest.mark.parametrize(
    ("scenario", "train_passed", "validation_passed", "decision"),
    [
        ("improve", 3, 3, "accept"),
        ("no_improvement", 1, 1, "reject"),
        ("overfit", 3, 0, "reject"),
    ],
)
@pytest.mark.asyncio
async def test_trace_stage_replays_four_evalsets_without_writeback(
    tmp_path: Path,
    scenario: str,
    train_passed: int,
    validation_passed: int,
    decision: str,
    monkeypatch: pytest.MonkeyPatch,
):
    class _ForbiddenComponent:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("trace mode must not construct runtime components")

    monkeypatch.setattr(pipeline_module, "BusinessAgent", _ForbiddenComponent)
    monkeypatch.setattr(
        pipeline_module,
        "FakeCandidateProviderAdapter",
        _ForbiddenComponent,
    )
    root = _copy_example(tmp_path, f"trace_{scenario}")
    source = root / "prompts" / "system.md"
    baseline_source = source.read_text(encoding="utf-8")
    prepared = prepare_run(
        root / "pipeline.trace.json",
        run_id=f"trace_{scenario}",
    )
    baseline_working = await prepared.working_target.read_all()

    result = await run_trace_stage(
        prepared,
        scenario=scenario,  # type: ignore[arg-type]
    )

    assert result.baseline_train.passed_case_count == 1
    assert result.baseline_validation.passed_case_count == 1
    assert result.candidate_train.passed_case_count == train_passed
    assert result.candidate_validation.passed_case_count == validation_passed
    assert result.gate_decision.decision == decision
    assert result.writeback.status == "skipped"
    assert result.writeback.reason == "trace_replay"
    assert result.writeback.attempted is False
    assert source.read_text(encoding="utf-8") == baseline_source
    assert await prepared.working_target.read_all() == baseline_working
    assert len(result.analysis.train_diff.cases) == 3
    assert len(result.analysis.validation_diff.cases) == 3
    report_dir = Path(prepared.workspace.run_dir) / "report"
    report = OptimizationReport.model_validate_json(
        (report_dir / "optimization_report.json").read_text(encoding="utf-8")
    )
    index = ArtifactIndex.model_validate_json(
        (report_dir / "artifact_index.json").read_text(encoding="utf-8")
    )
    assert report.execution_mode == "trace"
    artifact_ids = {artifact.artifact_id for artifact in index.artifacts}
    assert "input.trace.candidate_train" in artifact_ids
    assert "input.trace.candidate_validation" in artifact_ids
