# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Stage 5 integration tests for success and failure report orchestration."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from examples.optimization.eval_optimize_loop import pipeline as pipeline_module
from examples.optimization.eval_optimize_loop import run_real_integration
from examples.optimization.eval_optimize_loop.business_agent import BusinessAgent
from examples.optimization.eval_optimize_loop.fake import DeterministicFakeModel
from examples.optimization.eval_optimize_loop.fake import DeterministicFakeCandidateProvider
from examples.optimization.eval_optimize_loop.pipeline import PipelineExecutionError
from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.pipeline import run_offline_stage
from examples.optimization.eval_optimize_loop.pipeline import run_real_stage
from examples.optimization.eval_optimize_loop.schemas import ArtifactIndex
from examples.optimization.eval_optimize_loop.schemas import FailureReport
from examples.optimization.eval_optimize_loop.schemas import OptimizationReport
from examples.optimization.eval_optimize_loop.schemas import OptimizerRuntimeParameters
from trpc_agent_sdk.evaluation import OptimizeResult
from trpc_agent_sdk.evaluation import TargetPrompt


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"


def _offline_agent(target: TargetPrompt) -> BusinessAgent:
    return BusinessAgent(
        target,
        DeterministicFakeModel,
        agent_name="stage5_offline_agent",
        app_name="stage5_offline_test",
        user_id="stage5-test",
    )


def _copy_example(tmp_path: Path, name: str = "eval_optimize_loop") -> Path:
    target = tmp_path / name
    shutil.copytree(
        _EXAMPLE,
        target,
        ignore=shutil.ignore_patterns("runs", "__pycache__"),
    )
    return target


def _set_real_mode(root: Path) -> None:
    path = root / "pipeline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["execution"]["mode"] = "real"
    path.write_text(json.dumps(payload), encoding="utf-8")


def _optimize_result(
    baseline_prompts: dict[str, str],
    best_prompts: dict[str, str],
) -> OptimizeResult:
    return OptimizeResult(
        algorithm="gepa_reflective",
        status="SUCCEEDED",
        finish_reason="completed",
        error_message="",
        baseline_pass_rate=1 / 3,
        best_pass_rate=1.0,
        pass_rate_improvement=2 / 3,
        baseline_prompts=baseline_prompts,
        best_prompts=best_prompts,
        total_rounds=1,
        rounds=[],
        total_reflection_lm_calls=1,
        total_llm_cost=0.25,
        total_token_usage={"prompt": 10, "completion": 5, "total": 15},
        duration_seconds=0.5,
        started_at="2026-07-18T00:00:00Z",
        finished_at="2026-07-18T00:00:01Z",
    )


@pytest.mark.parametrize("scenario", ["improve", "no_improvement", "overfit"])
@pytest.mark.asyncio
async def test_fake_stage_publishes_complete_report_bundle(
    tmp_path: Path,
    scenario: str,
) -> None:
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id=f"stage5_{scenario}")

    result = await run_offline_stage(prepared, scenario=scenario)

    run_dir = Path(prepared.workspace.run_dir)
    report_dir = run_dir / "report"
    report = OptimizationReport.model_validate_json(
        (report_dir / "optimization_report.json").read_text(encoding="utf-8")
    )
    assert report.gate_decision == result.gate_decision
    assert report.candidate == result.candidate
    assert (report_dir / "artifact_index.json").is_file()
    assert not (run_dir / "failure_report.json").exists()


