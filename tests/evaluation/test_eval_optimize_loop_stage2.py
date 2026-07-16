# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Offline tests for the deterministic stage-two eval/optimization loop."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.fake import DeterministicFakeAgent
from examples.optimization.eval_optimize_loop.fake import DeterministicFakeCandidateProvider
from examples.optimization.eval_optimize_loop.pipeline import FakeStageExecutionError
from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.pipeline import run_fake_stage
from examples.optimization.eval_optimize_loop.schemas import FakeStageResult
from trpc_agent_sdk.evaluation import EvalStatus
from trpc_agent_sdk.evaluation import TargetPrompt


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"


def _copy_example(tmp_path: Path, name: str = "eval_optimize_loop") -> Path:
    target = tmp_path / name
    shutil.copytree(_EXAMPLE, target, ignore=shutil.ignore_patterns("runs", "__pycache__"))
    return target


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _target_for_prompt(path: Path, content: str) -> TargetPrompt:
    path.write_text(content, encoding="utf-8")
    return TargetPrompt().add_path("system_prompt", str(path))


def test_fake_candidate_provider_is_pure_deterministic_and_preserves_fields():
    provider = DeterministicFakeCandidateProvider()
    current = {"system_prompt": "baseline", "skill": "unchanged"}
    original = dict(current)

    first = provider.propose(current, scenario="improve", seed=42)
    second = provider.propose(current, scenario="improve", seed=42)

    assert first == second
    assert current == original
    assert first.changed_fields == ["system_prompt"]
    assert first.prompts["skill"] == "unchanged"
    assert first.parent_prompt_sha256 != first.candidate_prompt_sha256
    assert first.candidate_id == f"fake-improve-{first.candidate_prompt_sha256[:12]}"


def test_fake_candidate_provider_rejects_missing_field_and_unknown_scenario():
    provider = DeterministicFakeCandidateProvider()
    with pytest.raises(ValueError, match="target field is missing"):
        provider.propose({"skill": "x"}, scenario="improve", seed=42)
    with pytest.raises(ValueError, match="unknown fake candidate scenario"):
        provider.propose(
            {"system_prompt": "x"},
            scenario="unknown",  # type: ignore[arg-type]
            seed=42,
        )


def test_overfit_candidate_contains_rules_not_eval_ids_or_full_examples():
    proposal = DeterministicFakeCandidateProvider().propose(
        {"system_prompt": "baseline"},
        scenario="overfit",
        seed=42,
    )
    prompt = proposal.prompts["system_prompt"]
    for forbidden in (
        "train_output_format",
        "train_tool_choice",
        "train_tool_arguments",
        "How can I update my email address?",
        "A100",
        "B-204",
    ):
        assert forbidden not in prompt
    assert "account_terms=email" in prompt
    assert "order_lookup=true" in prompt
    assert "refund_route=false" in prompt


@pytest.mark.asyncio
async def test_fake_agent_rereads_prompt_and_is_deterministic(tmp_path: Path):
    target = _target_for_prompt(tmp_path / "system.md", "baseline")
    agent = DeterministicFakeAgent(target)
    email_query = "How can I update my email address?"
    order_query = "Check the status of order A100."

    assert await agent.call_agent(email_query) == (
        '{"route":"account","message":"Open profile settings to update your email."}'
    )
    baseline_order = await agent.call_agent(order_query)
    assert '"route":"general_support"' in baseline_order

    proposal = DeterministicFakeCandidateProvider().propose(
        await target.read_all(),
        scenario="improve",
        seed=42,
    )
    await target.write_all(proposal.prompts)
    expected_order = '{"route":"order_lookup","message":"Checking order A100."}'
    assert await agent.call_agent(order_query) == expected_order
    assert await asyncio.gather(*(agent.call_agent(order_query) for _ in range(8))) == [expected_order] * 8


@pytest.mark.asyncio
async def test_fake_agent_unknown_query_has_stable_fallback(tmp_path: Path):
    target = _target_for_prompt(tmp_path / "system.md", "baseline")
    agent = DeterministicFakeAgent(target)
    expected = (
        '{"route":"general_support","message":'
        '"Please provide more details so I can route your request."}'
    )
    assert await agent.call_agent("Tell me a joke") == expected
    assert await agent.call_agent("  Tell   me a JOKE  ") == expected
    with pytest.raises(TypeError, match="query must be a string"):
        await agent.call_agent(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("scenario", "candidate_train_passed", "candidate_validation_passed"),
    [
        ("improve", 3, 3),
        ("no_improvement", 1, 1),
        ("overfit", 3, 0),
    ],
)
@pytest.mark.asyncio
async def test_fake_stage_scenario_matrix(
    tmp_path: Path,
    scenario: str,
    candidate_train_passed: int,
    candidate_validation_passed: int,
):
    root = _copy_example(tmp_path, scenario)
    source = root / "prompts" / "system.md"
    baseline_source = source.read_text(encoding="utf-8")
    prepared = prepare_run(root / "pipeline.json", run_id=f"stage2_{scenario}")

    result = await run_fake_stage(prepared, scenario=scenario)  # type: ignore[arg-type]

    assert result.baseline_train.passed_case_count == 1
    assert result.baseline_validation.passed_case_count == 1
    assert result.candidate_train.passed_case_count == candidate_train_passed
    assert result.candidate_validation.passed_case_count == candidate_validation_passed
    assert result.baseline_train.average_score == pytest.approx(1 / 3)
    assert result.baseline_validation.average_score == pytest.approx(1 / 3)
    assert len(result.baseline_train.eval_results_by_eval_id) == 3
    assert len(result.baseline_validation.eval_results_by_eval_id) == 3
    assert result.baseline_train.failed_summary is not None
    assert result.baseline_validation.failed_summary is not None
    assert source.read_text(encoding="utf-8") == baseline_source
    assert await prepared.working_target.read_all() == result.candidate.prompts

    if scenario == "improve":
        assert result.candidate_train.failed_summary is None
        assert result.candidate_validation.failed_summary is None
        restored = FakeStageResult.model_validate_json(result.model_dump_json())
        assert restored.candidate.candidate_id == result.candidate.candidate_id
    elif scenario == "overfit":
        baseline_refund = result.baseline_validation.eval_results_by_eval_id["val_refund_route"][0]
        candidate_refund = result.candidate_validation.eval_results_by_eval_id["val_refund_route"][0]
        assert baseline_refund.final_eval_status == EvalStatus.PASSED
        assert candidate_refund.final_eval_status == EvalStatus.FAILED


