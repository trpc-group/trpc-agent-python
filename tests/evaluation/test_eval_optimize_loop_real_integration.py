# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""真实业务 Agent 与真实反思模型集成入口的离线契约测试。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.optimization.eval_optimize_loop.candidate_provider import (
    AgentOptimizerCandidateProvider,
)
from examples.optimization.eval_optimize_loop.candidate_provider import CandidateRequest
from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.real_agent import BusinessModelConfig
from examples.optimization.eval_optimize_loop.real_agent import RealBusinessAgent
from examples.optimization.eval_optimize_loop.real_agent import load_business_model_config
from examples.optimization.eval_optimize_loop import run_real_integration
from examples.optimization.eval_optimize_loop.schemas import OptimizerRuntimeParameters
from trpc_agent_sdk.evaluation import OptimizeResult


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE = _REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"


def _copy_example(tmp_path: Path) -> Path:
    target = tmp_path / "eval_optimize_loop"
    shutil.copytree(_EXAMPLE, target, ignore=shutil.ignore_patterns("runs", "__pycache__"))
    return target


def _result(baseline: dict[str, str]) -> OptimizeResult:
    best = {name: f"{value}\noptimized" for name, value in baseline.items()}
    return OptimizeResult(
        algorithm="gepa_reflective",
        status="SUCCEEDED",
        finish_reason="completed",
        baseline_pass_rate=0.3,
        best_pass_rate=0.7,
        pass_rate_improvement=0.4,
        baseline_prompts=baseline,
        best_prompts=best,
        total_rounds=1,
        rounds=[],
        total_reflection_lm_calls=1,
        total_llm_cost=0.0,
        total_token_usage={"prompt": 0, "completion": 0, "total": 0},
        duration_seconds=0.1,
        started_at="2026-07-18T00:00:00Z",
        finished_at="2026-07-18T00:00:01Z",
    )


def test_optimizer_runtime_parameters_are_serializable_and_validated():
    parameters = OptimizerRuntimeParameters(
        model_name="mimo-v2.5",
        temperature=0.2,
        max_tokens=2048,
        think=False,
        max_candidate_proposals=1,
    )

    assert OptimizerRuntimeParameters.model_validate_json(parameters.model_dump_json()) == parameters
    with pytest.raises(ValueError):
        OptimizerRuntimeParameters(model_name="", max_candidate_proposals=1)
    with pytest.raises(ValueError):
        OptimizerRuntimeParameters(model_name="model", max_candidate_proposals=0)
    with pytest.raises(ValueError):
        OptimizerRuntimeParameters(model_name="model", temperature=float("inf"))


def test_business_model_config_requires_all_three_environment_values():
    with pytest.raises(ValueError) as exc_info:
        load_business_model_config({})

    message = str(exc_info.value)
    assert "TRPC_AGENT_API_KEY" in message
    assert "TRPC_AGENT_BASE_URL" in message
    assert "TRPC_AGENT_MODEL_NAME" in message

    normalized = load_business_model_config(
        {
            "TRPC_AGENT_API_KEY": " key ",
            "TRPC_AGENT_BASE_URL": " https://example.test ",
            "TRPC_AGENT_MODEL_NAME": " model ",
        }
    )
    assert normalized == BusinessModelConfig(
        api_key="key",
        base_url="https://example.test",
        model_name="model",
    )