@pytest.mark.asyncio
async def test_real_stage_reports_optimizer_native_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    _set_real_mode(root)
    pipeline_path = root / "pipeline.json"
    pipeline_payload = json.loads(pipeline_path.read_text(encoding="utf-8"))
    pipeline_payload["artifacts"]["copy_input_files"] = False
    pipeline_path.write_text(json.dumps(pipeline_payload), encoding="utf-8")
    optimizer_path = root / "optimizer.json"
    optimizer_payload = json.loads(optimizer_path.read_text(encoding="utf-8"))
    optimizer_payload["optimize"]["algorithm"]["reflection_lm"]["extra_fields"] = {
        "authorization": "Bearer stage5-authorization-secret",
        "token": "stage5-provider-token",
        "client_secret": "stage5-client-secret",
    }
    optimizer_path.write_text(json.dumps(optimizer_payload), encoding="utf-8")
    prepared = prepare_run(root / "pipeline.json", run_id="stage5_real")
    baseline = await prepared.working_target.read_all()
    best = DeterministicFakeCandidateProvider().propose(
        baseline,
        scenario="improve",
        seed=prepared.input_snapshot.seed,
    ).prompts

    async def fake_optimize(**kwargs: object) -> OptimizeResult:
        output_dir = Path(str(kwargs["output_dir"]))
        rounds_dir = output_dir / "rounds"
        rounds_dir.mkdir(parents=True)
        result = _optimize_result(baseline, best)
        result.dump_to(str(output_dir / "result.json"))
        (rounds_dir / "round_001.json").write_text("{}\n", encoding="utf-8")
        return result

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.candidate_provider.AgentOptimizer.optimize",
        fake_optimize,
    )

    result = await run_real_stage(
        prepared,
        call_agent=_offline_agent(prepared.working_target).call_agent,
        optimizer_parameters=OptimizerRuntimeParameters(
            model_name="stage5-reflection-model",
            max_candidate_proposals=1,
        ),
    )

    report_dir = Path(prepared.workspace.run_dir) / "report"
    report = OptimizationReport.model_validate_json(
        (report_dir / "optimization_report.json").read_text(encoding="utf-8")
    )
    index = ArtifactIndex.model_validate_json(
        (report_dir / "artifact_index.json").read_text(encoding="utf-8")
    )
    native_paths = {
        reference.relative_path
        for reference in index.artifacts
        if reference.artifact_type == "optimizer_native"
    }
    assert native_paths == {
        "optimizer.runtime.json",
        "optimizer/result.json",
        "optimizer/rounds/round_001.json",
    }
    runtime_text = (
        Path(prepared.workspace.run_dir) / "optimizer.runtime.json"
    ).read_text(encoding="utf-8")
    for secret in (
        "stage5-authorization-secret",
        "stage5-provider-token",
        "stage5-client-secret",
    ):
        assert secret not in runtime_text
    assert report.candidate == result.candidate
    assert report.optimizer_resources.total_rounds.status == "available"
    assert report.optimizer_resources.reflection_lm_calls.status == "available"
    assert report.optimizer_resources.duration_seconds.status == "available"


_FAILURE_CASES = [
    ("_evaluate_split", "baseline_train"),
    ("FakeCandidateProviderAdapter.propose", "candidate_generation"),
    ("build_evaluation_analysis", "analysis"),
    ("evaluate_gate", "gate"),
    ("perform_writeback", "writeback"),
    ("publish_report_bundle", "reporting"),
]


