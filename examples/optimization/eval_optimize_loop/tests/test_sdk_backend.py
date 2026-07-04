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
    assert calls["output_dir"].endswith("out")
    assert calls["target_prompt"].paths == [("system_prompt", str(prompt_path))]
    assert candidates[0].candidate_id == "sdk_best"
    assert candidates[0].prompt == "optimized prompt"
    assert candidates[0].prompt_diff.startswith("--- baseline_system_prompt.txt")
    assert backend.last_result is not None
    assert backend.last_result_summary["baseline_pass_rate"] == 0.5


def test_sdk_backend_default_target_prompt_uses_system_prompt_from_prompt_path(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(monkeypatch, best_prompts={"system_prompt": "optimized system"})
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline system", encoding="utf-8")

    candidates = SDKBackend(
        prompt_path=prompt_path,
        call_agent_path="fake_call_agent_module:call_agent",
    ).optimize(
        baseline_prompt="baseline system",
        train_path=tmp_path / "train.evalset.json",
        val_path=tmp_path / "val.evalset.json",
        optimizer_config_path=tmp_path / "optimizer.json",
        output_dir=tmp_path / "out",
    )

    assert calls["target_prompt"].paths == [("system_prompt", str(prompt_path))]
    assert candidates[0].prompt == "optimized system"


def test_sdk_backend_router_prompt_only_can_succeed(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(monkeypatch, best_prompts={"router_prompt": "optimized router"})
    router_path = tmp_path / "router.txt"
    router_path.write_text("baseline router", encoding="utf-8")

    candidates = SDKBackend(
        prompt_path=tmp_path / "unused_system.txt",
        call_agent_path="fake_call_agent_module:call_agent",
        target_prompt_paths={"router_prompt": router_path},
    ).optimize(
        baseline_prompt="unused",
        train_path=tmp_path / "train.evalset.json",
        val_path=tmp_path / "val.evalset.json",
        optimizer_config_path=tmp_path / "optimizer.json",
        output_dir=tmp_path / "out",
    )

    assert calls["target_prompt"].paths == [("router_prompt", str(router_path))]
    assert candidates[0].prompt == "## router_prompt\n\noptimized router"


def test_sdk_backend_skill_prompt_only_can_succeed(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(monkeypatch, best_prompts={"skill_prompt": "optimized skill"})
    skill_path = tmp_path / "skill.txt"
    skill_path.write_text("baseline skill", encoding="utf-8")

    candidates = SDKBackend(
        prompt_path=tmp_path / "unused_system.txt",
        call_agent_path="fake_call_agent_module:call_agent",
        target_prompt_paths={"skill_prompt": skill_path},
    ).optimize(
        baseline_prompt="unused",
        train_path=tmp_path / "train.evalset.json",
        val_path=tmp_path / "val.evalset.json",
        optimizer_config_path=tmp_path / "optimizer.json",
        output_dir=tmp_path / "out",
    )

    assert calls["target_prompt"].paths == [("skill_prompt", str(skill_path))]
    assert candidates[0].prompt == "## skill_prompt\n\noptimized skill"


def test_sdk_backend_missing_registered_best_prompt_field_is_clear(tmp_path: Path, monkeypatch):
    _install_fake_sdk(monkeypatch, best_prompts={"router_prompt": "optimized router"})
    router_path = tmp_path / "router.txt"
    skill_path = tmp_path / "skill.txt"
    router_path.write_text("baseline router", encoding="utf-8")
    skill_path.write_text("baseline skill", encoding="utf-8")
    backend = SDKBackend(
        prompt_path=tmp_path / "unused_system.txt",
        call_agent_path="fake_call_agent_module:call_agent",
        target_prompt_paths={"router_prompt": router_path, "skill_prompt": skill_path},
    )

    with pytest.raises(ValueError, match="best_prompts.*missing registered target fields.*skill_prompt"):
        backend.optimize(
            baseline_prompt="unused",
            train_path=tmp_path / "train.evalset.json",
            val_path=tmp_path / "val.evalset.json",
            optimizer_config_path=tmp_path / "optimizer.json",
            output_dir=tmp_path / "out",
        )


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

    with pytest.raises(ValueError, match="missing registered target fields.*system_prompt"):
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
        run_id="sdk_test_run",
    )

    output_dir = tmp_path / "sdk_run"
    payload = (output_dir / "optimization_report.json").read_text(encoding="utf-8")
    markdown = (output_dir / "optimization_report.md").read_text(encoding="utf-8")
    assert report.run["mode"] == "sdk"
    assert report.run["update_source"] is False
    assert report.selected_candidate == "sdk_best"
    assert report.baseline_validation.score == 0.5
    assert report.candidates[0]["validation_result"].score == 0.75
    assert report.gate_decisions[0].validation_score_delta == 0.25
    assert report.gate_decisions[0].candidate_cost == 0.123
    assert report.gate_decisions[0].gate_status == "partial_applied"
    assert report.gate_decisions[0].not_applied_checks == [
        "per_case_delta",
        "protected_regression",
        "new_hard_failure",
        "max_score_drop_per_case",
    ]
    assert report.audit["duration_seconds"] == 12.3
    assert report.audit["total_run_cost"] == 0.123
    assert report.audit["cost"]["total"] == 0.123
    assert report.audit["sdk_result_summary"]["status"] == "SUCCEEDED"
    assert report.audit["sdk_result_summary"]["baseline_metric_breakdown"] == {"exact_match": 0.5}
    assert report.audit["sdk_result_summary"]["best_metric_breakdown"] == {"exact_match": 0.75}
    assert report.audit["sdk_result_summary"]["metric_thresholds"] == {"exact_match": 0.7}
    assert report.audit["sdk_result_summary"]["total_token_usage"] == {
        "prompt": 100,
        "completion": 25,
        "total": 125,
    }
    assert report.audit["sdk_result_summary"]["rounds"][0]["validation_pass_rate"] == 0.75
    assert report.audit["sdk_result_availability"] == {
        "aggregate_validation_result": True,
        "full_train_eval_result": False,
        "full_per_case_validation_delta": False,
    }
    assert "train EvalResult compatibility field is unavailable" in report.audit["sdk_score_explanation"]
    assert "partial_applied" in payload
    assert "sdk_best (partial_applied)" in markdown
    assert "not applied checks: per_case_delta" in markdown
    assert "SDK mode uses OptimizeResult aggregate validation metrics" in markdown
    assert "fake_call_agent_module:call_agent" in report.run["reproducibility_command"]
    assert "module:function" not in report.run["reproducibility_command"]
    assert (output_dir / "runs" / "sdk_test_run" / "input_hashes.json").is_file()
    assert (output_dir / "runs" / "sdk_test_run" / "prompt_diffs" / "sdk_best.diff").is_file()
    assert calls["update_source"] is False
    assert calls["output_dir"].endswith("sdk_optimizer")
    assert report.run["sdk_artifact_dir"].endswith("sdk_optimizer")


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


def test_run_pipeline_mode_sdk_default_run_id_uses_sdk_started_at(tmp_path: Path, monkeypatch):
    _install_fake_sdk(
        monkeypatch,
        best_prompt="optimized prompt",
        started_at="2026-07-04T12:34:56+00:00",
    )

    report = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
    )

    assert report.run["run_id"] == "eval_optimize_loop_sdk_2026-07-04T12-34-56-00-00"
    assert (tmp_path / "sdk_run" / "runs" / report.run["run_id"]).is_dir()


def test_run_pipeline_mode_sdk_uses_default_wrapper_gate_when_sdk_config_has_no_gate(
    tmp_path: Path,
    monkeypatch,
):
    _install_fake_sdk(
        monkeypatch,
        best_prompt="optimized prompt",
        baseline_pass_rate=0.5,
        best_pass_rate=0.505,
        pass_rate_improvement=0.005,
        total_llm_cost=0.123,
    )
    optimizer_path = _write_sdk_optimizer_config(tmp_path)

    report = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=optimizer_path,
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
    )

    decision = report.gate_decisions[0]
    assert report.selected_candidate is None
    assert decision.accepted is False
    assert decision.gate_status == "partial_applied"
    assert decision.validation_score_delta == 0.005
    assert any("validation improvement" in reason for reason in decision.reasons)


