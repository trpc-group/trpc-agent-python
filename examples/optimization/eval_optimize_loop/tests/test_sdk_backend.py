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
from examples.optimization.eval_optimize_loop.run_pipeline import _parse_target_prompt_paths
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


def test_sdk_backend_empty_best_prompts_dict_error_is_clear(tmp_path: Path, monkeypatch):
    _install_fake_sdk(monkeypatch, best_prompts={})
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")
    backend = SDKBackend(prompt_path=prompt_path, call_agent_path="fake_call_agent_module:call_agent")

    with pytest.raises(ValueError, match="best_prompts was empty"):
        backend.optimize(
            baseline_prompt="baseline",
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


def test_sdk_backend_call_agent_must_be_callable(tmp_path: Path, monkeypatch):
    call_agent_module = types.ModuleType("fake_call_agent_module")
    call_agent_module.call_agent = "not callable"
    monkeypatch.setitem(sys.modules, "fake_call_agent_module", call_agent_module)
    backend = SDKBackend(prompt_path=tmp_path / "prompt.txt", call_agent_path="fake_call_agent_module:call_agent")

    with pytest.raises(ValueError, match="--sdk-call-agent.*fake_call_agent_module:call_agent"):
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

    with pytest.raises(ValueError, match="contained empty registered target fields.*system_prompt"):
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
    assert report.baseline_train.score == 0.333333
    assert report.baseline_validation.score == 0.666667
    assert report.candidates[0]["train_result"].score == 1.0
    assert report.candidates[0]["validation_result"].score == 1.0
    assert len(report.per_case_deltas) == 6
    assert {
        (delta.split, delta.case_id): delta.delta_type
        for delta in report.per_case_deltas
    } == {
        ("train", "train_json_refund"): "new_pass",
        ("train", "train_exact_order_status"): "new_pass",
        ("train", "train_rubric_retry_summary"): "unchanged",
        ("validation", "val_json_invoice"): "new_pass",
        ("validation", "val_explain_cache"): "unchanged",
        ("validation", "val_protected_yes_no"): "unchanged",
    }
    assert report.gate_decisions[0].validation_score_delta == 0.333333
    assert report.gate_decisions[0].candidate_cost == 0.123
    assert report.gate_decisions[0].gate_status == "applied"
    assert report.gate_decisions[0].not_applied_checks == []
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
        "full_train_eval_result": True,
        "full_per_case_validation_delta": True,
    }
    assert "AgentEvaluator post-optimization runs" in report.audit["sdk_score_explanation"]
    assert "partial_applied" not in payload
    assert "sdk_best (accepted)" in markdown
    assert "not applied checks" not in markdown
    assert "SDK mode uses OptimizeResult aggregate validation metrics" not in markdown
    assert "fake_call_agent_module:call_agent" in report.run["reproducibility_command"]
    assert "module:function" not in report.run["reproducibility_command"]
    assert (output_dir / "runs" / "sdk_test_run" / "input_hashes.json").is_file()
    assert (output_dir / "runs" / "sdk_test_run" / "prompt_diffs" / "sdk_best.diff").is_file()
    assert (output_dir / "runs" / "sdk_test_run" / "case_results" / "sdk_best_validation.json").is_file()
    assert calls["update_source"] is False
    assert calls["output_dir"].endswith("sdk_optimizer")
    assert report.run["sdk_artifact_dir"].endswith("sdk_optimizer")
    assert calls["agent_evaluator_runs"] == [
        ("baseline", "train"),
        ("baseline", "validation"),
        ("sdk_best", "train"),
        ("sdk_best", "validation"),
    ]


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
    assert report.selected_candidate is None
    assert report.gate_decisions[0].accepted is False


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

    assert report.run["run_id"] == "eval_optimize_loop_sdk_20260704T123456Z"
    assert (tmp_path / "sdk_run" / "runs" / report.run["run_id"]).is_dir()
    assert "--run-id" not in report.run["reproducibility_command"]


def test_run_pipeline_mode_sdk_default_run_id_collision_gets_suffix(tmp_path: Path, monkeypatch):
    _install_fake_sdk(
        monkeypatch,
        best_prompt="optimized prompt",
        started_at="2026-07-04T12:34:56+00:00",
    )

    first = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
    )
    second = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
    )

    assert first.run["run_id"] == "eval_optimize_loop_sdk_20260704T123456Z"
    assert second.run["run_id"] == "eval_optimize_loop_sdk_20260704T123456Z-1"
    assert (tmp_path / "sdk_run" / "runs" / first.run["run_id"]).is_dir()
    assert (tmp_path / "sdk_run" / "runs" / second.run["run_id"]).is_dir()