@pytest.mark.asyncio
async def test_real_business_agent_rereads_prompts_and_returns_only_final_non_thought_text(
    monkeypatch: pytest.MonkeyPatch,
):
    prompt_versions = [
        {"system_prompt": "baseline instruction"},
        {"system_prompt": "candidate instruction"},
    ]

    class FakeTarget:
        async def read_all(self):
            return prompt_versions.pop(0)

    class FakePart:
        def __init__(self, text: str, *, thought: bool = False):
            self.text = text
            self.thought = thought

    class FakeEvent:
        def __init__(self, final: bool, parts: list[FakePart]):
            self._final = final
            self.content = type("Content", (), {"parts": parts})()

        def is_final_response(self):
            return self._final

    captured_instructions: list[str] = []

    class FakeLlmAgent:
        def __init__(self, **kwargs):
            captured_instructions.append(kwargs["instruction"])

    class FakeSessionService:
        async def create_session(self, **kwargs):
            return None

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def run_async(self, **kwargs):
            yield FakeEvent(False, [FakePart("intermediate")])
            yield FakeEvent(True, [FakePart("thinking", thought=True), FakePart("answer")])

    monkeypatch.setattr("examples.optimization.eval_optimize_loop.real_agent.OpenAIModel", lambda **kwargs: object())
    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.business_agent.LlmAgent",
        FakeLlmAgent,
    )
    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.business_agent.InMemorySessionService",
        FakeSessionService,
    )
    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.business_agent.Runner",
        FakeRunner,
    )
    agent = RealBusinessAgent(
        FakeTarget(),
        BusinessModelConfig(api_key="secret", base_url="https://example.test", model_name="model"),
    )

    assert await agent.call_agent("first") == "answer"
    assert await agent.call_agent("second") == "answer"
    assert captured_instructions == ["baseline instruction", "candidate instruction"]


