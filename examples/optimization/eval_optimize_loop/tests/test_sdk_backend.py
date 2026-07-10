from __future__ import annotations

import builtins
import inspect
import json
import sys
import types
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.eval_loop import backends as backend_module
from examples.optimization.eval_optimize_loop.eval_loop.backends import SDKBackend
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalCase
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalResult
from examples.optimization.eval_optimize_loop.eval_loop.schemas import OptimizationResult
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_OPTIMIZER_CONFIG
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_PROMPT
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_TRAIN
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_VAL
from examples.optimization.eval_optimize_loop.run_pipeline import _parse_target_prompt_paths
from examples.optimization.eval_optimize_loop.run_pipeline import _sdk_gate_decision
from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline


def test_backend_protocols_expose_unified_async_api():
    assert inspect.iscoroutinefunction(backend_module.EvaluationBackend.evaluate)
    assert inspect.iscoroutinefunction(backend_module.OptimizationBackend.optimize_candidates)
    assert inspect.iscoroutinefunction(backend_module.FakeBackend.evaluate)
    assert inspect.iscoroutinefunction(backend_module.FakeBackend.optimize_candidates)
    assert inspect.iscoroutinefunction(backend_module.SDKBackend.evaluate)
    assert inspect.iscoroutinefunction(backend_module.SDKBackend.optimize_candidates)


@pytest.mark.asyncio
async def test_fake_backend_implements_unified_contract_with_real_trace(tmp_path: Path):
    prompt = "baseline prompt"
    dataset_path = tmp_path / "fake_train.evalset.json"
    dataset_path.write_text(
        json.dumps({
            "split": "train",
            "cases": [{
                "case_id": "case_a",
                "input": "Mention latency and retries.",
                "expectation": {
                    "type": "rubric",
                    "must_include": ["latency", "retries"],
                },
            }],
        }),
        encoding="utf-8",
    )
    backend = backend_module.FakeBackend(seed=91)

    result = await backend.evaluate(
        prompt_id="baseline",
        prompts={"system_prompt": prompt},
        dataset_path=dataset_path,
        split="train",
        trace=True,
        artifact_dir=tmp_path / "fake_eval",
    )

    assert result.cases
    assert all(case.metrics == {"fake_judge_score": case.score} for case in result.cases)
    assert all(case.trace_available is True and case.trace for case in result.cases)

    without_trace = await backend.evaluate(
        prompt_id="baseline_without_trace",
        prompts={"system_prompt": prompt},
        dataset_path=dataset_path,
        split="train",
        trace=False,
        artifact_dir=tmp_path / "fake_eval_without_trace",
    )
    assert all(case.trace_available is False and not case.trace for case in without_trace.cases)

    with pytest.raises(ValueError, match="system_prompt"):
        await backend.evaluate(
            prompt_id="missing_system_prompt",
            prompts={},
            dataset_path=dataset_path,
            split="train",
            trace=False,
            artifact_dir=tmp_path / "missing",
        )


@pytest.mark.asyncio
async def test_fake_backend_wraps_candidates_in_complete_optimization_result(tmp_path: Path):
    baseline_prompt = DEFAULT_PROMPT.read_text(encoding="utf-8")
    baseline_train = _empty_eval_result("baseline", "train")

    result = await backend_module.FakeBackend(seed=91).optimize_candidates(
        baseline_prompts={"system_prompt": baseline_prompt},
        baseline_train=baseline_train,
        failure_summary={"failed_case_ids": ["train_json_strict"]},
        train_path=DEFAULT_TRAIN,
        validation_path=DEFAULT_VAL,
        config_path=DEFAULT_OPTIMIZER_CONFIG,
        artifact_dir=tmp_path / "fake_optimize",
    )

    assert isinstance(result, OptimizationResult)
    assert result.candidates
    assert result.cost.complete is True
    assert result.cost.total == 0.0
    assert len(result.rounds) == len(result.candidates)
    assert all(candidate.bundle() == {"system_prompt": candidate.prompt} for candidate in result.candidates)


