from __future__ import annotations

import builtins
import json
import sys
import types
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.eval_loop.backends import SDKBackend
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_OPTIMIZER_CONFIG
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_PROMPT
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_TRAIN
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_VAL
from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline


def test_sdk_backend_requires_call_agent_path(tmp_path: Path):
    backend = SDKBackend(prompt_path=tmp_path / "prompt.txt")

    with pytest.raises(ValueError, match="--sdk-call-agent"):
        backend.optimize(
            baseline_prompt="baseline",
            train_path=tmp_path / "train.evalset.json",
            val_path=tmp_path / "val.evalset.json",
            optimizer_config_path=tmp_path / "optimizer.json",
            output_dir=tmp_path / "out",
        )


def test_sdk_backend_calls_agent_optimizer_and_converts_best_prompt(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")

    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")
    backend = SDKBackend(prompt_path=prompt_path, call_agent_path="fake_call_agent_module:call_agent")

    candidates = backend.optimize(
        baseline_prompt="baseline",
        train_path=tmp_path / "train.evalset.json",
        val_path=tmp_path / "val.evalset.json",
        optimizer_config_path=tmp_path / "optimizer.json",
        output_dir=tmp_path / "out",
    )

    assert calls["config_path"].endswith("optimizer.json")
    assert calls["update_source"] is False
    assert calls["target_prompt"].paths == [("system_prompt", str(prompt_path))]
    assert candidates[0].candidate_id == "sdk_best"
    assert candidates[0].prompt == "optimized prompt"
    assert candidates[0].prompt_diff.startswith("--- baseline_system_prompt.txt")


def test_sdk_backend_passes_update_source_true(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    SDKBackend(
        prompt_path=prompt_path,
        call_agent_path="fake_call_agent_module:call_agent",
        update_source=True,
    ).optimize(
        baseline_prompt="baseline",
        train_path=tmp_path / "train.evalset.json",
        val_path=tmp_path / "val.evalset.json",
        optimizer_config_path=tmp_path / "optimizer.json",
        output_dir=tmp_path / "out",
    )

    assert calls["update_source"] is True


def test_sdk_backend_call_agent_import_failure_names_target(tmp_path: Path):
    backend = SDKBackend(prompt_path=tmp_path / "prompt.txt", call_agent_path="missing.module:call_agent")

    with pytest.raises(ValueError, match="missing.module:call_agent"):
        backend.optimize(
            baseline_prompt="baseline",
            train_path=tmp_path / "train.evalset.json",
            val_path=tmp_path / "val.evalset.json",
            optimizer_config_path=tmp_path / "optimizer.json",
            output_dir=tmp_path / "out",
        )


def test_sdk_backend_sdk_import_failure_is_clear(tmp_path: Path, monkeypatch):
    call_agent_module = types.ModuleType("fake_call_agent_module")

    async def call_agent(query: str) -> str:
        return query

    call_agent_module.call_agent = call_agent
    monkeypatch.setitem(sys.modules, "fake_call_agent_module", call_agent_module)

    real_import = builtins.__import__

    def fail_sdk_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "trpc_agent_sdk.evaluation":
            raise ImportError("forced sdk import failure")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_sdk_import)
    backend = SDKBackend(prompt_path=tmp_path / "prompt.txt", call_agent_path="fake_call_agent_module:call_agent")

    with pytest.raises(ValueError, match="AgentOptimizer/TargetPrompt"):
        backend.optimize(
            baseline_prompt="baseline",
            train_path=tmp_path / "train.evalset.json",
            val_path=tmp_path / "val.evalset.json",
            optimizer_config_path=tmp_path / "optimizer.json",
            output_dir=tmp_path / "out",
        )


def test_sdk_backend_empty_best_prompt_error_is_clear(tmp_path: Path, monkeypatch):
    _install_fake_sdk(monkeypatch, best_prompt="")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")
    backend = SDKBackend(prompt_path=prompt_path, call_agent_path="fake_call_agent_module:call_agent")

    with pytest.raises(ValueError, match="best_prompts\\['system_prompt'\\]"):
        backend.optimize(
            baseline_prompt="baseline",
            train_path=tmp_path / "train.evalset.json",
            val_path=tmp_path / "val.evalset.json",
            optimizer_config_path=tmp_path / "optimizer.json",
            output_dir=tmp_path / "out",
        )


@pytest.mark.asyncio
async def test_sdk_backend_sync_optimize_rejects_active_event_loop(tmp_path: Path, monkeypatch):
    _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")
    backend = SDKBackend(prompt_path=tmp_path / "prompt.txt", call_agent_path="fake_call_agent_module:call_agent")

    with pytest.raises(ValueError, match="optimize_async"):
        backend.optimize(
            baseline_prompt="baseline",
            train_path=tmp_path / "train.evalset.json",
            val_path=tmp_path / "val.evalset.json",
            optimizer_config_path=tmp_path / "optimizer.json",
            output_dir=tmp_path / "out",
        )


@pytest.mark.asyncio
async def test_sdk_backend_async_api_works_inside_active_event_loop(tmp_path: Path, monkeypatch):
    _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")
    backend = SDKBackend(prompt_path=prompt_path, call_agent_path="fake_call_agent_module:call_agent")

    candidates = await backend.optimize_async(
        baseline_prompt="baseline",
        train_path=tmp_path / "train.evalset.json",
        val_path=tmp_path / "val.evalset.json",
        optimizer_config_path=tmp_path / "optimizer.json",
        output_dir=tmp_path / "out",
    )

    assert candidates[0].candidate_id == "sdk_best"


def test_run_pipeline_mode_sdk_writes_report_without_fallback(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")

    report = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
    )

    output_dir = tmp_path / "sdk_run"
    payload = (output_dir / "optimization_report.json").read_text(encoding="utf-8")
    markdown = (output_dir / "optimization_report.md").read_text(encoding="utf-8")
    assert report.run["mode"] == "sdk"
    assert report.run["update_source"] is False
    assert report.selected_candidate == "sdk_best"
    assert report.gate_decisions[0].gate_status == "not_applicable"
    assert "SDK optimizer result does not expose per-case validation results" in payload
    assert "sdk_best (not_applicable)" in markdown
    assert (output_dir / "runs" / "eval_optimize_loop_sdk" / "input_hashes.json").is_file()
    assert (output_dir / "runs" / "eval_optimize_loop_sdk" / "prompt_diffs" / "sdk_best.diff").is_file()
    assert calls["update_source"] is False


def test_run_pipeline_mode_sdk_accepts_sdk_shaped_inputs_without_fake_schema(tmp_path: Path, monkeypatch):
    _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")
    train_path = tmp_path / "sdk_train.evalset.json"
    val_path = tmp_path / "sdk_val.evalset.json"
    optimizer_path = tmp_path / "sdk_optimizer.json"
    prompt_path = tmp_path / "system_prompt.txt"
    train_path.write_text(json.dumps({"eval_cases": []}), encoding="utf-8")
    val_path.write_text(json.dumps({"eval_cases": []}), encoding="utf-8")
    optimizer_path.write_text(
        json.dumps({"seed": "sdk-owned-seed", "optimize": {"algorithm": {"name": "gepa_reflective"}}}),
        encoding="utf-8",
    )
    prompt_path.write_text("baseline", encoding="utf-8")

    report = run_pipeline(
        mode="sdk",
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_path,
        prompt_path=prompt_path,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
    )

    assert report.run["mode"] == "sdk"
    assert report.run["train_cases"] == 0
    assert report.selected_candidate == "sdk_best"


def test_run_pipeline_mode_sdk_passes_update_source_true(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")

    report = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
        update_source=True,
    )

    assert report.run["update_source"] is True
    assert calls["update_source"] is True


def test_run_pipeline_mode_sdk_missing_call_agent_is_not_fake_fallback(tmp_path: Path):
    with pytest.raises(ValueError, match="--sdk-call-agent"):
        run_pipeline(
            mode="sdk",
            train_path=DEFAULT_TRAIN,
            val_path=DEFAULT_VAL,
            optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
            prompt_path=DEFAULT_PROMPT,
            output_dir=tmp_path / "sdk_run",
        )


def _install_fake_sdk(monkeypatch, *, best_prompt: str):
    calls = {}

    class FakeTargetPrompt:
        def __init__(self):
            self.paths = []

        def add_path(self, name, path):
            self.paths.append((name, path))
            return self

    class FakeAgentOptimizer:
        @staticmethod
        async def optimize(**kwargs):
            calls.update(kwargs)
            return types.SimpleNamespace(
                best_prompts={"system_prompt": best_prompt},
                status="SUCCEEDED",
                best_pass_rate=1.0,
            )

    fake_eval_module = types.ModuleType("trpc_agent_sdk.evaluation")
    fake_eval_module.AgentOptimizer = FakeAgentOptimizer
    fake_eval_module.TargetPrompt = FakeTargetPrompt
    monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", fake_eval_module)

    call_agent_module = types.ModuleType("fake_call_agent_module")

    async def call_agent(query: str) -> str:
        return query

    call_agent_module.call_agent = call_agent
    monkeypatch.setitem(sys.modules, "fake_call_agent_module", call_agent_module)
    return calls