@pytest.mark.asyncio
async def test_provider_writes_runtime_config_without_persisting_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _copy_example(tmp_path)
    prepared = prepare_run(root / "pipeline.json", run_id="runtime_config")
    baseline = await prepared.working_target.read_all()
    original_config = Path(prepared.input_snapshot.optimizer_config_path).read_text(encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_optimize(**kwargs):
        captured.update(kwargs)
        output_dir = Path(str(kwargs["output_dir"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(kwargs["config_path"]), output_dir / "config.snapshot.json")
        return _result(baseline)

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
        seed=42,
        runtime_parameters=OptimizerRuntimeParameters(
            provider_name="openai",
            model_name="mimo-v2.5",
            variant="chat_completions",
            temperature=0.25,
            max_tokens=1024,
            think=True,
            max_candidate_proposals=1,
        ),
    )

    template = json.loads(request.optimizer_config_path.read_text(encoding="utf-8"))
    template["evaluate"]["credential_probe"] = {
        "api_key": "judge-secret",
        "base_url": "https://judge.example.test",
    }
    request.optimizer_config_path.write_text(json.dumps(template), encoding="utf-8")

    await AgentOptimizerCandidateProvider(lambda query: query).propose(request)

    runtime_path = Path(str(captured["config_path"]))
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    reflection = runtime["optimize"]["algorithm"]["reflection_lm"]
    assert runtime_path == Path(prepared.workspace.run_dir) / "optimizer.runtime.json"
    assert reflection == {
        "provider_name": "openai",
        "model_name": "mimo-v2.5",
        "variant": "chat_completions",
        "base_url": "${TRPC_AGENT_BASE_URL}",
        "api_key": "${TRPC_AGENT_API_KEY}",
        "generation_config": {"temperature": 0.25, "max_tokens": 1024},
        "think": True,
    }
    assert runtime["optimize"]["algorithm"]["max_candidate_proposals"] == 1
    assert runtime["evaluate"]["credential_probe"] == {
        "api_key": "${TRPC_AGENT_API_KEY}",
        "base_url": "${TRPC_AGENT_BASE_URL}",
    }
    runtime_text = runtime_path.read_text(encoding="utf-8")
    snapshot_text = (request.output_dir / "config.snapshot.json").read_text(encoding="utf-8")
    assert "judge-secret" not in runtime_text
    assert "judge-secret" not in snapshot_text
    assert original_config != request.optimizer_config_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_checks_source_prompt_even_when_pipeline_fails(monkeypatch: pytest.MonkeyPatch):
    class SourceTarget:
        reads = 0

        async def read_all(self):
            self.reads += 1
            return {"system_prompt": "before" if self.reads == 1 else "drifted"}

    prepared = SimpleNamespace(
        source_target=SourceTarget(),
        working_target=object(),
    )

    monkeypatch.setattr(run_real_integration, "prepare_run", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(run_real_integration, "RealBusinessAgent", lambda *args, **kwargs: object())

    async def fail_stage(*args, **kwargs):
        raise RuntimeError("pipeline failed")

    monkeypatch.setattr(run_real_integration, "run_real_stage", fail_stage)
    args = SimpleNamespace(config=Path("pipeline.real.json"), run_id="failure")

    with pytest.raises(RuntimeError, match="source Prompt changed"):
        await run_real_integration._run(
            args,
            BusinessModelConfig(api_key="key", base_url="url", model_name="model"),
            OptimizerRuntimeParameters(model_name="model"),
        )


def test_real_cli_complete_gate_reject_still_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    snapshot = SimpleNamespace(passed_case_count=1, total_case_count=3, average_score=1 / 3)
    result = SimpleNamespace(
        baseline_train=snapshot,
        baseline_validation=snapshot,
        candidate_train=snapshot,
        candidate_validation=snapshot,
        optimize_result=SimpleNamespace(status="SUCCEEDED", total_rounds=1),
        candidate=SimpleNamespace(candidate_id="optimizer-123456789abc"),
        gate_decision=SimpleNamespace(decision="reject", rejection_reasons=["no improvement"]),
        writeback=SimpleNamespace(status="skipped", reason="gate_rejected"),
    )
    prepared = SimpleNamespace(workspace=SimpleNamespace(run_dir="runs/offline"))

    async def complete(*args, **kwargs):
        return prepared, result

    monkeypatch.setenv("TRPC_AGENT_API_KEY", "key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "https://example.test")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "business-model")
    monkeypatch.setattr(run_real_integration, "_run", complete)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_real_integration.py", "--run-real", "--optimizer-model-name", "optimizer-model"],
    )

    assert run_real_integration.main() == 0
    output = capsys.readouterr().out
    assert "Gate decision: REJECT" in output
    assert "Baseline train" in output
    assert "Baseline validation" in output
    assert "Candidate train" in output
    assert "Candidate validation" in output


def test_real_cli_pipeline_error_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    async def fail(*args, **kwargs):
        raise RuntimeError("offline failure")

    monkeypatch.setenv("TRPC_AGENT_API_KEY", "key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "https://example.test")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "business-model")
    monkeypatch.setattr(run_real_integration, "_run", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_real_integration.py", "--run-real", "--optimizer-model-name", "optimizer-model"],
    )

    assert run_real_integration.main() == 1
    assert "offline failure" in capsys.readouterr().err


def test_real_cli_redacts_environment_secrets_from_pipeline_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    api_key = "integration-secret-key"
    base_url = "https://private-gateway.example.test/v1"

    async def fail(*args, **kwargs):
        raise RuntimeError(
            f"request failed api_key={api_key} base_url={base_url}"
        )

    monkeypatch.setenv("TRPC_AGENT_API_KEY", api_key)
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", base_url)
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "business-model")
    monkeypatch.setattr(run_real_integration, "_run", fail)
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

    assert run_real_integration.main() == 1
    error = capsys.readouterr().err
    assert api_key not in error
    assert base_url not in error
    assert "[REDACTED]" in error


def test_real_cli_requires_explicit_confirmation_before_creating_workspace(tmp_path: Path):
    root = _copy_example(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "run_real_integration.py"),
            "--config",
            str(root / "pipeline.real.json"),
            "--optimizer-model-name",
            "mimo-v2.5",
            "--run-id",
            "must_not_exist",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "--run-real" in completed.stderr
    assert not (root / "runs" / "must_not_exist").exists()


def test_real_cli_requires_business_model_environment_before_creating_workspace(
    tmp_path: Path,
):
    root = _copy_example(tmp_path)
    env = {
        key: value
        for key, value in __import__("os").environ.items()
        if key not in {"TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"}
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "run_real_integration.py"),
            "--run-real",
            "--config",
            str(root / "pipeline.real.json"),
            "--optimizer-model-name",
            "mimo-v2.5",
            "--run-id",
            "missing_env",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "TRPC_AGENT_API_KEY" in completed.stderr
    assert "TRPC_AGENT_BASE_URL" in completed.stderr
    assert "TRPC_AGENT_MODEL_NAME" in completed.stderr
    assert not (root / "runs" / "missing_env").exists()
