from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.eval_loop.backends import SDKBackend


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
            return types.SimpleNamespace(best_prompts={"system_prompt": "optimized prompt"})

    fake_eval_module = types.ModuleType("trpc_agent_sdk.evaluation")
    fake_eval_module.AgentEvaluator = object
    fake_eval_module.AgentOptimizer = FakeAgentOptimizer
    fake_eval_module.TargetPrompt = FakeTargetPrompt
    monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", fake_eval_module)

    call_agent_module = types.ModuleType("fake_call_agent_module")

    async def call_agent(query: str) -> str:
        return query

    call_agent_module.call_agent = call_agent
    monkeypatch.setitem(sys.modules, "fake_call_agent_module", call_agent_module)

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