@pytest.mark.asyncio
async def test_sdk_backend_maps_rounds_deduplicates_bundles_and_marks_cost_incomplete(
    tmp_path: Path,
    monkeypatch,
):
    round_prompts = {
        "system_prompt": "round system",
        "router_prompt": "round router",
    }
    rounds = [
        _sdk_round(
            1,
            round_prompts,
            acceptance_reason="first proposal",
            metric_breakdown={"quality": 0.6},
            round_llm_cost=0.1,
            duration_seconds=0.5,
            accepted=False,
        ),
        _sdk_round(
            2,
            dict(round_prompts),
            acceptance_reason="duplicate proposal",
            metric_breakdown={"quality": 0.7},
            round_llm_cost=0.2,
            duration_seconds=0.75,
            accepted=True,
        ),
    ]
    calls = _install_fake_sdk(
        monkeypatch,
        best_prompts=round_prompts,
        rounds=rounds,
        total_llm_cost=0.3,
    )
    system_path = tmp_path / "system.txt"
    router_path = tmp_path / "router.txt"
    system_path.write_text("baseline system", encoding="utf-8")
    router_path.write_text("baseline router", encoding="utf-8")
    baseline_prompts = {
        "system_prompt": "baseline system",
        "router_prompt": "baseline router",
    }
    backend = SDKBackend(
        prompt_path=system_path,
        call_agent_path="fake_call_agent_module:call_agent",
        target_prompt_paths={"system_prompt": system_path, "router_prompt": router_path},
    )

    result = await backend.optimize_candidates(
        baseline_prompts=baseline_prompts,
        baseline_train=_empty_eval_result("baseline", "train"),
        failure_summary={"failed_case_ids": ["case_a"]},
        train_path=tmp_path / "train.evalset.json",
        validation_path=tmp_path / "validation.evalset.json",
        config_path=tmp_path / "optimizer.json",
        artifact_dir=tmp_path / "sdk_optimize",
    )

    assert calls["update_source"] is False
    assert [candidate.candidate_id for candidate in result.candidates] == ["sdk_round_001"]
    assert result.candidates[0].prompt_fields == round_prompts
    assert result.candidates[0].bundle() == round_prompts
    assert result.candidates[0].rationale == "first proposal"
    assert "router_prompt" in result.candidates[0].prompt_diff
    assert [round_record.round_id for round_record in result.rounds] == [1, 2]
    assert result.rounds[0].metrics == {"quality": 0.6}
    assert result.rounds[1].cost.total == 0.2
    assert result.cost.total == 0.3
    assert result.cost.complete is False
    assert result.raw_summary["rounds"][1]["acceptance_reason"] == "duplicate proposal"
    assert result.raw_summary["rounds"][1]["accepted"] is True


@pytest.mark.asyncio
async def test_sdk_backend_appends_best_when_no_round_contains_it(tmp_path: Path, monkeypatch):
    round_prompts = {"system_prompt": "round prompt"}
    best_prompts = {"system_prompt": "best prompt"}
    _install_fake_sdk(
        monkeypatch,
        best_prompts=best_prompts,
        rounds=[_sdk_round(4, round_prompts, acceptance_reason="explored")],
    )
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("baseline", encoding="utf-8")

    result = await SDKBackend(
        prompt_path=prompt_path,
        call_agent_path="fake_call_agent_module:call_agent",
    ).optimize_candidates(
        baseline_prompts={"system_prompt": "baseline"},
        baseline_train=_empty_eval_result("baseline", "train"),
        failure_summary={},
        train_path=tmp_path / "train.evalset.json",
        validation_path=tmp_path / "validation.evalset.json",
        config_path=tmp_path / "optimizer.json",
        artifact_dir=tmp_path / "sdk_optimize",
    )

    assert [candidate.candidate_id for candidate in result.candidates] == ["sdk_round_004", "sdk_best"]
    assert result.candidates[1].prompt_fields == best_prompts