def _install_stage_failure(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    def fail_sync(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated stage failure")

    async def fail_async(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated stage failure")

    if target == "FakeCandidateProviderAdapter.propose":
        monkeypatch.setattr(
            pipeline_module.FakeCandidateProviderAdapter,
            "propose",
            fail_async,
        )
    elif target in {"_evaluate_split", "perform_writeback"}:
        monkeypatch.setattr(pipeline_module, target, fail_async)
    else:
        monkeypatch.setattr(pipeline_module, target, fail_sync, raising=False)


@pytest.mark.parametrize(("target", "expected_phase"), _FAILURE_CASES)
@pytest.mark.asyncio
async def test_fake_stage_writes_phase_specific_failure_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    expected_phase: str,
) -> None:
    root = _copy_example(tmp_path)
    prepared = prepare_run(
        root / "pipeline.json",
        run_id=f"stage5_failure_{expected_phase}",
    )
    _install_stage_failure(monkeypatch, target)

    with pytest.raises(Exception, match="simulated stage failure"):
        await run_offline_stage(prepared, scenario="improve")

    run_dir = Path(prepared.workspace.run_dir)
    failure = FailureReport.model_validate_json(
        (run_dir / "failure_report.json").read_text(encoding="utf-8")
    )
    assert failure.failed_phase == expected_phase
    assert failure.exception_type in {
        "RuntimeError",
        "PipelineExecutionError",
        "ArtifactWriteError",
    }
    assert "simulated stage failure" in failure.error_message
    assert not (run_dir / "report").exists()


@pytest.mark.asyncio
async def test_failure_report_error_preserves_original_exception_as_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="stage5_double_failure")

    async def fail_evaluation(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated stage failure")

    def fail_failure_report(*args: object, **kwargs: object) -> None:
        raise RuntimeError("failed to write failure report")

    monkeypatch.setattr(pipeline_module, "_evaluate_split", fail_evaluation)
    monkeypatch.setattr(
        pipeline_module,
        "write_failure_report",
        fail_failure_report,
        raising=False,
    )

    with pytest.raises(PipelineExecutionError) as raised:
        await run_offline_stage(prepared, scenario="improve")

    assert "simulated stage failure" in str(raised.value)
    assert "failed to write failure report" in str(raised.value)
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert str(raised.value.__cause__) == "simulated stage failure"


@pytest.mark.asyncio
async def test_reporting_failure_rolls_back_successful_source_writeback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    config_path = root / "pipeline.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["writeback"]["enabled"] = True
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    prepared = prepare_run(config_path, run_id="stage5_reporting_rollback")
    source_before = await prepared.source_target.read_all()

    def fail_report(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated reporting failure")

    monkeypatch.setattr(pipeline_module, "publish_report_bundle", fail_report)

    with pytest.raises(RuntimeError, match="simulated reporting failure"):
        await run_offline_stage(prepared, scenario="improve")

    assert await prepared.source_target.read_all() == source_before
    failure = FailureReport.model_validate_json(
        (Path(prepared.workspace.run_dir) / "failure_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure.failed_phase == "reporting"


@pytest.mark.asyncio
async def test_reporting_and_writeback_rollback_failure_are_both_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    config_path = root / "pipeline.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["writeback"]["enabled"] = True
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    prepared = prepare_run(config_path, run_id="stage5_reporting_rollback_failure")

    def fail_report(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated reporting failure")

    async def fail_rollback(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated source rollback failure")

    monkeypatch.setattr(pipeline_module, "publish_report_bundle", fail_report)
    monkeypatch.setattr(pipeline_module, "_rollback_written_source", fail_rollback)

    with pytest.raises(PipelineExecutionError) as raised:
        await run_offline_stage(prepared, scenario="improve")

    assert "simulated reporting failure" in str(raised.value)
    assert "simulated source rollback failure" in str(raised.value)
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert str(raised.value.__cause__) == "simulated reporting failure"


@pytest.mark.asyncio
async def test_failure_report_remains_writable_when_source_prompt_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="stage5_source_unreadable")
    progress = pipeline_module._MutableReportProgress(
        started_at=pipeline_module.datetime.now(pipeline_module.timezone.utc)
    )

    async def fail_source_read() -> dict[str, str]:
        raise OSError("simulated unreadable source Prompt")

    monkeypatch.setattr(prepared.source_target, "read_all", fail_source_read)

    await pipeline_module._record_failure(
        prepared,
        progress,
        RuntimeError("simulated pipeline failure"),
    )

    failure = FailureReport.model_validate_json(
        (Path(prepared.workspace.run_dir) / "failure_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure.source_prompt_hashes == {}


def test_fake_cli_prints_report_paths(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            str(_EXAMPLE / "run_pipeline.py"),
            "--config",
            str(root / "pipeline.json"),
            "--run-id",
            "stage5_cli",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "optimization_report.json" in completed.stdout
    assert "optimization_report.md" in completed.stdout
    assert "artifact_index.json" in completed.stdout


@pytest.mark.asyncio
async def test_all_fake_scenarios_duration_is_under_three_minutes_with_reports(
    tmp_path: Path,
) -> None:
    started_at = time.perf_counter()

    for scenario in ("improve", "no_improvement", "overfit"):
        root = _copy_example(tmp_path, name=f"eval_optimize_loop_{scenario}")
        prepared = prepare_run(root / "pipeline.json", run_id=f"stage5_{scenario}")

        await run_offline_stage(prepared, scenario=scenario)

        report_dir = Path(prepared.workspace.run_dir) / "report"
        assert (report_dir / "optimization_report.json").is_file()
        assert (report_dir / "optimization_report.md").is_file()
        assert (report_dir / "artifact_index.json").is_file()

    assert time.perf_counter() - started_at < 180.0


def test_real_cli_gate_reject_prints_report_paths_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = SimpleNamespace(
        passed_case_count=1,
        total_case_count=3,
        average_score=1 / 3,
    )
    result = SimpleNamespace(
        baseline_train=snapshot,
        baseline_validation=snapshot,
        candidate_train=snapshot,
        candidate_validation=snapshot,
        optimize_result=SimpleNamespace(status="SUCCEEDED", total_rounds=1),
        candidate=SimpleNamespace(candidate_id="optimizer-stage5"),
        gate_decision=SimpleNamespace(
            decision="reject",
            rejection_reasons=["no improvement"],
        ),
        writeback=SimpleNamespace(status="skipped", reason="gate_rejected"),
    )
    prepared = SimpleNamespace(
        workspace=SimpleNamespace(run_dir="runs/stage5_real_cli")
    )

    async def complete(*args: object, **kwargs: object) -> tuple[object, object]:
        return prepared, result

    monkeypatch.setenv("TRPC_AGENT_API_KEY", "key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "https://example.test")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "business-model")
    monkeypatch.setattr(run_real_integration, "_run", complete)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_real_integration.py",
            "--run-real",
            "--optimizer-model-name",
            "optimizer-model",
        ],
    )

    assert run_real_integration.main() == 0
    output = capsys.readouterr().out
    assert "Gate decision: REJECT" in output
    assert "optimization_report.json" in output
    assert "optimization_report.md" in output
    assert "artifact_index.json" in output