def test_run_pipeline_mode_sdk_custom_gate_rejects_low_aggregate_validation_improvement(
    tmp_path: Path,
    monkeypatch,
):
    _install_fake_sdk(
        monkeypatch,
        best_prompt="optimized prompt",
        baseline_pass_rate=0.5,
        best_pass_rate=0.75,
        pass_rate_improvement=0.25,
        total_llm_cost=0.123,
    )
    gate_path = _write_gate_config(tmp_path, min_val_score_improvement=0.3, max_total_cost=1.0)

    report = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=_write_sdk_optimizer_config(tmp_path),
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
        gate_config_path=gate_path,
    )

    decision = report.gate_decisions[0]
    assert report.selected_candidate is None
    assert decision.accepted is False
    assert decision.validation_score_delta == 0.25
    assert any("validation improvement" in reason for reason in decision.reasons)


def test_run_pipeline_mode_sdk_custom_gate_rejects_cost_over_budget(tmp_path: Path, monkeypatch):
    _install_fake_sdk(
        monkeypatch,
        best_prompt="optimized prompt",
        baseline_pass_rate=0.5,
        best_pass_rate=0.75,
        pass_rate_improvement=0.25,
        total_llm_cost=2.0,
    )
    gate_path = _write_gate_config(tmp_path, min_val_score_improvement=0.01, max_total_cost=0.05)

    report = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=_write_sdk_optimizer_config(tmp_path),
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
        gate_config_path=gate_path,
    )

    decision = report.gate_decisions[0]
    assert report.selected_candidate is None
    assert decision.accepted is False
    assert decision.gate_status == "partial_applied"
    assert decision.total_run_cost == 2.0
    assert any("cost" in reason for reason in decision.reasons)


