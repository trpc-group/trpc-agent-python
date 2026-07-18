# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Tests for Stage 4 real optimization and guarded prompt writeback."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop import candidate_provider as candidate_provider_module
from examples.optimization.eval_optimize_loop import writeback as writeback_module
from examples.optimization.eval_optimize_loop.candidate_provider import AgentOptimizerCandidateProvider
from examples.optimization.eval_optimize_loop.candidate_provider import CandidateProviderError
from examples.optimization.eval_optimize_loop.candidate_provider import CandidateRequest
from examples.optimization.eval_optimize_loop.config import WritebackConfig
from examples.optimization.eval_optimize_loop.fake import DeterministicFakeAgent
from examples.optimization.eval_optimize_loop.fake import DeterministicFakeCandidateProvider
from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.pipeline import FakeStageExecutionError
from examples.optimization.eval_optimize_loop.pipeline import run_fake_stage
from examples.optimization.eval_optimize_loop.pipeline import run_real_stage
from examples.optimization.eval_optimize_loop.schemas import GateDecision
from examples.optimization.eval_optimize_loop.schemas import RealStageResult
from examples.optimization.eval_optimize_loop.writeback import perform_writeback
from trpc_agent_sdk.evaluation import OptimizeResult


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"


def _copy_example(tmp_path: Path, name: str = "eval_optimize_loop") -> Path:
    target = tmp_path / name
    shutil.copytree(_EXAMPLE, target, ignore=shutil.ignore_patterns("runs", "__pycache__"))
    return target


def _set_real_mode(root: Path, *, writeback: bool = False) -> None:
    path = root / "pipeline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["execution"]["mode"] = "real"
    payload["writeback"]["enabled"] = writeback
    path.write_text(json.dumps(payload), encoding="utf-8")


def _optimize_result(
    baseline_prompts: dict[str, str],
    best_prompts: dict[str, str],
    *,
    status: str = "SUCCEEDED",
) -> OptimizeResult:
    return OptimizeResult(
        algorithm="gepa_reflective",
        status=status,
        finish_reason="completed" if status == "SUCCEEDED" else "error",
        error_message="" if status == "SUCCEEDED" else "optimizer failed",
        baseline_pass_rate=1 / 3,
        best_pass_rate=1.0,
        pass_rate_improvement=2 / 3,
        baseline_prompts=baseline_prompts,
        best_prompts=best_prompts,
        total_rounds=0,
        rounds=[],
        total_reflection_lm_calls=1,
        total_llm_cost=0.25,
        total_token_usage={"prompt": 10, "completion": 5, "total": 15},
        duration_seconds=0.5,
        started_at="2026-07-17T00:00:00Z",
        finished_at="2026-07-17T00:00:01Z",
    )


def _install_optimizer_result(
    monkeypatch: pytest.MonkeyPatch,
    baseline: dict[str, str],
    best: dict[str, str],
) -> None:
    async def fake_optimize(**kwargs):
        output_dir = Path(str(kwargs["output_dir"]))
        output_dir.mkdir(parents=True)
        result = _optimize_result(baseline, best)
        result.dump_to(str(output_dir / "result.json"))
        return result

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.candidate_provider.AgentOptimizer.optimize",
        fake_optimize,
    )


@pytest.mark.asyncio
async def test_real_candidate_provider_uses_isolated_target_and_never_updates_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_provider")
    baseline = await prepared.working_target.read_all()
    best = {name: f"{content}\noptimized" for name, content in baseline.items()}
    captured: dict[str, object] = {}

    async def call_agent(query: str) -> str:
        return query

    async def fake_optimize(**kwargs):
        captured.update(kwargs)
        output_dir = Path(str(kwargs["output_dir"]))
        output_dir.mkdir(parents=True)
        result = _optimize_result(baseline, best)
        result.dump_to(str(output_dir / "result.json"))
        return result

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.candidate_provider.AgentOptimizer.optimize",
        fake_optimize,
    )
    request = CandidateRequest(
        current_prompts=baseline,
        target_prompt=prepared.working_target,
        optimizer_config_path=Path(prepared.input_snapshot.optimizer_config_path),
        train_evalset_path=Path(prepared.input_snapshot.train_evalset_path),
        validation_evalset_path=Path(prepared.input_snapshot.validation_evalset_path),
        output_dir=Path(prepared.workspace.run_dir) / "optimizer",
        seed=prepared.input_snapshot.seed,
    )

    generated = await AgentOptimizerCandidateProvider(call_agent).propose(request)

    assert captured["target_prompt"] is prepared.working_target
    assert captured["update_source"] is False
    assert captured["verbose"] == 0
    assert generated.proposal.provider == "agent_optimizer"
    assert generated.proposal.prompts == best
    assert generated.proposal.changed_fields == ["system_prompt"]
    assert generated.proposal.candidate_id.startswith("optimizer-")
    assert generated.optimize_result == _optimize_result(baseline, best)
    assert (request.output_dir / "result.json").is_file()
    assert await prepared.working_target.read_all() == baseline