def test_sdk_result_mapping_preserves_metrics_trace_and_expected_label():
    expected_case = EvalCase(
        case_id="case_a",
        split="validation",
        input="question",
        expectation={"answer": "expected"},
        expected_failure_category="format_violation",
    )
    first_run = _sdk_case_run(
        "case_a",
        status="FAILED",
        metrics=[
            _sdk_metric("response_match", 0.5, status="FAILED", reason="wrong response"),
            _sdk_metric("style", 0.25, status="PASSED"),
        ],
        output="first output",
        user_content="first question",
        intermediate_data={"step": 1},
    )
    last_run = _sdk_case_run(
        "case_a",
        status="FAILED",
        metrics=[
            _sdk_metric("response_match", 1.0, status="PASSED"),
            _sdk_metric("style", 0.75, status="PASSED"),
        ],
        output="last output",
        user_content="last question",
        intermediate_data={"step": 2},
    )

    mapped = backend_module._eval_result_from_sdk_result(
        _sdk_evaluate_result({"case_a": [first_run, last_run]}),
        prompt_id="candidate",
        split="validation",
        expected_cases=[expected_case],
    )

    case = mapped.cases[0]
    assert case.metrics == {"response_match": 0.75, "style": 0.5}
    assert case.score == 0.625
    assert case.output == "last output"
    assert case.trace_available is True
    assert case.trace == {
        "user_content": {"parts": [{"text": "last question"}]},
        "final_response": {"parts": [{"text": "last output"}]},
        "intermediate_data": {"step": 2},
    }
    assert case.expected_failure_category == "format_violation"
    assert case.failure_category == "final_response_mismatch"
    assert case.failure_reason == "wrong response"
    assert case.hard_failed is False


@pytest.mark.parametrize(
    ("sdk_case_ids", "expected_case_ids", "message"),
    [
        (["case_a"], ["case_a", "case_b"], "missing.*case_b"),
        (["case_a", "case_b"], ["case_a"], "extra.*case_b"),
    ],
)
def test_sdk_result_mapping_rejects_case_id_set_mismatch(sdk_case_ids, expected_case_ids, message):
    sdk_runs = {
        case_id: [_sdk_case_run(case_id, status="PASSED", metrics=[])]
        for case_id in sdk_case_ids
    }
    expected_cases = [
        EvalCase(case_id=case_id, split="validation", input="", expectation={})
        for case_id in expected_case_ids
    ]

    with pytest.raises(ValueError, match=message):
        backend_module._eval_result_from_sdk_result(
            _sdk_evaluate_result(sdk_runs),
            prompt_id="candidate",
            split="validation",
            expected_cases=expected_cases,
        )


def test_sdk_result_mapping_rejects_duplicate_case_ids_across_eval_sets():
    run = _sdk_case_run("case_a", status="PASSED", metrics=[])
    result = types.SimpleNamespace(
        results_by_eval_set_id={
            "set_a": types.SimpleNamespace(eval_results_by_eval_id={"case_a": [run]}),
            "set_b": types.SimpleNamespace(eval_results_by_eval_id={"case_a": [run]}),
        }
    )
    expected = [EvalCase(case_id="case_a", split="validation", input="", expectation={})]

    with pytest.raises(ValueError, match="duplicate.*case_a"):
        backend_module._eval_result_from_sdk_result(
            result,
            prompt_id="candidate",
            split="validation",
            expected_cases=expected,
        )


def test_sdk_result_mapping_rejects_non_finite_metric_scores():
    expected = [EvalCase(case_id="case_a", split="validation", input="", expectation={})]
    run = _sdk_case_run(
        "case_a",
        status="FAILED",
        metrics=[_sdk_metric("quality", float("nan"), status="FAILED")],
    )

    with pytest.raises(ValueError, match="finite"):
        backend_module._eval_result_from_sdk_result(
            _sdk_evaluate_result({"case_a": [run]}),
            prompt_id="candidate",
            split="validation",
            expected_cases=expected,
        )