def test_run_pipeline_mode_sdk_does_not_pass_wrapper_gate_config_to_agent_optimizer(
    tmp_path: Path,
    monkeypatch,
):
    calls = _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")
    optimizer_path = _write_sdk_optimizer_config(tmp_path)
    gate_path = _write_gate_config(tmp_path, min_val_score_improvement=0.5, max_total_cost=0.05)

    run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=optimizer_path,
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
        gate_config_path=gate_path,
    )

    assert Path(calls["config_path"]).resolve() == optimizer_path.resolve()
    assert "gate" not in json.loads(Path(calls["config_path"]).read_text(encoding="utf-8"))
    assert json.loads(gate_path.read_text(encoding="utf-8"))["gate"]["max_total_cost"] == 0.05


def test_run_pipeline_mode_sdk_registers_multiple_target_prompt_paths(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(
        monkeypatch,
        best_prompts={
            "system_prompt": "optimized system",
            "router_prompt": "optimized router",
            "skill_prompt": "optimized skill",
        },
    )
    system_path = tmp_path / "system.txt"
    router_path = tmp_path / "router.txt"
    skill_path = tmp_path / "skill.txt"
    system_path.write_text("baseline system", encoding="utf-8")
    router_path.write_text("baseline router", encoding="utf-8")
    skill_path.write_text("baseline skill", encoding="utf-8")

    report = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=_write_sdk_optimizer_config(tmp_path),
        prompt_path=system_path,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
        target_prompts=[
            f"system_prompt={system_path}",
            f"router_prompt={router_path}",
            f"skill_prompt={skill_path}",
        ],
        gate_config_path=_write_gate_config(tmp_path, min_val_score_improvement=0.01, max_total_cost=1.0),
        run_id="sdk_multi_target",
    )

    assert calls["target_prompt"].paths == [
        ("system_prompt", str(system_path)),
        ("router_prompt", str(router_path)),
        ("skill_prompt", str(skill_path)),
    ]
    assert report.audit["sdk_result_summary"]["best_prompts"] == {
        "system_prompt": "optimized system",
        "router_prompt": "optimized router",
        "skill_prompt": "optimized skill",
    }
    assert "router_prompt" in report.candidates[0]["candidate"].prompt_diff
    assert set(report.audit["candidate_prompt_hashes_by_field"]["sdk_best"]) == {
        "system_prompt",
        "router_prompt",
        "skill_prompt",
    }
    run_dir = tmp_path / "sdk_run" / "runs" / "sdk_multi_target"
    assert (run_dir / "candidate_prompts" / "sdk_best" / "system_prompt.txt").read_text(
        encoding="utf-8"
    ) == "optimized system"
    assert (run_dir / "candidate_prompts" / "sdk_best" / "router_prompt.txt").read_text(
        encoding="utf-8"
    ) == "optimized router"
    assert (run_dir / "candidate_prompts" / "sdk_best" / "skill_prompt.txt").read_text(
        encoding="utf-8"
    ) == "optimized skill"
    input_hashes = json.loads((run_dir / "input_hashes.json").read_text(encoding="utf-8"))
    assert set(input_hashes["target_prompts"]) == {"system_prompt", "router_prompt", "skill_prompt"}
    assert "gate_config" in input_hashes
    command = report.run["reproducibility_command"]
    assert "--sdk-call-agent fake_call_agent_module:call_agent" in command
    assert f"--target-prompt router_prompt={router_path}" in command
    assert "--gate-config" in command


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
    assert "--update-source" in report.run["reproducibility_command"]


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


