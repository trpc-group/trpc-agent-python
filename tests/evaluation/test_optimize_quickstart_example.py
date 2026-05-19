# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Smoke tests for ``examples/optimization/quickstart``.

Goals:
    * import the quickstart's ``agent`` package and ``run_optimization`` script
      without side effects
    * verify env-variable validation in ``agent.config.get_model_config``
    * verify ``agent.create_agent`` reads its instruction from
      ``agent/prompts/system.md`` and ``agent/prompts/skill.md``
    * verify the script-level ``call_agent`` is async and exposes a single
      ``query`` parameter (the contract the optimizer relies on)
    * verify the quickstart's ``optimizer.json`` is a valid
      ``OptimizeConfigFile`` and exercises the multi-metric scenario
    * verify the end-to-end optimize flow wires together when the reflection
      LLM, the gepa main loop, and the LLM judge are all mocked out

The quickstart's ``agent`` and ``run_optimization`` are loaded by absolute path
because they live outside the python package tree.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Loader helpers (import quickstart files by path without polluting sys.modules)
# ---------------------------------------------------------------------------


_QUICKSTART_DIR = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "optimization"
    / "quickstart"
)


def _load_quickstart_agent() -> Any:
    """Import ``agent.agent`` from the quickstart example directory."""
    if str(_QUICKSTART_DIR) not in sys.path:
        sys.path.insert(0, str(_QUICKSTART_DIR))
    if "agent" in sys.modules:
        # ensure we always reimport against the freshly mutated env
        for name in [k for k in sys.modules if k == "agent" or k.startswith("agent.")]:
            sys.modules.pop(name, None)
    import agent.agent as agent_mod  # type: ignore
    return agent_mod


def _load_quickstart_run_module() -> Any:
    """Load ``run_optimization.py`` as an importable module without executing main()."""
    if str(_QUICKSTART_DIR) not in sys.path:
        sys.path.insert(0, str(_QUICKSTART_DIR))
    spec = importlib.util.spec_from_file_location(
        "quickstart_run_optimization",
        _QUICKSTART_DIR / "run_optimization.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRPC_AGENT_API_KEY", "fake-key")
    monkeypatch.setenv("TRPC_AGENT_BASE_URL", "http://localhost/fake")
    monkeypatch.setenv("TRPC_AGENT_MODEL_NAME", "fake-model")


# ---------------------------------------------------------------------------
# Structure / contract
# ---------------------------------------------------------------------------


def test_quickstart_directory_layout_matches_expected_structure():
    expected = {
        "agent/__init__.py",
        "agent/agent.py",
        "agent/config.py",
        "agent/prompts/system.md",
        "agent/prompts/skill.md",
        "optimizer.json",
        "train.evalset.json",
        "val.evalset.json",
        "run_optimization.py",
    }
    for rel in expected:
        path = _QUICKSTART_DIR / rel
        assert path.exists(), f"missing quickstart file: {rel}"


def test_prompt_files_are_non_empty_markdown_files():
    for rel in ("agent/prompts/system.md", "agent/prompts/skill.md"):
        text = (_QUICKSTART_DIR / rel).read_text(encoding="utf-8")
        assert text.strip(), f"{rel} must not be empty"


def test_optimizer_json_declares_multi_metric_and_multi_prompt_setup():
    """The quickstart must showcase a multi-metric configuration so users see
    the reporter handle the multi-metric scenario end to end. The judge LLM
    metric (``llm_rubric_response``) must carry a populated rubrics list."""
    import json
    payload = json.loads((_QUICKSTART_DIR / "optimizer.json").read_text(encoding="utf-8"))
    metrics = payload["evaluate"]["metrics"]
    assert len(metrics) >= 2, "quickstart should configure 2+ metrics"
    names = {m["metric_name"] for m in metrics}
    assert "final_response_avg_score" in names
    assert "llm_rubric_response" in names
    judge_metric = next(m for m in metrics if m["metric_name"] == "llm_rubric_response")
    judge_cfg = judge_metric["criterion"]["llm_judge"]
    assert judge_cfg.get("judge_model"), "llm_rubric_response must configure judge_model"
    rubrics = judge_cfg.get("rubrics") or []
    assert len(rubrics) >= 2, "llm_rubric_response must list at least 2 rubrics"


def test_optimizer_json_validates_against_optimize_config_file():
    """Schema-level smoke: the example config must load cleanly via the SDK's
    public loader so any breaking schema change surfaces here."""
    from trpc_agent_sdk.evaluation._optimize_config import load_optimize_config

    cfg = load_optimize_config(str(_QUICKSTART_DIR / "optimizer.json"))
    metric_names = {m.metric_name for m in cfg.evaluate.get_eval_metrics()}
    assert metric_names == {"final_response_avg_score", "llm_rubric_response"}
    # Framework-level stop policy defaults to "all" via the example.
    assert cfg.optimize.stop.required_metrics == "all"