def test_run_pipeline_mode_sdk_explicit_run_id_stays_stable(tmp_path: Path, monkeypatch):
    _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")

    first = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
        run_id="valid_20260704-1.ok",
    )
    second = run_pipeline(
        mode="sdk",
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        optimizer_config_path=DEFAULT_OPTIMIZER_CONFIG,
        prompt_path=DEFAULT_PROMPT,
        output_dir=tmp_path / "sdk_run",
        sdk_call_agent="fake_call_agent_module:call_agent",
        run_id="valid_20260704-1.ok",
    )

    assert first.run["run_id"] == "valid_20260704-1.ok"
    assert second.run["run_id"] == "valid_20260704-1.ok"


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
    assert report.selected_candidate == "sdk_best"
    assert decision.accepted is True
    assert decision.gate_status == "applied"
    assert decision.validation_score_delta == 0.333333
    assert any("validation score improved" in reason for reason in decision.reasons)


def test_run_pipeline_mode_sdk_custom_gate_rejects_low_post_eval_validation_improvement(
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
    gate_path = _write_gate_config(tmp_path, min_val_score_improvement=0.5, max_total_cost=1.0)

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
    assert decision.validation_score_delta == 0.333333
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
    assert decision.gate_status == "applied"
    assert decision.total_run_cost == 2.0
    assert any("cost" in reason for reason in decision.reasons)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("min_val_score_improvement", True),
        ("max_total_cost", float("nan")),
        ("max_total_cost", float("inf")),
    ],
)
def test_run_pipeline_mode_sdk_rejects_invalid_gate_numbers(
    tmp_path: Path,
    monkeypatch,
    field_name,
    field_value,
):
    _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")
    gate = {"min_val_score_improvement": 0.01, "max_total_cost": 1.0}
    gate[field_name] = field_value
    gate_path = tmp_path / "bad_gate.json"
    gate_path.write_text(json.dumps({"gate": gate}), encoding="utf-8")

    with pytest.raises(ValueError, match=f"--gate-config.*{field_name}"):
        run_pipeline(
            mode="sdk",
            train_path=DEFAULT_TRAIN,
            val_path=DEFAULT_VAL,
            optimizer_config_path=_write_sdk_optimizer_config(tmp_path),
            prompt_path=DEFAULT_PROMPT,
            output_dir=tmp_path / "sdk_run",
            sdk_call_agent="fake_call_agent_module:call_agent",
            gate_config_path=gate_path,
        )


@pytest.mark.parametrize("run_id", ["../../escape", "a/b", "", ".", "..", "has space", "a\\b"])
def test_run_pipeline_rejects_invalid_run_id(tmp_path: Path, monkeypatch, run_id):
    _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")

    with pytest.raises(ValueError, match="--run-id") as exc_info:
        run_pipeline(
            mode="sdk",
            train_path=DEFAULT_TRAIN,
            val_path=DEFAULT_VAL,
            optimizer_config_path=_write_sdk_optimizer_config(tmp_path),
            prompt_path=DEFAULT_PROMPT,
            output_dir=tmp_path / "sdk_run",
            sdk_call_agent="fake_call_agent_module:call_agent",
            run_id=run_id,
        )
    assert repr(run_id) in str(exc_info.value)


@pytest.mark.parametrize(
    "field_name",
    ["../router", "router/prompt", "router prompt", "router.prompt", "router-prompt", "", " router_prompt"],
)
def test_run_pipeline_mode_sdk_rejects_invalid_target_prompt_field_names(
    tmp_path: Path,
    monkeypatch,
    field_name,
):
    _install_fake_sdk(monkeypatch, best_prompt="optimized prompt")
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    with pytest.raises(ValueError, match="--target-prompt") as exc_info:
        run_pipeline(
            mode="sdk",
            train_path=DEFAULT_TRAIN,
            val_path=DEFAULT_VAL,
            optimizer_config_path=_write_sdk_optimizer_config(tmp_path),
            prompt_path=DEFAULT_PROMPT,
            output_dir=tmp_path / "sdk_run",
            sdk_call_agent="fake_call_agent_module:call_agent",
            target_prompts=[f"{field_name}={prompt_path}"],
        )
    assert repr(field_name) in str(exc_info.value)