@pytest.mark.asyncio
async def test_sdk_backend_evaluate_temporarily_installs_and_restores_prompt_bytes(
    tmp_path: Path,
    monkeypatch,
):
    dataset_path = tmp_path / "validation.evalset.json"
    dataset_path.write_text(
        json.dumps({
            "split": "validation",
            "cases": [{
                "case_id": "case_a",
                "input": "question",
                "expectation": {"answer": "answer"},
                "expected_failure_category": "format_violation",
            }],
        }),
        encoding="utf-8",
    )
    prompt_path = tmp_path / "prompt.txt"
    original_bytes = b"original prompt\r\n"
    prompt_path.write_bytes(original_bytes)
    sdk_result = _sdk_evaluate_result({
        "case_a": [
            _sdk_case_run(
                "case_a",
                status="PASSED",
                metrics=[_sdk_metric("quality", 1.0, status="PASSED")],
                output="answer",
                user_content="question",
                intermediate_data={"tool": "none"},
            )
        ]
    })
    calls = _install_fake_agent_evaluator(
        monkeypatch,
        result=sdk_result,
        on_evaluate=lambda: prompt_path.read_text(encoding="utf-8") == "candidate prompt",
        raise_after_evaluate=True,
    )
    backend = SDKBackend(
        prompt_path=prompt_path,
        call_agent_path="fake_call_agent_module:call_agent",
    )

    mapped = await backend.evaluate(
        prompt_id="candidate",
        prompts={"system_prompt": "candidate prompt"},
        dataset_path=dataset_path,
        split="validation",
        trace=False,
        artifact_dir=tmp_path / "sdk_eval",
    )

    assert calls["observed_candidate"] is True
    assert calls["eval_result_output_dir"] == str(tmp_path / "sdk_eval")
    assert prompt_path.read_bytes() == original_bytes
    assert mapped.cases[0].trace_available is True


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


def test_sdk_backend_never_delegates_source_writeback(tmp_path: Path, monkeypatch):
    calls = _install_fake_sdk(
        monkeypatch,
        best_prompt="optimized prompt",
        write_source_when_requested=True,
    )
    prompt_path = tmp_path / "prompt.txt"
    original_bytes = b"baseline\r\n"
    prompt_path.write_bytes(original_bytes)

    backend = SDKBackend(
        prompt_path=prompt_path,
        call_agent_path="fake_call_agent_module:call_agent",
        update_source=True,
    )
    backend.optimize(
        baseline_prompt="baseline\n",
        train_path=tmp_path / "train.evalset.json",
        val_path=tmp_path / "val.evalset.json",
        optimizer_config_path=tmp_path / "optimizer.json",
        output_dir=tmp_path / "out",
    )

    assert calls["update_source"] is False
    assert prompt_path.read_bytes() == original_bytes
    assert "update_source" not in vars(backend)


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


def test_sdk_gate_decision_skips_disabled_cost_gate_but_keeps_quality_threshold():
    decision = _sdk_gate_decision(
        candidate_id="sdk_best",
        sdk_summary={
            "status": "SUCCEEDED",
            "pass_rate_improvement": 0.005,
            "total_llm_cost": 1000.0,
        },
        gate_config={
            "min_val_score_improvement": 0.01,
            "max_total_cost": None,
        },
    )

    assert decision.accepted is False
    assert any("validation improvement" in reason for reason in decision.reasons)
    assert not any("cost" in reason for reason in decision.reasons)
    assert decision.total_run_cost == 1000.0


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

    with pytest.raises(ValueError) as exc_info:
        _parse_target_prompt_paths(
            [
                f"system_prompt={prompt_path}",
                f"router_prompt={equivalent_path}",
            ],
            default_prompt_path=prompt_path,
        )

    assert str(exc_info.value) == "--target-prompt fields must not reference the same resolved file"


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


def test_run_pipeline_mode_sdk_keeps_source_writeback_outside_backend(tmp_path: Path, monkeypatch):
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
    assert calls["update_source"] is False
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
    rounds: list[object] | None = None,
    write_source_when_requested: bool = False,
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
            if write_source_when_requested and kwargs.get("update_source"):
                for _, path in kwargs["target_prompt"].paths:
                    Path(path).write_bytes(b"optimizer mutated source")
            result_prompts = best_prompts if best_prompts is not None else {
                "system_prompt": "optimized prompt" if best_prompt is None else best_prompt
            }
            effective_rounds = rounds if rounds is not None else [
                _sdk_round(
                    1,
                    {},
                    acceptance_reason="accepted",
                    validation_pass_rate=best_pass_rate,
                    accepted=True,
                    failed_case_ids=["case_a"],
                    round_llm_cost=total_llm_cost,
                    duration_seconds=duration_seconds,
                )
            ]
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
                total_rounds=len(effective_rounds),
                rounds=effective_rounds,
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