# ---------------------------------------------------------------------------
# agent.config: environment-variable validation
# ---------------------------------------------------------------------------


def test_get_model_config_raises_when_env_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TRPC_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("TRPC_AGENT_BASE_URL", raising=False)
    monkeypatch.delenv("TRPC_AGENT_MODEL_NAME", raising=False)
    agent_mod = _load_quickstart_agent()
    with pytest.raises(ValueError) as exc_info:
        agent_mod.get_model_config()
    msg = str(exc_info.value)
    assert "TRPC_AGENT_API_KEY" in msg
    assert "TRPC_AGENT_BASE_URL" in msg
    assert "TRPC_AGENT_MODEL_NAME" in msg


def test_get_model_config_returns_tuple_when_env_set(fake_env: None):
    agent_mod = _load_quickstart_agent()
    api_key, base_url, model_name = agent_mod.get_model_config()
    assert api_key == "fake-key"
    assert base_url == "http://localhost/fake"
    assert model_name == "fake-model"


# ---------------------------------------------------------------------------
# agent.agent: LlmAgent factory
# ---------------------------------------------------------------------------


def test_create_agent_composes_instruction_from_both_prompt_files(fake_env: None):
    agent_mod = _load_quickstart_agent()
    from trpc_agent_sdk.agents import LlmAgent

    agent_instance = agent_mod.create_agent()
    system_text = agent_mod.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    skill_text = agent_mod.SKILL_PATH.read_text(encoding="utf-8").strip()
    assert isinstance(agent_instance, LlmAgent)
    assert system_text in agent_instance.instruction
    assert skill_text in agent_instance.instruction
    assert agent_instance.name == "math_word_problem_agent"


def test_create_agent_picks_up_latest_prompt_text(fake_env: None):
    """Optimizer-flow sanity: rewriting any of the prompt files must be
    visible to the next agent."""
    agent_mod = _load_quickstart_agent()
    original_system = agent_mod.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    original_skill = agent_mod.SKILL_PATH.read_text(encoding="utf-8")
    try:
        agent_mod.SYSTEM_PROMPT_PATH.write_text("UPDATED SYSTEM", encoding="utf-8")
        agent_mod.SKILL_PATH.write_text("UPDATED SKILL", encoding="utf-8")
        new_agent = agent_mod.create_agent()
        assert "UPDATED SYSTEM" in new_agent.instruction
        assert "UPDATED SKILL" in new_agent.instruction
    finally:
        agent_mod.SYSTEM_PROMPT_PATH.write_text(original_system, encoding="utf-8")
        agent_mod.SKILL_PATH.write_text(original_skill, encoding="utf-8")


# ---------------------------------------------------------------------------
# run_optimization.py: call_agent contract
# ---------------------------------------------------------------------------


def test_run_optimization_module_exposes_async_call_agent(fake_env: None):
    module = _load_quickstart_run_module()
    assert inspect.iscoroutinefunction(module.call_agent), (
        "AgentOptimizer requires call_agent to be an async callable"
    )
    sig = inspect.signature(module.call_agent)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == "query"


def test_run_optimization_uses_runner_and_inmemory_session_service(fake_env: None):
    """The example must build call_agent on top of framework primitives."""
    module = _load_quickstart_run_module()
    src = (_QUICKSTART_DIR / "run_optimization.py").read_text(encoding="utf-8")
    assert "from trpc_agent_sdk.runners import Runner" in src
    assert "from trpc_agent_sdk.sessions import InMemorySessionService" in src
    assert "AgentOptimizer.optimize" in src
    assert "TargetPrompt" in src
    assert hasattr(module, "main")
    assert inspect.iscoroutinefunction(module.main)


# ---------------------------------------------------------------------------
# End-to-end wiring: optimizer flow with mocked gepa + mocked LLM judge
# ---------------------------------------------------------------------------


class _FakeGEPAResult:
    def __init__(self, candidates: list[dict], val_scores: list[float]) -> None:
        self.candidates = candidates
        self.val_aggregate_scores = val_scores
        self.parents = [[None]] + [[i - 1] for i in range(1, len(candidates))]
        self.discovery_eval_counts = [0] * len(candidates)
        self.total_metric_calls = 0
        self.best_outputs_valset = None

    @property
    def best_idx(self) -> int:
        return max(
            range(len(self.val_aggregate_scores)),
            key=lambda i: self.val_aggregate_scores[i],
        )


