from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from examples.optimization.eval_optimize_loop.eval_loop.fake_model import FakeModel
from examples.optimization.eval_optimize_loop.eval_loop.schemas import EvalCase
from examples.optimization.eval_optimize_loop.eval_loop.schemas import to_jsonable
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_PROMPT
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_TRAIN
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_VAL
from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline_async
from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import AgentOptimizer
from trpc_agent_sdk.evaluation import GepaReflectiveOptimizer
from trpc_agent_sdk.evaluation import TargetPrompt


class FakeGEPAResult:

    def __init__(
        self,
        baseline: dict[str, str],
        candidate: dict[str, str],
    ) -> None:
        self.candidates = [baseline, candidate]
        self.val_aggregate_scores = [2 / 3, 1.0]
        self.parents = [[None], [0]]
        self.discovery_eval_counts = [0, 1]
        self.total_metric_calls = 6
        self.best_outputs_valset = None
        self.per_objective_best_candidates = {}

    @property
    def best_idx(self) -> int:
        return 1


@pytest.mark.asyncio
async def test_sdk_pipeline_uses_real_facade_evaluator_and_post_gate_writeback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    train_path = inputs_dir / "train.evalset.json"
    val_path = inputs_dir / "val.evalset.json"
    prompt_path = inputs_dir / "system_prompt.txt"
    optimizer_config_path = inputs_dir / "optimizer.json"
    gate_path = inputs_dir / "gate.json"
    shutil.copyfile(DEFAULT_TRAIN, train_path)
    shutil.copyfile(DEFAULT_VAL, val_path)
    expected_train_case_ids = _eval_ids(train_path)
    expected_val_case_ids = _eval_ids(val_path)
    shutil.copyfile(DEFAULT_PROMPT, prompt_path)
    optimizer_config_path.write_text(
        json.dumps(
            {
                "evaluate": {
                    "metrics": [{
                        "metric_name": "final_response_avg_score",
                        "threshold": 0.5,
                    }],
                    "num_runs": 1,
                },
                "optimize": {
                    "eval_case_parallelism": 1,
                    "stop": {
                        "required_metrics": None
                    },
                    "algorithm": {
                        "name": "gepa_reflective",
                        "seed": 91,
                        "reflection_lm": {
                            "provider_name": "openai",
                            "model_name": "gpt-4o",
                            "api_key": "test-key",
                        },
                        "max_metric_calls": 6,
                    },
                },
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    gate_path.write_text(
        json.dumps(
            {
                "gate": {
                    "min_val_score_improvement": 0.01,
                    "allow_new_hard_fail": False,
                    "protected_case_ids": [],
                    "max_score_drop_per_case": 0.0,
                    "max_total_cost": None,
                }
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    baseline = {"system_prompt": prompt_path.read_text(encoding="utf-8")}
    candidate = {
        "system_prompt":
        (baseline["system_prompt"].rstrip("\n") + "\n\nUse strict JSON only when the user explicitly asks.\n")
    }
    gepa_calls: list[dict[str, object]] = []

    async def fake_call_gepa(
        self: GepaReflectiveOptimizer,
        **kwargs: object,
    ) -> FakeGEPAResult:
        assert isinstance(self, GepaReflectiveOptimizer)
        gepa_calls.append(dict(kwargs))
        return FakeGEPAResult(baseline, candidate)

    async def call_agent(query: str) -> str:
        prompt = prompt_path.read_text(encoding="utf-8")
        return deterministic_sdk_response(prompt, query)

    monkeypatch.setattr(
        GepaReflectiveOptimizer,
        "_call_gepa_optimize",
        fake_call_gepa,
    )
    module = ModuleType("issue91_sdk_call_agent")
    module.call_agent = call_agent
    monkeypatch.setitem(sys.modules, module.__name__, module)

    report = await run_pipeline_async(
        mode="sdk",
        train_path=train_path,
        val_path=val_path,
        optimizer_config_path=optimizer_config_path,
        prompt_path=prompt_path,
        output_dir=tmp_path / "out",
        sdk_call_agent="issue91_sdk_call_agent:call_agent",
        gate_config_path=gate_path,
        update_source=True,
        run_id="sdk-integration",
    )

    assert AgentOptimizer.__module__.startswith("trpc_agent_sdk.evaluation.")
    assert AgentEvaluator.__module__.startswith("trpc_agent_sdk.evaluation.")
    assert TargetPrompt.__module__.startswith("trpc_agent_sdk.evaluation.")
    assert GepaReflectiveOptimizer.__module__.startswith("trpc_agent_sdk.evaluation.")
    assert len(gepa_calls) == 1
    gepa_call = gepa_calls[0]
    assert gepa_call["seed_candidate"] == baseline
    captured_trainset = gepa_call["trainset"]
    captured_valset = gepa_call["valset"]
    assert isinstance(captured_trainset, list)
    assert isinstance(captured_valset, list)
    assert {case.eval_id for case in captured_trainset} == expected_train_case_ids
    assert {case.eval_id for case in captured_valset} == expected_val_case_ids
    assert gepa_call["callbacks"]
    assert report.selected_candidate == "sdk_round_001"
    assert report.candidates
    for record in report.candidates:
        assert {case.case_id for case in record["train_result"].cases} == expected_train_case_ids
        assert {case.case_id for case in record["validation_result"].cases} == expected_val_case_ids
    assert report.gate_decisions
    assert all(item.gate_status == "applied" for item in report.gate_decisions)
    decision = next(item for item in report.gate_decisions if item.candidate_id == "sdk_round_001")
    assert decision.gate_status == "applied"
    assert decision.accepted is True
    assert report.writeback.status == "applied"
    assert prompt_path.read_text(encoding="utf-8") == candidate["system_prompt"]

    sdk_summary = report.audit["sdk_result_summary"]
    assert sdk_summary["status"] == "SUCCEEDED"
    assert sdk_summary["extras"]["total_metric_calls"] == 6
    assert sdk_summary["total_llm_cost"] == 0.0
    assert report.cost_summary.reported_optimizer_cost == 0.0
    assert "partial_applied" not in json.dumps(to_jsonable(report))
    run_dir = tmp_path / "out" / "runs" / "sdk-integration"
    assert (run_dir / "optimizer" / "result.json").is_file()
    optimizer_config_snapshot = run_dir / "optimizer" / "config.snapshot.json"
    artifact_files = sorted(path for path in run_dir.rglob("*") if path.is_file())
    assert optimizer_config_snapshot in artifact_files
    leaked_test_key = [
        path.relative_to(run_dir).as_posix() for path in artifact_files if b"test-key" in path.read_bytes()
    ]
    assert leaked_test_key == []


def deterministic_sdk_response(prompt: str, query: str) -> str:
    case = EvalCase(
        case_id="sdk-runtime-query",
        split="runtime",
        input=query,
        expectation={"type": "runtime-only; must not be inspected"},
    )
    output, _, _ = FakeModel(seed=91).generate(
        "sdk-runtime",
        prompt,
        case.input,
    )
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return output
    return json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _eval_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(case["eval_id"]) for case in payload["eval_cases"]}