@pytest.mark.asyncio
async def test_real_stage_runs_full_regression_and_skips_disabled_writeback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "real_stage")
    _set_real_mode(root)
    source_path = root / "prompts" / "system.md"
    source_before = source_path.read_text(encoding="utf-8")
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_real")
    baseline = await prepared.working_target.read_all()
    best = DeterministicFakeCandidateProvider().propose(
        baseline,
        scenario="improve",
        seed=42,
    ).prompts

    async def fake_optimize(**kwargs):
        output_dir = Path(str(kwargs["output_dir"]))
        output_dir.mkdir(parents=True)
        result = _optimize_result(baseline, best)
        result.dump_to(str(output_dir / "result.json"))
        return result

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.candidate_provider.AgentOptimizer.optimize",
        fake_optimize,
    )
    agent = DeterministicFakeAgent(prepared.working_target)

    result = await run_real_stage(prepared, call_agent=agent.call_agent)

    assert isinstance(result, RealStageResult)
    assert result.baseline_train.passed_case_count == 1
    assert result.baseline_validation.passed_case_count == 1
    assert result.candidate_train.passed_case_count == 3
    assert result.candidate_validation.passed_case_count == 3
    assert result.gate_decision.decision == "accept"
    assert result.writeback.status == "skipped"
    assert result.writeback.reason == "disabled"
    assert result.optimize_result.best_prompts == best
    assert source_path.read_text(encoding="utf-8") == source_before
    assert await prepared.working_target.read_all() == best
    assert RealStageResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.asyncio
async def test_real_stage_writes_accepted_candidate_after_hash_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "write_accepted")
    _set_real_mode(root, writeback=True)
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_write")
    baseline = await prepared.working_target.read_all()
    best = DeterministicFakeCandidateProvider().propose(
        baseline,
        scenario="improve",
        seed=42,
    ).prompts
    _install_optimizer_result(monkeypatch, baseline, best)

    result = await run_real_stage(
        prepared,
        call_agent=DeterministicFakeAgent(prepared.working_target).call_agent,
    )

    assert result.gate_decision.decision == "accept"
    assert result.writeback.status == "written"
    assert result.writeback.reason == "written"
    assert result.writeback.attempted is True
    assert result.writeback.changed_fields == ["system_prompt"]
    assert await prepared.source_target.read_all() == best
    assert result.writeback.source_hashes_before != result.writeback.source_hashes_after


@pytest.mark.asyncio
async def test_real_stage_blocks_writeback_when_source_drifted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "source_drift")
    _set_real_mode(root, writeback=True)
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_drift")
    baseline = await prepared.working_target.read_all()
    best = DeterministicFakeCandidateProvider().propose(
        baseline,
        scenario="improve",
        seed=42,
    ).prompts
    _install_optimizer_result(monkeypatch, baseline, best)
    source_path = root / "prompts" / "system.md"
    source_path.write_text("concurrent user edit", encoding="utf-8")

    result = await run_real_stage(
        prepared,
        call_agent=DeterministicFakeAgent(prepared.working_target).call_agent,
    )

    assert result.gate_decision.decision == "accept"
    assert result.writeback.status == "blocked"
    assert result.writeback.reason == "source_drift"
    assert result.writeback.attempted is False
    assert source_path.read_text(encoding="utf-8") == "concurrent user edit"


@pytest.mark.asyncio
async def test_real_stage_returns_failed_writeback_after_verified_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "write_failure")
    _set_real_mode(root, writeback=True)
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_write_failure")
    baseline = await prepared.working_target.read_all()
    best = DeterministicFakeCandidateProvider().propose(
        baseline,
        scenario="improve",
        seed=42,
    ).prompts
    _install_optimizer_result(monkeypatch, baseline, best)

    async def fail_write(prompts: dict[str, str]) -> None:
        raise OSError("simulated source write failure")

    monkeypatch.setattr(prepared.source_target, "write_all", fail_write)

    result = await run_real_stage(
        prepared,
        call_agent=DeterministicFakeAgent(prepared.working_target).call_agent,
    )

    assert result.writeback.status == "failed"
    assert result.writeback.reason == "write_error"
    assert result.writeback.attempted is True
    assert "simulated source write failure" in (result.writeback.error_message or "")
    assert await prepared.source_target.read_all() == baseline