@pytest.mark.asyncio
async def test_quickstart_optimize_flow_runs_with_mocked_llm(
    tmp_path: Path,
    fake_env: None,
    monkeypatch: pytest.MonkeyPatch,
):
    """Full wiring: AgentOptimizer.optimize → adapter.evaluate → call_agent stub
    → mocked gepa → mocked LLM judge → SUCCEEDED OptimizeResult.

    Real LLM calls (reflection_lm + judge_model) are short-circuited so the
    test only exercises the framework's plumbing.
    """
    from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt
    from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
    from trpc_agent_sdk.evaluation._llm_judge import LLMJudge
    from trpc_agent_sdk.evaluation._optimize_gepa_reflective import (
        GepaReflectiveOptimizer,
    )

    agent_mod = _load_quickstart_agent()
    original_system = agent_mod.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    original_skill = agent_mod.SKILL_PATH.read_text(encoding="utf-8")

    # ``stub_call_agent`` returns a string that contains every reference answer
    # from train + val, so the ``contains``-based ``final_response_avg_score``
    # accepts every case (baseline_pass_rate is independently zeroed below by
    # the gepa stub returning a single seed candidate that passes too).
    expected_answers = [
        "答案：11 个",
        "答案：150 公里",
        "答案：160 元",
        "答案：40 个",
        "答案：3.5 千克",
        "答案：18 人",
    ]

    async def stub_call_agent(query: str) -> str:
        return " | ".join(expected_answers)

    async def fake_judge_evaluate(self, actual_invocations, expected_invocations):
        """Return a perfect EvaluationResult so llm_rubric_response is always
        PASSED without touching a real judge model."""
        from trpc_agent_sdk.evaluation._eval_result import EvaluationResult
        from trpc_agent_sdk.evaluation._eval_result import PerInvocationResult

        per_invocation_results = [
            PerInvocationResult(
                actual_invocation=actual,
                expected_invocation=expected,
                score=1.0,
                eval_status=EvalStatus.PASSED,
            )
            for actual, expected in zip(actual_invocations, expected_invocations)
        ]
        return EvaluationResult(
            overall_score=1.0,
            overall_eval_status=EvalStatus.PASSED,
            per_invocation_results=per_invocation_results,
        )

    monkeypatch.setattr(LLMJudge, "evaluate", fake_judge_evaluate)

    async def fake_call_gepa(self, **kwargs):
        seed = kwargs["seed_candidate"]
        improved = dict(seed)
        for key in improved:
            improved[key] = improved[key] + "\n\nIMPROVED"
        return _FakeGEPAResult(
            candidates=[seed, improved],
            val_scores=[0.0, 1.0],
        )

    monkeypatch.setattr(
        GepaReflectiveOptimizer, "_call_gepa_optimize", fake_call_gepa
    )

    try:
        target = (
            TargetPrompt()
            .add_path("system_prompt", str(agent_mod.SYSTEM_PROMPT_PATH))
            .add_path("skill", str(agent_mod.SKILL_PATH))
        )
        result = await AgentOptimizer.optimize(
            config_path=str(_QUICKSTART_DIR / "optimizer.json"),
            call_agent=stub_call_agent,
            target_prompt=target,
            train_dataset_path=str(_QUICKSTART_DIR / "train.evalset.json"),
            validation_dataset_path=str(_QUICKSTART_DIR / "val.evalset.json"),
            output_dir=str(tmp_path / "quickstart_runs"),
            verbose=0,
        )

        assert result.status == "SUCCEEDED"
        assert result.algorithm == "gepa_reflective"
        assert result.best_pass_rate == pytest.approx(1.0)
        # default update_source=False keeps both sources untouched
        assert (
            agent_mod.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") == original_system
        )
        assert (
            agent_mod.SKILL_PATH.read_text(encoding="utf-8") == original_skill
        )
        # Both registered prompts are present in best_prompts and were rewritten.
        assert set(result.best_prompts.keys()) == {"system_prompt", "skill"}
        assert "IMPROVED" in result.best_prompts["system_prompt"]
        assert "IMPROVED" in result.best_prompts["skill"]
        # Artifacts include both best_prompts files (multi-prompt scenario).
        best_dir = tmp_path / "quickstart_runs" / "best_prompts"
        assert (best_dir / "system_prompt.md").is_file()
        assert (best_dir / "skill.md").is_file()
    finally:
        agent_mod.SYSTEM_PROMPT_PATH.write_text(original_system, encoding="utf-8")
        agent_mod.SKILL_PATH.write_text(original_skill, encoding="utf-8")


