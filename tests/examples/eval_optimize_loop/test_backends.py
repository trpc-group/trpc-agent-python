"""Backend and candidate-generator substitution contract tests."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from trpc_agent_sdk.evaluation import (
    AgentEvaluator,
    EvalConfig,
    EvalSet,
    EvalStatus,
    TargetPrompt,
)

from examples.optimization.eval_optimize_loop.pipeline import backends as backends_module
from examples.optimization.eval_optimize_loop.pipeline import optimizer_worker as optimizer_worker_module
from examples.optimization.eval_optimize_loop.pipeline.backends import (
    DeterministicCandidateGenerator,
    FakeEvaluationBackend,
    LiveCandidateGenerator,
    LiveEvaluationBackend,
    TraceEvaluationBackend,
)
from examples.optimization.eval_optimize_loop.pipeline.evaluation import normalize_result
from examples.optimization.eval_optimize_loop.pipeline.live_adapter import (
    LiveAdapterSpec,
    load_verified_callback,
)
from examples.optimization.eval_optimize_loop.pipeline.offline_evaluation import prepare_offline_evaluation
from examples.optimization.eval_optimize_loop.pipeline.models import (
    AttributionSnapshot,
    Phase,
    Split,
)


def _eval_set() -> EvalSet:
    return EvalSet.model_validate({
        "evalSetId":
        "backend_contract",
        "evalCases": [{
            "evalId":
            "case",
            "conversation": [{
                "invocationId": "expected",
                "userContent": {
                    "role": "user",
                    "parts": [{
                        "text": "BEHAVIOR:stable; ANSWER:OK"
                    }],
                },
                "finalResponse": {
                    "role": "model",
                    "parts": [{
                        "text": "OK"
                    }]
                },
            }],
            "sessionInput": {
                "appName": "backend",
                "userId": "test",
                "state": {}
            },
        }],
    })


def _config() -> EvalConfig:
    return EvalConfig(
        metrics=[{
            "metric_name": "final_response_avg_score",
            "threshold": 1,
            "criterion": {
                "final_response": {
                    "text": {
                        "match": "exact"
                    }
                }
            },
        }],
        num_runs=1,
    )


async def importable_call_agent(query: str) -> str:
    return query


def _live_adapter() -> LiveAdapterSpec:
    return LiveAdapterSpec.resolve(f"{__name__}:importable_call_agent", importable_call_agent)


def test_worker_callback_verification_rejects_source_drift(tmp_path) -> None:
    adapter = _live_adapter()
    assert load_verified_callback(
        adapter.import_path,
        expected_source_path=str(adapter.source_path),
        expected_source_sha256=adapter.source_sha256,
        expected_callable_sha256=adapter.callable_sha256,
    ) is importable_call_agent
    with pytest.raises(ValueError, match="source path differs"):
        load_verified_callback(
            adapter.import_path,
            expected_source_path=str(tmp_path / "shadowed.py"),
            expected_source_sha256=adapter.source_sha256,
            expected_callable_sha256=adapter.callable_sha256,
        )
    with pytest.raises(ValueError, match="source hash differs"):
        load_verified_callback(
            adapter.import_path,
            expected_source_path=str(adapter.source_path),
            expected_source_sha256="0" * 64,
            expected_callable_sha256=adapter.callable_sha256,
        )
    with pytest.raises(ValueError, match="callback code differs"):
        load_verified_callback(
            adapter.import_path,
            expected_source_path=str(adapter.source_path),
            expected_source_sha256=adapter.source_sha256,
            expected_callable_sha256="0" * 64,
        )


def _install_worker_result(monkeypatch, result, captured=None) -> None:
    class CompletedProcess:
        returncode = 0

        async def wait(self):
            return self.returncode

    async def fake_subprocess(*args, **kwargs):
        request_path = Path(args[-1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        output_dir = Path(request["outputDir"])
        output_dir.mkdir(parents=True)
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        if captured is not None:
            captured.update({"args": args, "kwargs": kwargs, "request": request})
        return CompletedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(
        backends_module,
        "OptimizeResult",
        SimpleNamespace(from_file=lambda path: result),
    )


@pytest.mark.asyncio
async def test_fake_backend_uses_public_services_and_does_not_mutate_inputs(tmp_path, monkeypatch) -> None:
    backend = FakeEvaluationBackend()
    eval_set = _eval_set()
    config = _config()
    before_set = eval_set.model_dump(mode="json")
    before_config = config.model_dump(mode="json")
    calls = 0
    original = AgentEvaluator.evaluate_eval_set

    async def recording_evaluate_eval_set(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(AgentEvaluator, "evaluate_eval_set", recording_evaluate_eval_set)
    raw = await backend.evaluate(
        eval_set=eval_set,
        eval_config=config,
        prompts={"system": "baseline"},
        split=Split.TRAIN,
        phase=Phase.BASELINE,
        audit_dir=str(tmp_path),
    )
    snapshot = normalize_result(
        raw,
        eval_set,
        config,
        split=Split.TRAIN,
        phase=Phase.BASELINE,
    )
    assert snapshot.pass_rate == 1
    assert calls == 1
    assert eval_set.model_dump(mode="json") == before_set
    assert config.model_dump(mode="json") == before_config


@pytest.mark.asyncio
async def test_fake_llm_metric_uses_deterministic_offline_substitute(tmp_path, monkeypatch) -> None:

    def forbid_real_judge(*args, **kwargs):
        raise AssertionError("fake mode constructed a real LLM judge")

    monkeypatch.setattr(
        AgentEvaluator,
        "evaluate_eval_set",
        lambda *args, **kwargs: pytest.fail("custom registry must remain run-local"),
    )
    monkeypatch.setattr(
        "trpc_agent_sdk.evaluation._llm_judge.LLMJudge.__init__",
        forbid_real_judge,
    )
    config = EvalConfig(
        metrics=[
            {
                "metric_name": "llm_final_response",
                "threshold": 1,
                "criterion": {
                    "llm_judge": {
                        "judge_model": {
                            "model_name": "must-not-load"
                        }
                    }
                },
            },
            {
                "metric_name": "llm_rubric_response",
                "threshold": 1,
                "criterion": {
                    "llm_judge": {
                        "judge_model": {
                            "model_name": "must-not-load"
                        },
                        "rubrics": [{
                            "id": "response_quality",
                            "content": {
                                "text": "The response is correct."
                            },
                            "type": "OFFLINE_RESPONSE_EXACT_REFERENCE",
                        }],
                    }
                },
            },
        ],
        num_runs=1,
    )
    eval_set = _eval_set()
    raw = await FakeEvaluationBackend().evaluate(
        eval_set=eval_set,
        eval_config=config,
        prompts={"system": "baseline"},
        split=Split.TRAIN,
        phase=Phase.BASELINE,
        audit_dir=str(tmp_path),
    )
    snapshot = normalize_result(
        raw,
        eval_set,
        config,
        split=Split.TRAIN,
        phase=Phase.BASELINE,
    )
    assert snapshot.metric_scores == {
        "llm_final_response": 1,
        "llm_rubric_response": 1,
    }
    run_metrics = {metric.metric_name: metric for metric in snapshot.cases[0].runs[0].metrics}
    assert [rubric.id for rubric in run_metrics["llm_rubric_response"].rubrics] == ["response_quality"]


def test_offline_rubric_empty_invocations_are_a_hard_failure() -> None:
    config = EvalConfig(
        metrics=[{
            "metric_name": "llm_rubric_response",
            "threshold": 1,
            "criterion": {
                "llm_judge": {
                    "judge_model": {
                        "model_name": "must-not-load"
                    },
                    "rubrics": [{
                        "id": "response_quality",
                        "type": "OFFLINE_RESPONSE_NON_EMPTY",
                    }],
                }
            },
        }],
        num_runs=1,
    )
    offline_config, registry = prepare_offline_evaluation(config)
    assert registry is not None
    evaluator = registry.get_evaluator(offline_config.get_eval_metrics()[0])

    result = evaluator.evaluate_invocations([], None)

    assert result.overall_score == 0.0
    assert result.overall_eval_status == EvalStatus.FAILED
    assert result.per_invocation_results == []


@pytest.mark.asyncio
async def test_fake_knowledge_recall_fails_before_black_box_inference(tmp_path, monkeypatch) -> None:

    def fail_if_called(*args, **kwargs):
        raise AssertionError("black-box inference ran before capability validation")

    monkeypatch.setattr(
        "examples.optimization.eval_optimize_loop.pipeline.backends.deterministic_fake_response",
        fail_if_called,
    )
    config = EvalConfig(
        metrics=[{
            "metric_name": "llm_rubric_knowledge_recall",
            "threshold": 1,
            "criterion": {
                "llm_judge": {
                    "judge_model": {
                        "model_name": "must-not-load"
                    },
                    "rubrics": [{
                        "id": "knowledge_evidence",
                        "type": "OFFLINE_KNOWLEDGE_NON_EMPTY",
                    }],
                }
            },
        }],
        num_runs=1,
    )
    with pytest.raises(ValueError, match="does not support metrics"):
        await FakeEvaluationBackend().evaluate(
            eval_set=_eval_set(),
            eval_config=config,
            prompts={"system": "baseline"},
            split=Split.TRAIN,
            phase=Phase.BASELINE,
            audit_dir=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_offline_rubric_rejects_unstructured_natural_language(tmp_path) -> None:
    config = EvalConfig(
        metrics=[{
            "metric_name": "llm_rubric_response",
            "threshold": 1,
            "criterion": {
                "llm_judge": {
                    "judge_model": {
                        "model_name": "must-not-load"
                    },
                    "rubrics": [{
                        "id": "quality",
                        "content": {
                            "text": "Be correct."
                        }
                    }],
                }
            },
        }],
        num_runs=1,
    )
    with pytest.raises(ValueError, match="requires an explicit type"):
        await FakeEvaluationBackend().evaluate(
            eval_set=_eval_set(),
            eval_config=config,
            prompts={"system": "baseline"},
            split=Split.TRAIN,
            phase=Phase.BASELINE,
            audit_dir=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_offline_equals_requires_explicit_content(tmp_path) -> None:
    config = EvalConfig(
        metrics=[{
            "metric_name": "llm_rubric_response",
            "threshold": 1,
            "criterion": {
                "llm_judge": {
                    "judge_model": {
                        "model_name": "must-not-load"
                    },
                    "rubrics": [{
                        "id": "missing_operand",
                        "type": "OFFLINE_RESPONSE_EQUALS",
                    }],
                }
            },
        }],
        num_runs=1,
    )
    with pytest.raises(ValueError, match="requires content.text"):
        await FakeEvaluationBackend().evaluate(
            eval_set=_eval_set(),
            eval_config=config,
            prompts={"system": "baseline"},
            split=Split.TRAIN,
            phase=Phase.BASELINE,
            audit_dir=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_offline_exact_reference_preserves_empty_response(tmp_path) -> None:
    eval_set = _eval_set()
    invocation = eval_set.eval_cases[0].conversation[0]
    invocation.user_content.parts[0].text = "BEHAVIOR:stable; ANSWER:"
    invocation.final_response.parts[0].text = ""
    config = EvalConfig(
        metrics=[{
            "metric_name": "llm_rubric_response",
            "threshold": 1,
            "criterion": {
                "llm_judge": {
                    "judge_model": {
                        "model_name": "must-not-load"
                    },
                    "rubrics": [{
                        "id": "empty_reference",
                        "type": "OFFLINE_RESPONSE_EXACT_REFERENCE",
                    }],
                }
            },
        }],
        num_runs=1,
    )
    raw = await FakeEvaluationBackend().evaluate(
        eval_set=eval_set,
        eval_config=config,
        prompts={"system": "baseline"},
        split=Split.TRAIN,
        phase=Phase.BASELINE,
        audit_dir=str(tmp_path),
    )
    snapshot = normalize_result(
        raw,
        eval_set,
        config,
        split=Split.TRAIN,
        phase=Phase.BASELINE,
    )
    assert snapshot.metric_scores == {"llm_rubric_response": 1}


@pytest.mark.asyncio
async def test_trace_reference_free_rubric_uses_placeholder_expected(tmp_path) -> None:
    source = _eval_set().model_dump(mode="json", by_alias=True, exclude_none=True)
    case = source["evalCases"][0]
    actual = case.pop("conversation")
    case["evalMode"] = "trace"
    case["actualConversation"] = actual
    eval_set = EvalSet.model_validate(source)
    fixture = {
        "schemaVersion": "v1",
        "datasetHashes": {
            "train": "train-hash",
            "validation": "validation-hash"
        },
        "phases": {
            phase: {
                "train": {
                    "case": actual
                },
                "validation": {
                    "case": actual
                },
            }
            for phase in ("baseline", "candidate")
        },
    }
    fixture_path = tmp_path / "trace-reference-free.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    config = EvalConfig(
        metrics=[{
            "metric_name": "llm_rubric_response",
            "threshold": 1,
            "criterion": {
                "llm_judge": {
                    "judge_model": {
                        "model_name": "must-not-load"
                    },
                    "rubrics": [
                        {
                            "id": "reference_free_quality",
                            "content": {
                                "text": "OK"
                            },
                            "type": "OFFLINE_RESPONSE_CONTAINS",
                        },
                        {
                            "id": "missing_quality",
                            "content": {
                                "text": "MISSING"
                            },
                            "type": "OFFLINE_RESPONSE_CONTAINS",
                        },
                    ],
                }
            },
        }],
        num_runs=1,
    )
    raw = await TraceEvaluationBackend(
        str(fixture_path),
        {
            "train": "train-hash",
            "validation": "validation-hash"
        },
    ).evaluate(
        eval_set=eval_set,
        eval_config=config,
        prompts={"system": "unused"},
        split=Split.TRAIN,
        phase=Phase.BASELINE,
        audit_dir=str(tmp_path),
    )
    snapshot = normalize_result(
        raw,
        eval_set,
        config,
        split=Split.TRAIN,
        phase=Phase.BASELINE,
    )
    metric = snapshot.cases[0].runs[0].metrics[0]
    assert metric.score == 0.5
    assert [(rubric.id, rubric.passed) for rubric in metric.rubrics] == [
        ("reference_free_quality", True),
        ("missing_quality", False),
    ]
    assert snapshot.cases[0].runs[0].trace[0]["expected"] is None


@pytest.mark.asyncio
async def test_trace_knowledge_recall_scores_recorded_tool_evidence(tmp_path) -> None:
    source = _eval_set().model_dump(mode="json", by_alias=True, exclude_none=True)
    case = source["evalCases"][0]
    actual = case.pop("conversation")
    actual[0]["intermediateData"] = {
        "toolResponses": [{
            "name": "knowledge_search",
            "response": {
                "text": "pinned source fact"
            },
        }]
    }
    case["evalMode"] = "trace"
    case["actualConversation"] = actual
    eval_set = EvalSet.model_validate(source)
    fixture = {
        "schemaVersion": "v1",
        "datasetHashes": {
            "train": "train-hash",
            "validation": "validation-hash"
        },
        "phases": {
            phase: {
                "train": {
                    "case": actual
                },
                "validation": {
                    "case": actual
                }
            }
            for phase in ("baseline", "candidate")
        },
    }
    fixture_path = tmp_path / "trace-knowledge.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    config = EvalConfig(
        metrics=[{
            "metric_name": "llm_rubric_knowledge_recall",
            "threshold": 1,
            "criterion": {
                "llm_judge": {
                    "judge_model": {
                        "model_name": "must-not-load"
                    },
                    "rubrics": [{
                        "id": "source_fact",
                        "content": {
                            "text": "pinned source fact"
                        },
                        "type": "OFFLINE_KNOWLEDGE_CONTAINS",
                    }],
                }
            },
        }],
        num_runs=1,
    )
    raw = await TraceEvaluationBackend(
        str(fixture_path),
        {
            "train": "train-hash",
            "validation": "validation-hash"
        },
    ).evaluate(
        eval_set=eval_set,
        eval_config=config,
        prompts={"system": "unused"},
        split=Split.TRAIN,
        phase=Phase.BASELINE,
        audit_dir=str(tmp_path),
    )
    snapshot = normalize_result(
        raw,
        eval_set,
        config,
        split=Split.TRAIN,
        phase=Phase.BASELINE,
    )
    metric = snapshot.cases[0].runs[0].metrics[0]
    assert metric.score == 1
    assert [(rubric.id, rubric.passed) for rubric in metric.rubrics] == [("source_fact", True)]


@pytest.mark.asyncio
async def test_trace_backend_replays_fixture_without_agent(tmp_path) -> None:
    eval_set = _eval_set()
    actual = eval_set.eval_cases[0].conversation[0].model_dump(mode="json", by_alias=True)
    fixture = {
        "schemaVersion": "v1",
        "datasetHashes": {
            "train": "train-hash",
            "validation": "validation-hash"
        },
        "phases": {
            phase: {
                "train": {
                    "case": [actual]
                },
                "validation": {
                    "case": [actual]
                },
            }
            for phase in ("baseline", "candidate")
        },
    }
    fixture_path = tmp_path / "trace.json"
    fixture_path.write_text(__import__("json").dumps(fixture), encoding="utf-8")
    backend = TraceEvaluationBackend(
        str(fixture_path),
        {
            "train": "train-hash",
            "validation": "validation-hash"
        },
    )
    fixture["phases"]["baseline"]["train"]["case"][0]["finalResponse"] = {
        "role": "model",
        "parts": [{
            "text": "CHANGED_AFTER_PREFLIGHT"
        }],
    }
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    raw = await backend.evaluate(
        eval_set=eval_set,
        eval_config=_config(),
        prompts={"system": "unused"},
        split=Split.TRAIN,
        phase=Phase.BASELINE,
        audit_dir=str(tmp_path),
    )
    snapshot = normalize_result(
        raw,
        eval_set,
        _config(),
        split=Split.TRAIN,
        phase=Phase.BASELINE,
    )
    assert snapshot.pass_rate == 1


def test_live_backend_requires_async_callback() -> None:
    with pytest.raises(TypeError, match="async"):
        LiveEvaluationBackend(lambda query: query)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_live_backend_uses_agent_evaluator_entrypoint(tmp_path, monkeypatch) -> None:
    calls = 0
    original = AgentEvaluator.evaluate_eval_set

    async def recording_evaluate_eval_set(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await original(*args, **kwargs)

    async def call_agent(query: str) -> str:
        del query
        return "OK"

    monkeypatch.setattr(AgentEvaluator, "evaluate_eval_set", recording_evaluate_eval_set)
    raw = await LiveEvaluationBackend(call_agent).evaluate(
        eval_set=_eval_set(),
        eval_config=_config(),
        prompts={"system": "baseline"},
        split=Split.VALIDATION,
        phase=Phase.BASELINE,
        audit_dir=str(tmp_path),
    )
    snapshot = normalize_result(
        raw,
        _eval_set(),
        _config(),
        split=Split.VALIDATION,
        phase=Phase.BASELINE,
    )
    assert snapshot.pass_rate == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_deterministic_generator_only_uses_failure_facts(tmp_path) -> None:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    target = TargetPrompt().add_path("system", str(prompt_path))
    attribution = AttributionSnapshot(split=Split.TRAIN, phase=Phase.BASELINE, failures=())
    proposal = await DeterministicCandidateGenerator().generate(
        target_prompt=target,
        baseline_prompts={"system": "baseline"},
        train_attribution=attribution,
        inner_train_path="inner-train",
        inner_selection_path="inner-selection",
        config_path="config",
        output_dir="output",
    )
    assert proposal.changed is False


@pytest.mark.asyncio
async def test_live_generator_forces_update_source_false(monkeypatch, tmp_path) -> None:
    async def call_agent(query: str) -> str:
        return query

    captured = {}

    async def fake_optimize(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(dump_to=lambda path: Path(path).write_text("{}", encoding="utf-8"))

    monkeypatch.setattr(
        optimizer_worker_module,
        "load_verified_callback",
        lambda spec, **kwargs: call_agent,
    )
    monkeypatch.setattr(optimizer_worker_module.AgentOptimizer, "optimize", fake_optimize)
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps({
            "callbackSpec": _live_adapter().import_path,
            "callbackSourcePath": str(_live_adapter().source_path),
            "callbackSourceSha256": _live_adapter().source_sha256,
            "callbackCallableSha256": _live_adapter().callable_sha256,
            "promptPaths": {
                "system": str(prompt_path)
            },
            "configPath": "optimizer.json",
            "trainPath": "inner-train.json",
            "validationPath": "inner-selection.json",
            "outputDir": str(tmp_path / "optimizer-output"),
            "verbose": 0,
        }),
        encoding="utf-8",
    )
    await optimizer_worker_module._run(request_path)
    assert captured["update_source"] is False
    assert captured["validation_dataset_path"] == "inner-selection.json"


@pytest.mark.parametrize("error_type", (KeyboardInterrupt, SystemExit))
def test_optimizer_worker_rethrows_process_control(monkeypatch, tmp_path, error_type) -> None:
    output_dir = tmp_path / "optimizer-output"
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps({
            "outputDir": str(output_dir)
        }),
        encoding="utf-8",
    )
    control = error_type("stop worker")

    async def fail(_request_path):
        raise control

    monkeypatch.setattr(optimizer_worker_module, "_run", fail)
    monkeypatch.setattr(sys, "argv", ["optimizer_worker", str(request_path)])

    with pytest.raises(error_type) as raised:
        optimizer_worker_module.main()

    assert raised.value is control
    assert not (output_dir / "worker_error.json").exists()


def test_optimizer_worker_error_artifact_is_bounded_and_redacted(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "optimizer-output"
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps({
            "outputDir": str(output_dir)
        }),
        encoding="utf-8",
    )

    async def fail(_request_path):
        raise RuntimeError(
            "Authorization: Bearer worker-token; api_key=worker-key; " + "context" * 1000)

    monkeypatch.setattr(optimizer_worker_module, "_run", fail)
    monkeypatch.setattr(sys, "argv", ["optimizer_worker", str(request_path)])

    assert optimizer_worker_module.main() == 1
    payload = json.loads((output_dir / "worker_error.json").read_text(encoding="utf-8"))
    assert payload["errorType"] == "RuntimeError"
    assert len(payload["message"]) <= 4000
    assert "worker-token" not in payload["message"]
    assert "worker-key" not in payload["message"]
    assert payload["message"].count("[REDACTED]") == 2


@pytest.mark.asyncio
async def test_live_generator_accepts_sdk_skipped_round(monkeypatch, tmp_path) -> None:
    async def fake_optimize(**kwargs):
        return SimpleNamespace(
            status="SUCCEEDED",
            error_message="",
            rounds=[
                SimpleNamespace(
                    round=1,
                    candidate_prompts={},
                    accepted=False,
                    validation_pass_rate=0.0,
                    kind="reflective",
                    optimized_field_names=[],
                    metric_breakdown={},
                    acceptance_reason="no candidate produced this round",
                    skip_reason="reflect-LM produced no usable new prompt",
                    error_message=None,
                    round_llm_cost=0.02,
                    round_token_usage={"total": 4},
                    duration_seconds=0.3,
                )
            ],
            algorithm="gepa_reflective",
            baseline_prompts={"system": "baseline"},
            best_prompts={"system": "baseline"},
            stop_reason="completed",
            total_reflection_lm_calls=1,
            total_llm_cost=0.02,
            total_token_usage={"total": 4},
            duration_seconds=0.4,
        )

    _install_worker_result(monkeypatch, await fake_optimize())
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    proposal = await LiveCandidateGenerator(_live_adapter()).generate(
        target_prompt=TargetPrompt().add_path("system", str(prompt_path)),
        baseline_prompts={"system": "baseline"},
        train_attribution=AttributionSnapshot(split=Split.TRAIN, phase=Phase.BASELINE, failures=()),
        inner_train_path="inner-train.json",
        inner_selection_path="inner-selection.json",
        config_path="optimizer.json",
        output_dir=str(tmp_path / "optimizer-output"),
    )
    assert proposal.changed is False
    assert proposal.rounds[0].candidate_prompts == {}
    assert proposal.rounds[0].skip_reason == "reflect-LM produced no usable new prompt"
    assert proposal.rounds[0].score is None
    assert proposal.rounds[0].cost_usd == 0.02


@pytest.mark.asyncio
async def test_live_generator_preserves_failed_sdk_result_facts(monkeypatch, tmp_path) -> None:
    result = SimpleNamespace(
        status="FAILED",
        error_message="optimizer request failed",
        rounds=[SimpleNamespace(
            round=1,
            candidate_prompts={},
            accepted=False,
            validation_pass_rate=0.0,
            kind="reflective",
            optimized_field_names=[],
            metric_breakdown={},
            acceptance_reason="",
            skip_reason=None,
            error_message="reflection request failed",
            round_llm_cost=0.04,
            round_token_usage={"total": 6},
            duration_seconds=0.2,
        )],
        algorithm="gepa_reflective",
        baseline_prompts={"system": "baseline"},
        best_prompts={"system": "baseline"},
        stop_reason=None,
        total_reflection_lm_calls=1,
        total_llm_cost=0.04,
        total_token_usage={"total": 6},
        duration_seconds=0.3,
    )
    _install_worker_result(monkeypatch, result)
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")

    proposal = await LiveCandidateGenerator(_live_adapter()).generate(
        target_prompt=TargetPrompt().add_path("system", str(prompt_path)),
        baseline_prompts={"system": "baseline"},
        train_attribution=AttributionSnapshot(split=Split.TRAIN, phase=Phase.BASELINE, failures=()),
        inner_train_path="inner-train.json",
        inner_selection_path="inner-selection.json",
        config_path="optimizer.json",
        output_dir=str(tmp_path / "optimizer-output"),
    )

    assert proposal.status == "FAILED"
    assert proposal.error_message == "optimizer request failed"
    assert proposal.rounds[0].error_message == "reflection request failed"
    assert proposal.cost_sources[0].cost_usd == 0.04
    assert proposal.duration_seconds == 0.3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reported_baseline", "best_prompts", "expected_error"),
    (
        ({"system": "stale"}, {"system": "candidate"}, "baseline prompts differ"),
        ({"system": "baseline"}, {}, "best prompt keys differ"),
    ),
)
async def test_live_generator_rejects_invalid_success_contract(
    monkeypatch,
    tmp_path,
    reported_baseline,
    best_prompts,
    expected_error,
) -> None:
    result = SimpleNamespace(
        status="SUCCEEDED",
        error_message="",
        rounds=[SimpleNamespace(
            round=1,
            candidate_prompts={"system": "candidate"},
            accepted=True,
            validation_pass_rate=0.9,
            kind="reflective",
            optimized_field_names=["system"],
            metric_breakdown={"quality": 0.9},
            acceptance_reason="improved",
            skip_reason=None,
            error_message=None,
            round_llm_cost=0.02,
            round_token_usage={"total": 4},
            duration_seconds=0.1,
        )],
        algorithm="gepa_reflective",
        baseline_prompts=reported_baseline,
        best_prompts=best_prompts,
        stop_reason="completed",
        total_reflection_lm_calls=1,
        total_llm_cost=0.02,
        total_token_usage={"total": 4},
        duration_seconds=0.2,
    )
    _install_worker_result(monkeypatch, result)
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")

    proposal = await LiveCandidateGenerator(_live_adapter()).generate(
        target_prompt=TargetPrompt().add_path("system", str(prompt_path)),
        baseline_prompts={"system": "baseline"},
        train_attribution=AttributionSnapshot(split=Split.TRAIN, phase=Phase.BASELINE, failures=()),
        inner_train_path="inner-train.json",
        inner_selection_path="inner-selection.json",
        config_path="optimizer.json",
        output_dir=str(tmp_path / "optimizer-output"),
    )

    assert proposal.status == "SUCCEEDED"
    assert expected_error in proposal.adapter_error
    assert proposal.prompts == {"system": "baseline"}
    assert proposal.changed is False
    assert proposal.rounds[0].cost_usd == 0.02


@pytest.mark.asyncio
async def test_live_worker_is_reaped_after_repeated_parent_cancellation(monkeypatch, tmp_path) -> None:
    class StuckProcess:

        def __init__(self) -> None:
            self.returncode = None
            self.waiting = asyncio.Event()
            self.cleanup_waiting = asyncio.Event()
            self.finished = asyncio.Event()
            self.wait_calls = 0
            self.terminated = False
            self.killed = False

        async def wait(self):
            self.wait_calls += 1
            self.waiting.set()
            if self.wait_calls >= 2:
                self.cleanup_waiting.set()
            await self.finished.wait()
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15
            self.finished.set()

        def kill(self):
            self.killed = True
            self.returncode = -9
            self.finished.set()

    process = StuckProcess()

    async def fake_subprocess(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    output_dir = tmp_path / "optimizer-output"
    task = asyncio.create_task(
        LiveCandidateGenerator(
            _live_adapter(),
            shutdown_timeout_seconds=0.1,
        ).generate(
            target_prompt=TargetPrompt().add_path("system", str(prompt_path)),
            baseline_prompts={"system": "baseline"},
            train_attribution=AttributionSnapshot(split=Split.TRAIN, phase=Phase.BASELINE, failures=()),
            inner_train_path="inner-train.json",
            inner_selection_path="inner-selection.json",
            config_path="optimizer.json",
            output_dir=str(output_dir),
        ))
    await process.waiting.wait()
    task.cancel()
    await process.cleanup_waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as raised:
        await task
    assert (output_dir / "optimize.stop").read_text(encoding="utf-8") == "cancel requested\n"
    assert process.terminated is True
    assert process.returncode in {-15, -9}
    assert any("additional cancellation" in note for note in raised.value.__notes__)


@pytest.mark.asyncio
async def test_live_worker_cleanup_escalates_after_wait_and_terminate_errors(monkeypatch, tmp_path) -> None:
    class KillOnlyProcess:

        def __init__(self) -> None:
            self.returncode = None
            self.waiting = asyncio.Event()
            self.finished = asyncio.Event()
            self.wait_calls = 0
            self.killed = False

        async def wait(self):
            self.wait_calls += 1
            self.waiting.set()
            if self.wait_calls == 2:
                raise OSError("Authorization: Bearer wait-secret")
            await self.finished.wait()
            return self.returncode

        def terminate(self):
            raise PermissionError("api_key=terminate-secret")

        def kill(self):
            self.killed = True
            self.returncode = -9
            self.finished.set()

    process = KillOnlyProcess()

    async def fake_subprocess(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    task = asyncio.create_task(
        LiveCandidateGenerator(
            _live_adapter(),
            shutdown_timeout_seconds=0.01,
        ).generate(
            target_prompt=TargetPrompt().add_path("system", str(prompt_path)),
            baseline_prompts={"system": "baseline"},
            train_attribution=AttributionSnapshot(split=Split.TRAIN, phase=Phase.BASELINE, failures=()),
            inner_train_path="inner-train.json",
            inner_selection_path="inner-selection.json",
            config_path="optimizer.json",
            output_dir=str(tmp_path / "optimizer-output"),
        ))
    await process.waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as raised:
        await task

    diagnostics = "\n".join(raised.value.__notes__)
    assert process.killed is True
    assert process.returncode == -9
    assert "wait-secret" not in diagnostics
    assert "terminate-secret" not in diagnostics
    assert diagnostics.count("[REDACTED]") == 2


@pytest.mark.asyncio
async def test_live_worker_success_is_loaded_through_the_strict_candidate_adapter(monkeypatch, tmp_path) -> None:
    round_ = SimpleNamespace(
        round=1,
        candidate_prompts={"system": "candidate"},
        accepted=True,
        validation_pass_rate=0.8,
        kind="reflective",
        optimized_field_names=["system"],
        metric_breakdown={"quality": 0.8},
        acceptance_reason="improved selection score",
        skip_reason=None,
        error_message=None,
        round_llm_cost=0.03,
        round_token_usage={"total": 8},
        duration_seconds=0.2,
    )
    result = SimpleNamespace(
        status="SUCCEEDED",
        error_message="",
        rounds=[round_],
        algorithm="gepa_reflective",
        baseline_prompts={"system": "baseline"},
        best_prompts={"system": "candidate"},
        stop_reason="completed",
        total_reflection_lm_calls=1,
        total_llm_cost=0.03,
        total_token_usage={"total": 8},
        duration_seconds=0.2,
    )
    captured = {}
    _install_worker_result(monkeypatch, result, captured)
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    proposal = await LiveCandidateGenerator(_live_adapter()).generate(
        target_prompt=TargetPrompt().add_path("system", str(prompt_path)),
        baseline_prompts={"system": "baseline"},
        train_attribution=AttributionSnapshot(split=Split.TRAIN, phase=Phase.BASELINE, failures=()),
        inner_train_path="inner-train.json",
        inner_selection_path="inner-selection.json",
        config_path="optimizer.json",
        output_dir=str(tmp_path / "optimizer-output"),
    )
    assert captured["args"][1:3] == (
        "-m",
        "examples.optimization.eval_optimize_loop.pipeline.optimizer_worker",
    )
    assert captured["request"]["callbackSpec"] == _live_adapter().import_path
    assert captured["request"]["callbackSourcePath"] == str(_live_adapter().source_path)
    assert captured["request"]["callbackSourceSha256"] == _live_adapter().source_sha256
    assert captured["request"]["callbackCallableSha256"] == _live_adapter().callable_sha256
    assert captured["request"]["promptPaths"] == {"system": str(prompt_path)}
    assert proposal.prompts == {"system": "candidate"}
    assert proposal.rounds[0].metric_scores == {"quality": 0.8}
    assert proposal.cost_sources[0].cost_usd == 0.03