@pytest.mark.asyncio
async def test_fake_stage_uses_the_same_guarded_writeback_path(tmp_path: Path):
    root = _copy_example(tmp_path, "fake_writeback")
    path = root / "pipeline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["writeback"]["enabled"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_fake_write")

    result = await run_fake_stage(prepared, scenario="improve")

    assert result.gate_decision.decision == "accept"
    assert result.writeback.status == "written"
    assert await prepared.source_target.read_all() == result.candidate.prompts


@pytest.mark.asyncio
async def test_fake_stage_never_writes_a_gate_rejected_candidate(tmp_path: Path):
    root = _copy_example(tmp_path, "fake_reject")
    path = root / "pipeline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["writeback"]["enabled"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_fake_reject")
    source_before = await prepared.source_target.read_all()

    result = await run_fake_stage(prepared, scenario="no_improvement")

    assert result.gate_decision.decision == "reject"
    assert result.writeback.status == "skipped"
    assert result.writeback.reason == "gate_rejected"
    assert await prepared.source_target.read_all() == source_before


@pytest.mark.asyncio
async def test_real_candidate_provider_rejects_failed_optimizer_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "optimizer_failed")
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_optimizer_failed")
    baseline = await prepared.working_target.read_all()

    async def failed_optimize(**kwargs):
        return _optimize_result(baseline, baseline, status="FAILED")

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.candidate_provider.AgentOptimizer.optimize",
        failed_optimize,
    )
    request = CandidateRequest(
        current_prompts=baseline,
        target_prompt=prepared.working_target,
        optimizer_config_path=Path(prepared.input_snapshot.optimizer_config_path),
        train_evalset_path=Path(prepared.input_snapshot.train_evalset_path),
        validation_evalset_path=Path(prepared.input_snapshot.validation_evalset_path),
        output_dir=Path(prepared.workspace.run_dir) / "optimizer",
        seed=42,
    )

    with pytest.raises(CandidateProviderError, match="returned FAILED"):
        await AgentOptimizerCandidateProvider(DeterministicFakeAgent(prepared.working_target).call_agent).propose(
            request
        )


@pytest.mark.asyncio
async def test_real_stage_rejects_optimizer_config_drift_before_optimization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "optimizer_drift")
    _set_real_mode(root)
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_optimizer_drift")
    optimizer_path = root / "optimizer.json"
    optimizer_path.write_text(
        optimizer_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    called = False

    async def should_not_optimize(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("optimizer must not run after config drift")

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.candidate_provider.AgentOptimizer.optimize",
        should_not_optimize,
    )

    with pytest.raises(FakeStageExecutionError, match="optimizer_config changed"):
        await run_real_stage(
            prepared,
            call_agent=DeterministicFakeAgent(prepared.working_target).call_agent,
        )
    assert called is False


@pytest.mark.asyncio
async def test_writeback_readback_mismatch_restores_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "readback_mismatch")
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_readback")
    baseline = await prepared.source_target.read_all()
    candidate = DeterministicFakeCandidateProvider().propose(
        baseline,
        scenario="improve",
        seed=42,
    )
    original_read_all = prepared.source_target.read_all
    read_count = 0

    async def mismatched_readback():
        nonlocal read_count
        read_count += 1
        actual = await original_read_all()
        if read_count == 2:
            return {"system_prompt": "unexpected readback"}
        return actual

    monkeypatch.setattr(prepared.source_target, "read_all", mismatched_readback)

    result = await perform_writeback(
        decision=GateDecision(decision="accept", rule_results=[]),
        config=WritebackConfig(enabled=True),
        snapshots=prepared.input_snapshot.prompt_snapshots,
        source_target=prepared.source_target,
        candidate=candidate,
    )

    assert result.status == "failed"
    assert result.reason == "readback_mismatch"
    assert await original_read_all() == baseline


def test_enabled_writeback_cannot_disable_source_hash_guard():
    with pytest.raises(ValueError, match="requires require_source_hash_match=true"):
        WritebackConfig(enabled=True, require_source_hash_match=False)