def _install_fake_sdk(
    monkeypatch,
    *,
    best_prompt: str | None = None,
    best_prompts: dict[str, str] | None = None,
    status: str = "SUCCEEDED",
    baseline_pass_rate: float = 0.5,
    best_pass_rate: float = 0.75,
    pass_rate_improvement: float = 0.25,
    total_llm_cost: float = 0.123,
    duration_seconds: float = 12.3,
    started_at: str | None = None,
):
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
            result_prompts = best_prompts if best_prompts is not None else {
                "system_prompt": "optimized prompt" if best_prompt is None else best_prompt
            }
            return types.SimpleNamespace(
                best_prompts=result_prompts,
                status=status,
                baseline_pass_rate=baseline_pass_rate,
                best_pass_rate=best_pass_rate,
                pass_rate_improvement=pass_rate_improvement,
                baseline_metric_breakdown={"exact_match": baseline_pass_rate},
                best_metric_breakdown={"exact_match": best_pass_rate},
                metric_thresholds={"exact_match": 0.7},
                total_llm_cost=total_llm_cost,
                total_token_usage={"prompt": 100, "completion": 25, "total": 125},
                duration_seconds=duration_seconds,
                started_at=started_at,
                total_rounds=1,
                rounds=[
                    types.SimpleNamespace(
                        validation_pass_rate=best_pass_rate,
                        accepted=True,
                        failed_case_ids=["case_a"],
                        round_llm_cost=total_llm_cost,
                        budget_used=3,
                        budget_total=10,
                    )
                ],
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


def _write_sdk_optimizer_config(tmp_path: Path) -> Path:
    path = tmp_path / "sdk_optimizer.json"
    path.write_text(
        json.dumps({
            "evaluate": {"metrics": []},
            "optimize": {"algorithm": {"name": "gepa_reflective"}},
        }),
        encoding="utf-8",
    )
    return path


def _write_gate_config(
    tmp_path: Path,
    *,
    min_val_score_improvement: float,
    max_total_cost: float,
) -> Path:
    path = tmp_path / "wrapper_gate.json"
    path.write_text(
        json.dumps({
            "gate": {
                "min_val_score_improvement": min_val_score_improvement,
                "max_total_cost": max_total_cost,
            }
        }),
        encoding="utf-8",
    )
    return path