def _empty_eval_result(prompt_id: str, split: str) -> EvalResult:
    return EvalResult(
        prompt_id=prompt_id,
        split=split,
        score=0.0,
        passed=False,
        cost=0.0,
        cases=[],
    )


def _sdk_round(
    round_id: int,
    candidate_prompts: dict[str, str],
    *,
    acceptance_reason: str = "",
    metric_breakdown: dict[str, float] | None = None,
    validation_pass_rate: float = 0.0,
    round_llm_cost: float = 0.0,
    duration_seconds: float = 0.0,
    accepted: bool = False,
    failed_case_ids: list[str] | None = None,
):
    return types.SimpleNamespace(
        round=round_id,
        optimized_field_names=list(candidate_prompts),
        candidate_prompts=dict(candidate_prompts),
        train_pass_rate=0.0,
        validation_pass_rate=validation_pass_rate,
        metric_breakdown=dict(metric_breakdown or {}),
        accepted=accepted,
        acceptance_reason=acceptance_reason,
        failed_case_ids=list(failed_case_ids or []),
        failed_cases_truncated=0,
        per_field_diagnosis={},
        reflection_lm_calls=1,
        round_llm_cost=round_llm_cost,
        round_token_usage={"prompt": 0, "completion": 0, "total": 0},
        started_at="2026-07-04T12:00:00+00:00",
        duration_seconds=duration_seconds,
        kind="reflective",
        train_minibatch_size=0,
        train_subsample_parent_score=None,
        train_subsample_candidate_score=None,
        skip_reason=None,
        error_message=None,
        budget_used=3,
        budget_total=10,
        extras={},
    )


def _sdk_metric(metric_name: str, score: float, *, status: str, reason: str | None = None):
    return types.SimpleNamespace(
        metric_name=metric_name,
        threshold=0.5,
        score=score,
        eval_status=status,
        details=types.SimpleNamespace(reason=reason, score=score, rubric_scores=None),
    )


def _sdk_case_run(
    case_id: str,
    *,
    status: str,
    metrics: list[object],
    output: str | None = None,
    user_content: str | None = None,
    intermediate_data: object | None = None,
):
    per_invocation = []
    if output is not None or user_content is not None or intermediate_data is not None:
        actual_invocation = types.SimpleNamespace(
            invocation_id=f"{case_id}_invocation",
            user_content={"parts": [{"text": user_content or ""}]},
            final_response={"parts": [{"text": output or ""}]} if output is not None else None,
            intermediate_data=intermediate_data,
            creation_timestamp=0.0,
        )
        per_invocation.append(
            types.SimpleNamespace(
                actual_invocation=actual_invocation,
                expected_invocation=None,
                eval_metric_results=list(metrics),
            )
        )
    return types.SimpleNamespace(
        eval_set_id="set_a",
        eval_id=case_id,
        run_id=1,
        final_eval_status=status,
        error_message=None if status == "PASSED" else "case failed",
        overall_eval_metric_results=list(metrics),
        eval_metric_result_per_invocation=per_invocation,
        session_id=f"session_{case_id}",
        user_id="test_user",
        session_details=None,
    )


def _sdk_evaluate_result(runs_by_case_id: dict[str, list[object]]):
    return types.SimpleNamespace(
        results_by_eval_set_id={
            "set_a": types.SimpleNamespace(
                eval_results_by_eval_id=dict(runs_by_case_id),
                num_runs=max((len(runs) for runs in runs_by_case_id.values()), default=1),
            )
        }
    )


def _install_fake_agent_evaluator(
    monkeypatch,
    *,
    result: object,
    on_evaluate,
    raise_after_evaluate: bool,
):
    calls: dict[str, object] = {}

    class FakeExecuter:
        async def evaluate(self):
            calls["observed_candidate"] = bool(on_evaluate())
            if raise_after_evaluate:
                raise RuntimeError("case failed after producing a structured result")

        def get_result(self):
            return result

    class FakeAgentEvaluator:
        @staticmethod
        def get_executer(dataset_path, **kwargs):
            calls["dataset_path"] = dataset_path
            calls.update(kwargs)
            return FakeExecuter()

    fake_eval_module = types.ModuleType("trpc_agent_sdk.evaluation")
    fake_eval_module.AgentEvaluator = FakeAgentEvaluator
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