# ---------------------------------------------------------------------------
# CONC-2 fix: real gepa main loop drives adapter.evaluate multiple times,
# verifying the long-lived event loop is shared across rounds and that
# module-level async resources held by call_agent stay valid.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quickstart_real_gepa_loop_reuses_single_event_loop_across_rounds(
    tmp_path: Path,
    fake_env: None,
    monkeypatch: pytest.MonkeyPatch,
):
    """Real gepa.optimize drives adapter.evaluate multiple times. The
    adapter's long-lived event loop must be reused across every evaluate
    so call_agent can hold module-level async resources safely."""
    import asyncio
    import json

    from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt
    from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
    from trpc_agent_sdk.evaluation._llm_judge import LLMJudge
    from trpc_agent_sdk.evaluation._optimize_model_callable import (
        _OptimizeModelCallable,
    )

    agent_mod = _load_quickstart_agent()
    original_system = agent_mod.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    original_skill = agent_mod.SKILL_PATH.read_text(encoding="utf-8")

    # Track the running loop id every time call_agent fires; must stay
    # constant across all evaluate() invocations.
    seen_loop_ids: list[int] = []

    expected_answers = [
        "答案：11 个", "答案：150 公里", "答案：160 元",
        "答案：40 个", "答案：3.5 千克", "答案：18 人",
    ]

    async def stub_call_agent(query: str) -> str:
        seen_loop_ids.append(id(asyncio.get_running_loop()))
        return " | ".join(expected_answers)

    # Make the LLM judge always pass.
    async def fake_judge_evaluate(self, actual_invocations, expected_invocations):
        from trpc_agent_sdk.evaluation._eval_result import (
            EvaluationResult,
            PerInvocationResult,
        )
        return EvaluationResult(
            overall_score=1.0,
            overall_eval_status=EvalStatus.PASSED,
            per_invocation_results=[
                PerInvocationResult(
                    actual_invocation=a,
                    expected_invocation=e,
                    score=1.0,
                    eval_status=EvalStatus.PASSED,
                )
                for a, e in zip(actual_invocations, expected_invocations)
            ],
        )

    monkeypatch.setattr(LLMJudge, "evaluate", fake_judge_evaluate)

    # Stub reflection LM so gepa main loop doesn't hit a real backend.
    # Returns the candidate's instruction with a marker appended each time.
    rewrite_count = {"n": 0}

    def fake_reflection_call(self, prompt):
        rewrite_count["n"] += 1
        self.total_calls += 1
        return f"REWRITE_v{rewrite_count['n']}"

    monkeypatch.setattr(_OptimizeModelCallable, "__call__", fake_reflection_call)

    # Use a tiny budget so the run finishes quickly but still exercises
    # at least baseline + 1 round of adapter.evaluate (=2 evaluate calls
    # minimum, in practice baseline + minibatch_eval + valset_eval per
    # round = 3+ evaluate calls).
    config_path = tmp_path / "optimizer.json"
    config_payload = json.loads(
        (_QUICKSTART_DIR / "optimizer.json").read_text(encoding="utf-8")
    )
    config_payload["optimize"]["algorithm"]["max_metric_calls"] = 6
    config_payload["optimize"]["algorithm"]["reflection_minibatch_size"] = 1
    config_payload["optimize"]["algorithm"]["max_iterations_without_improvement"] = 1
    config_path.write_text(json.dumps(config_payload), encoding="utf-8")

    try:
        target = (
            TargetPrompt()
            .add_path("system_prompt", str(agent_mod.SYSTEM_PROMPT_PATH))
            .add_path("skill", str(agent_mod.SKILL_PATH))
        )
        result = await AgentOptimizer.optimize(
            config_path=str(config_path),
            call_agent=stub_call_agent,
            target_prompt=target,
            train_dataset_path=str(_QUICKSTART_DIR / "train.evalset.json"),
            validation_dataset_path=str(_QUICKSTART_DIR / "val.evalset.json"),
            output_dir=str(tmp_path / "real_gepa_runs"),
            verbose=0,
        )
    finally:
        agent_mod.SYSTEM_PROMPT_PATH.write_text(original_system, encoding="utf-8")
        agent_mod.SKILL_PATH.write_text(original_skill, encoding="utf-8")

    # Real gepa drove adapter.evaluate at least twice (baseline + round 1).
    assert len(seen_loop_ids) >= 2, (
        f"Expected real gepa main loop to call call_agent more than once; "
        f"saw {len(seen_loop_ids)} call(s)."
    )

    # All call_agent invocations across the entire optimize() ran on the
    # same long-lived event loop (CONC-2 fix).
    assert len(set(seen_loop_ids)) == 1, (
        f"call_agent ran on multiple distinct loops across rounds: "
        f"{set(seen_loop_ids)}. Module-level async resources would break."
    )

    # OptimizeResult is well-formed.
    assert result.status in {"SUCCEEDED", "FAILED"}
    assert result.algorithm == "gepa_reflective"