def test_target_prompt_paths_reject_same_resolved_file(tmp_path: Path):
    prompt_path = tmp_path / "prompt.txt"
    equivalent_path = tmp_path / "nested" / ".." / prompt_path.name
    prompt_path.write_text("baseline", encoding="utf-8")

    with pytest.raises(ValueError, match="same resolved file"):
        _parse_target_prompt_paths(
            [
                f"system_prompt={prompt_path}",
                f"router_prompt={equivalent_path}",
            ],
            default_prompt_path=prompt_path,
        )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("pass_rate_improvement", float("nan")),
        ("total_llm_cost", float("inf")),
        ("best_pass_rate", "bad"),
    ],
)
def test_run_pipeline_mode_sdk_rejects_non_finite_or_bad_numeric_summary(
    tmp_path: Path,
    monkeypatch,
    field_name,
    field_value,
):
    kwargs = {
        "baseline_pass_rate": 0.5,
        "best_pass_rate": 0.75,
        "pass_rate_improvement": 0.25,
        "total_llm_cost": 0.123,
    }
    kwargs[field_name] = field_value
    _install_fake_sdk(monkeypatch, best_prompt="optimized prompt", **kwargs)

    with pytest.raises(ValueError, match=f"SDK OptimizeResult field {field_name} must be a finite number"):
        run_pipeline(
            mode="sdk",
            train_path=DEFAULT_TRAIN,
            val_path=DEFAULT_VAL,
            optimizer_config_path=_write_sdk_optimizer_config(tmp_path),
            prompt_path=DEFAULT_PROMPT,
            output_dir=tmp_path / "sdk_run",
            sdk_call_agent="fake_call_agent_module:call_agent",
        )


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
    assert (run_dir / "candidate_prompts" / "sdk_best" / "bundle.txt").read_text(
        encoding="utf-8"
    ) == report.candidates[0]["candidate"].prompt
    input_hashes = json.loads((run_dir / "input_hashes.json").read_text(encoding="utf-8"))
    assert set(input_hashes["target_prompts"]) == {"system_prompt", "router_prompt", "skill_prompt"}
    assert "gate_config" in input_hashes
    command = report.run["reproducibility_command"]
    assert "--sdk-call-agent fake_call_agent_module:call_agent" in command
    assert "--target-prompt" in command
    assert f"router_prompt={router_path}" in command
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

    class FakeAgentEvaluator:
        @staticmethod
        def get_executer(eval_dataset_file_path_or_dir, **kwargs):
            return FakeEvalExecuter(Path(eval_dataset_file_path_or_dir))

    class FakeEvalExecuter:
        def __init__(self, eval_path: Path):
            self.eval_path = eval_path
            self._result = None

        async def evaluate(self):
            split = "train" if "train" in self.eval_path.name else "validation"
            prompt_label = _current_prompt_label(calls)
            calls.setdefault("agent_evaluator_runs", []).append((prompt_label, split))
            scores = _fake_eval_scores(self.eval_path, split=split, prompt_label=prompt_label)
            self._result = types.SimpleNamespace(
                results_by_eval_set_id={
                    f"{split}_set": types.SimpleNamespace(
                        eval_results_by_eval_id={
                            case_id: [_fake_case_result(case_id, score)]
                            for case_id, score in scores
                        }
                    )
                }
            )
            if any(score < 1.0 for _, score in scores):
                raise AssertionError("evaluation cases failed")

        def get_result(self):
            return self._result

    fake_eval_module = types.ModuleType("trpc_agent_sdk.evaluation")
    fake_eval_module.AgentOptimizer = FakeAgentOptimizer
    fake_eval_module.AgentEvaluator = FakeAgentEvaluator
    fake_eval_module.TargetPrompt = FakeTargetPrompt
    monkeypatch.setitem(sys.modules, "trpc_agent_sdk.evaluation", fake_eval_module)

    call_agent_module = types.ModuleType("fake_call_agent_module")

    async def call_agent(query: str) -> str:
        return query

    call_agent_module.call_agent = call_agent
    monkeypatch.setitem(sys.modules, "fake_call_agent_module", call_agent_module)
    return calls


def _current_prompt_label(calls: dict) -> str:
    target_prompt = calls.get("target_prompt")
    paths = getattr(target_prompt, "paths", []) if target_prompt is not None else []
    contents = [
        Path(path).read_text(encoding="utf-8")
        for _, path in paths
        if Path(path).is_file()
    ]
    return "sdk_best" if any("optimized" in content for content in contents) else "baseline"


def _fake_eval_scores(eval_path: Path, *, split: str, prompt_label: str) -> list[tuple[str, float]]:
    case_ids = _case_ids(eval_path)
    if not case_ids:
        return []
    if prompt_label == "sdk_best":
        return [(case_id, 1.0) for case_id in case_ids]
    baseline_scores = {
        "train_json_refund": 0.0,
        "train_exact_order_status": 0.0,
        "train_rubric_retry_summary": 1.0,
        "val_json_invoice": 0.0,
        "val_explain_cache": 1.0,
        "val_protected_yes_no": 1.0,
    }
    return [(case_id, baseline_scores.get(case_id, 0.5 if split == "validation" else 0.0)) for case_id in case_ids]


def _case_ids(eval_path: Path) -> list[str]:
    payload = json.loads(eval_path.read_text(encoding="utf-8"))
    cases = payload.get("cases") or payload.get("eval_cases") or payload.get("evalCases") or []
    ids = []
    for case in cases:
        if isinstance(case, dict):
            case_id = case.get("id") or case.get("case_id") or case.get("eval_id") or case.get("evalId")
            if case_id:
                ids.append(str(case_id))
    return ids


def _fake_case_result(case_id: str, score: float):
    passed = score >= 1.0
    status = "PASSED" if passed else "FAILED"
    return types.SimpleNamespace(
        eval_id=case_id,
        final_eval_status=status,
        error_message=None if passed else "response did not match expectation",
        overall_eval_metric_results=[
            types.SimpleNamespace(
                metric_name="response_match_score",
                score=score,
                eval_status=status,
                details=types.SimpleNamespace(reason="response did not match expectation"),
            )
        ],
        eval_metric_result_per_invocation=[],
    )


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