@pytest.mark.asyncio
async def test_fake_stage_keeps_all_configured_runs(tmp_path: Path):
    root = _copy_example(tmp_path)
    optimizer_path = root / "optimizer.json"
    optimizer = _read_json(optimizer_path)
    optimizer["evaluate"]["num_runs"] = 2
    _write_json(optimizer_path, optimizer)
    prepared = prepare_run(root / "pipeline.json", run_id="two_runs")

    result = await run_fake_stage(prepared, scenario="improve")

    for snapshot in (
        result.baseline_train,
        result.baseline_validation,
        result.candidate_train,
        result.candidate_validation,
    ):
        assert all(len(runs) == 2 for runs in snapshot.eval_results_by_eval_id.values())


@pytest.mark.asyncio
async def test_fake_stage_rejects_unimplemented_fake_judge(tmp_path: Path):
    root = _copy_example(tmp_path)
    config_path = root / "pipeline.json"
    config = _read_json(config_path)
    config["execution"]["use_fake_judge"] = True
    _write_json(config_path, config)
    prepared = prepare_run(config_path, run_id="fake_judge")

    with pytest.raises(FakeStageExecutionError, match="use_fake_judge=true"):
        await run_fake_stage(prepared)


@pytest.mark.parametrize("mode", ["real", "trace"])
@pytest.mark.asyncio
async def test_fake_stage_rejects_non_fake_execution_modes(tmp_path: Path, mode: str):
    root = _copy_example(tmp_path, mode)
    config_path = root / "pipeline.json"
    config = _read_json(config_path)
    config["execution"]["mode"] = mode
    _write_json(config_path, config)
    prepared = prepare_run(config_path, run_id=f"mode_{mode}")

    with pytest.raises(
        FakeStageExecutionError,
        match=rf"requires execution.mode='fake', got '{mode}'",
    ):
        await run_fake_stage(prepared)


@pytest.mark.parametrize(
    ("relative_path", "expected_label"),
    [
        ("data/train.evalset.json", "train_evalset"),
        ("data/val.evalset.json", "validation_evalset"),
    ],
)
@pytest.mark.asyncio
async def test_fake_stage_rejects_evalset_changes_after_prepare_run(
    tmp_path: Path,
    relative_path: str,
    expected_label: str,
):
    root = _copy_example(tmp_path, expected_label)
    prepared = prepare_run(root / "pipeline.json", run_id=f"changed_{expected_label}")
    evalset_path = root / relative_path
    evalset = _read_json(evalset_path)
    evalset["description"] = "changed after prepare_run"
    _write_json(evalset_path, evalset)

    with pytest.raises(
        FakeStageExecutionError,
        match=rf"{expected_label} changed after prepare_run",
    ):
        await run_fake_stage(prepared)


@pytest.mark.asyncio
async def test_fake_stage_wraps_baseline_evaluator_errors(tmp_path: Path, monkeypatch):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="evaluator_error")

    async def fail_evaluation(*_args, **_kwargs):
        raise RuntimeError("injected evaluator failure")

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.pipeline.AgentEvaluator.evaluate_eval_set",
        fail_evaluation,
    )
    with pytest.raises(FakeStageExecutionError, match="baseline train evaluation failed"):
        await run_fake_stage(prepared)
    assert await prepared.working_target.read_all() == {
        snapshot.field_name: snapshot.content for snapshot in prepared.input_snapshot.prompt_snapshots
    }


@pytest.mark.asyncio
async def test_candidate_evaluation_error_retains_working_candidate(tmp_path: Path, monkeypatch):
    root = _copy_example(tmp_path)
    source = root / "prompts" / "system.md"
    baseline_source = source.read_text(encoding="utf-8")
    prepared = prepare_run(root / "pipeline.json", run_id="candidate_error")

    from examples.optimization.eval_optimize_loop import pipeline as pipeline_module

    original = pipeline_module.AgentEvaluator.evaluate_eval_set
    calls = 0

    async def fail_candidate_train(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("injected candidate failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(pipeline_module.AgentEvaluator, "evaluate_eval_set", fail_candidate_train)
    with pytest.raises(FakeStageExecutionError, match="candidate train evaluation failed"):
        await run_fake_stage(prepared, scenario="improve")

    working = await prepared.working_target.read_all()
    assert "deterministic-fake-candidate:start" in working["system_prompt"]
    assert source.read_text(encoding="utf-8") == baseline_source


def test_stage_two_cli_smoke(tmp_path: Path):
    root = _copy_example(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(_EXAMPLE / "run_pipeline.py"),
            "--config",
            str(root / "pipeline.json"),
            "--run-id",
            "cli_smoke",
            "--scenario",
            "overfit",
        ],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "Baseline train: 1/3 passed" in completed.stdout
    assert "Baseline validation: 1/3 passed" in completed.stdout
    assert "Candidate train: 3/3 passed" in completed.stdout
    assert "Candidate validation: 0/3 passed" in completed.stdout
    assert "Gate decision: REJECT" in completed.stdout