@pytest.mark.asyncio
async def test_real_stage_restores_working_prompt_after_optimizer_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "optimizer_exception")
    _set_real_mode(root)
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_optimizer_exception")
    baseline = await prepared.working_target.read_all()
    source_before = await prepared.source_target.read_all()
    changed = {name: f"{value}\nleft by failed optimizer" for name, value in baseline.items()}

    async def mutating_failure(**kwargs):
        await kwargs["target_prompt"].write_all(changed)
        raise RuntimeError("simulated optimizer crash")

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.candidate_provider.AgentOptimizer.optimize",
        mutating_failure,
    )

    with pytest.raises(FakeStageExecutionError, match="real candidate generation failed"):
        await run_real_stage(
            prepared,
            call_agent=DeterministicFakeAgent(prepared.working_target).call_agent,
        )

    assert await prepared.working_target.read_all() == baseline
    assert await prepared.source_target.read_all() == source_before


@pytest.mark.asyncio
async def test_real_candidate_provider_can_discard_native_artifacts_after_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "discard_artifacts")
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_discard_artifacts")
    baseline = await prepared.working_target.read_all()
    best = {name: f"{content}\noptimized" for name, content in baseline.items()}
    _install_optimizer_result(monkeypatch, baseline, best)
    output_dir = Path(prepared.workspace.run_dir) / "optimizer"
    request = CandidateRequest(
        current_prompts=baseline,
        target_prompt=prepared.working_target,
        optimizer_config_path=Path(prepared.input_snapshot.optimizer_config_path),
        train_evalset_path=Path(prepared.input_snapshot.train_evalset_path),
        validation_evalset_path=Path(prepared.input_snapshot.validation_evalset_path),
        output_dir=output_dir,
        seed=42,
        retain_native_artifacts=False,
    )

    generated = await AgentOptimizerCandidateProvider(
        DeterministicFakeAgent(prepared.working_target).call_agent
    ).propose(request)

    assert generated.optimize_result is not None
    assert generated.proposal.optimizer_output_dir is None
    assert not output_dir.exists()


def test_cli_explains_that_real_mode_requires_injected_call_agent(tmp_path: Path):
    root = _copy_example(tmp_path, "real_cli")
    _set_real_mode(root)

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "run_pipeline.py"),
            "--config",
            str(root / "pipeline.json"),
            "--run-id",
            "stage4_real_cli",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "run_real_stage(prepared, call_agent=...)" in completed.stderr
    assert not (root / "runs" / "stage4_real_cli").exists()


@pytest.mark.asyncio
async def test_writeback_blocks_drift_introduced_after_initial_hash_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "late_source_drift")
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_late_drift")
    baseline = await prepared.source_target.read_all()
    candidate = DeterministicFakeCandidateProvider().propose(
        baseline,
        scenario="improve",
        seed=42,
    )
    source_path = root / "prompts" / "system.md"
    original_verify = writeback_module.verify_source_hashes
    checks = 0

    def verify_then_drift(snapshots):
        nonlocal checks
        checks += 1
        original_verify(snapshots)
        if checks == 1:
            source_path.write_text("late concurrent edit", encoding="utf-8")

    monkeypatch.setattr(writeback_module, "verify_source_hashes", verify_then_drift)

    result = await perform_writeback(
        decision=GateDecision(decision="accept", rule_results=[]),
        config=WritebackConfig(enabled=True),
        snapshots=prepared.input_snapshot.prompt_snapshots,
        source_target=prepared.source_target,
        candidate=candidate,
    )

    assert result.status == "blocked"
    assert result.reason == "source_drift"
    assert result.attempted is False
    assert source_path.read_text(encoding="utf-8") == "late concurrent edit"


@pytest.mark.asyncio
async def test_real_candidate_provider_wraps_artifact_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path, "artifact_cleanup_failure")
    prepared = prepare_run(root / "pipeline.json", run_id="stage4_cleanup_failure")
    baseline = await prepared.working_target.read_all()
    best = {name: f"{content}\noptimized" for name, content in baseline.items()}
    _install_optimizer_result(monkeypatch, baseline, best)
    request = CandidateRequest(
        current_prompts=baseline,
        target_prompt=prepared.working_target,
        optimizer_config_path=Path(prepared.input_snapshot.optimizer_config_path),
        train_evalset_path=Path(prepared.input_snapshot.train_evalset_path),
        validation_evalset_path=Path(prepared.input_snapshot.validation_evalset_path),
        output_dir=Path(prepared.workspace.run_dir) / "optimizer",
        seed=42,
        retain_native_artifacts=False,
    )

    def fail_cleanup(path: Path, **kwargs) -> None:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(candidate_provider_module.shutil, "rmtree", fail_cleanup)

    with pytest.raises(CandidateProviderError, match="discard optimizer artifacts"):
        await AgentOptimizerCandidateProvider(
            DeterministicFakeAgent(prepared.working_target).call_agent
        ).propose(request)
