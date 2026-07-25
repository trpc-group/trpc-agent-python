# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the auditable evaluation-optimization loop example."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIR = REPO_ROOT / "examples" / "optimization" / "eval_optimize_loop"
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))

import pipeline as loop_pipeline  # noqa: E402
from pipeline import FAILURE_FINAL_RESPONSE  # noqa: E402
from pipeline import FAILURE_FORMAT  # noqa: E402
from pipeline import FAILURE_KNOWLEDGE  # noqa: E402
from pipeline import FAILURE_LLM_RUBRIC  # noqa: E402
from pipeline import FAILURE_PARAMETER  # noqa: E402
from pipeline import FAILURE_TOOL_CALL  # noqa: E402
from pipeline import classify_failure  # noqa: E402
from pipeline import evaluate_gate  # noqa: E402
from pipeline import run_pipeline  # noqa: E402


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_prompt(tmp_path: Path) -> Path:
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text(
        (EXAMPLE_DIR / "prompts" / "system.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return prompt_path


def _evaluation(
    *,
    score: float,
    pass_rate: float,
    case_id: str = "critical",
    passed: bool = True,
) -> dict:
    return {
        "score":
        score,
        "pass_rate":
        pass_rate,
        "metric_scores": {
            "final_response_avg_score": score
        },
        "cases": [{
            "case_id": case_id,
            "passed": passed,
            "score": score,
            "metric_scores": {
                "final_response_avg_score": score
            },
        }],
    }


def test_example_has_six_disjoint_cases() -> None:
    train = _load_json(EXAMPLE_DIR / "train.evalset.json")
    validation = _load_json(EXAMPLE_DIR / "val.evalset.json")
    train_ids = {case["eval_id"] for case in train["eval_cases"]}
    validation_ids = {case["eval_id"] for case in validation["eval_cases"]}

    assert len(train_ids) == 3
    assert len(validation_ids) == 3
    assert train_ids.isdisjoint(validation_ids)


def test_default_run_ids_are_utc_and_unique() -> None:
    run_ids = {loop_pipeline._default_run_id() for _ in range(2)}

    assert len(run_ids) == 2
    assert all(re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{8}", run_id) for run_id in run_ids)


@pytest.mark.asyncio
async def test_unrelated_evaluator_assertion_is_not_suppressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class BrokenExecuter:

        async def evaluate(self) -> None:
            raise AssertionError("internal evaluator invariant")

    monkeypatch.setattr(
        loop_pipeline.AgentEvaluator,
        "get_executer",
        staticmethod(lambda **_: BrokenExecuter()),
    )

    async def responder(query: str) -> str:
        return query

    with pytest.raises(AssertionError, match="internal evaluator invariant"):
        await loop_pipeline.run_evaluation(
            responder=responder,
            dataset_name="train",
            dataset_path=EXAMPLE_DIR / "train.evalset.json",
            eval_config_path=EXAMPLE_DIR / "optimizer.json",
            output_dir=tmp_path / "evaluation",
            execution_mode="call_agent",
        )


@pytest.mark.asyncio
async def test_full_fake_trace_pipeline_generates_auditable_reports(tmp_path: Path, ) -> None:
    prompt_path = _copy_prompt(tmp_path)
    original_prompt = prompt_path.read_text(encoding="utf-8")
    output_dir = tmp_path / "run"
    started = time.monotonic()

    report = await run_pipeline(
        config_path=EXAMPLE_DIR / "optimizer.json",
        train_path=EXAMPLE_DIR / "train.evalset.json",
        validation_path=EXAMPLE_DIR / "val.evalset.json",
        prompt_path=prompt_path,
        output_dir=output_dir,
        run_id="pytest-trace",
    )

    assert time.monotonic() - started < 180
    assert report["baseline"]["train"]["score"] == 0.0
    assert report["baseline"]["validation"]["score"] == pytest.approx(2 / 3, abs=1e-6)
    assert report["candidate"]["id"] == "balanced_candidate"
    assert report["candidate"]["evaluation"]["validation"]["score"] == 1.0
    assert report["gate_decision"]["accepted"] is True
    assert report["cost"]["model_calls"] == 18
    assert prompt_path.read_text(encoding="utf-8") == original_prompt
    assert (output_dir / "optimization_report.json").is_file()
    assert (output_dir / "optimization_report.md").is_file()
    assert not list(output_dir.rglob("*.tmp"))

    trace_path = (output_dir / "evaluations" / "baseline" / "train" / "train.trace.evalset.json")
    trace = _load_json(trace_path)
    assert all(case["evalMode"] == "trace" for case in trace["eval_cases"])
    assert all(case["actualConversation"] for case in trace["eval_cases"])

    baseline_failures = report["failure_attribution"]["baseline_train"]
    assert baseline_failures["total_failures"] == 3
    assert baseline_failures["counts"] == {
        FAILURE_FORMAT: 1,
        FAILURE_KNOWLEDGE: 1,
        FAILURE_TOOL_CALL: 1,
    }


@pytest.mark.asyncio
async def test_overfit_candidate_is_rejected_despite_train_improvement(tmp_path: Path, ) -> None:
    report = await run_pipeline(
        config_path=EXAMPLE_DIR / "optimizer.json",
        train_path=EXAMPLE_DIR / "train.evalset.json",
        validation_path=EXAMPLE_DIR / "val.evalset.json",
        prompt_path=_copy_prompt(tmp_path),
        output_dir=tmp_path / "run",
        run_id="pytest-overfit",
    )
    overfit = next(item for item in report["rounds"] if item["id"] == "overfit_candidate")
    failed_checks = {check["name"] for check in overfit["gate_decision"]["checks"] if not check["passed"]}

    assert overfit["delta"]["train"]["score"] > 0
    assert overfit["delta"]["validation"]["score"] < 0
    assert overfit["gate_decision"]["accepted"] is False
    assert "no_new_hard_fail" in failed_checks
    assert "critical_cases_do_not_regress" in failed_checks
    assert "train_validation_gain_gap" in failed_checks
    assert overfit["delta"]["validation"]["new_failures"] == [
        "val_critical_safety",
        "val_stable_math",
    ]


@pytest.mark.parametrize(
    ("metric_names", "reasons", "actual", "expected", "failure_type"),
    [
        (
            ["tool_trajectory_avg_score"],
            ["invalid parameter city_name"],
            "",
            "",
            FAILURE_PARAMETER,
        ),
        (
            ["tool_trajectory_avg_score"],
            [],
            "TOOL_ERROR: search was not called",
            "",
            FAILURE_TOOL_CALL,
        ),
        (
            ["llm_rubric_knowledge_recall"],
            [],
            "KNOWLEDGE_MISS: no grounded source",
            "",
            FAILURE_KNOWLEDGE,
        ),
        (
            ["llm_rubric_response"],
            ["rubric helpfulness score below threshold"],
            "partial",
            "complete",
            FAILURE_LLM_RUBRIC,
        ),
        (
            ["final_response_avg_score"],
            [],
            "amount is 128 CNY",
            '{"amount":128,"currency":"CNY"}',
            FAILURE_FORMAT,
        ),
        (
            ["final_response_avg_score"],
            [],
            "route=general",
            "route=calendar",
            FAILURE_FINAL_RESPONSE,
        ),
    ],
)
def test_failure_attribution_categories(
    metric_names: list[str],
    reasons: list[str],
    actual: str,
    expected: str,
    failure_type: str,
) -> None:
    assert classify_failure(
        metric_names=metric_names,
        reasons=reasons,
        actual=actual,
        expected=expected,
    ) == failure_type


def test_gate_rejects_candidate_over_budget() -> None:
    baseline_train = _evaluation(score=0.4, pass_rate=0.0, passed=False)
    baseline_validation = _evaluation(score=0.5, pass_rate=0.0, passed=False)
    candidate_train = _evaluation(score=0.7, pass_rate=1.0)
    candidate_validation = _evaluation(score=0.7, pass_rate=1.0)

    decision = evaluate_gate(
        gate_config={
            "min_validation_score_delta": 0.1,
            "min_validation_pass_rate_delta": 0.0,
            "max_new_validation_failures": 0,
            "max_train_validation_gain_gap": 0.5,
            "max_candidate_cost_usd": 1.0,
        },
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
        candidate_cost_usd=1.01,
    )

    assert decision["accepted"] is False
    cost_check = next(check for check in decision["checks"] if check["name"] == "candidate_cost")
    assert cost_check["passed"] is False


def test_gate_rejects_candidate_without_finite_cost_budget() -> None:
    baseline_train = _evaluation(score=0.4, pass_rate=0.0, passed=False)
    baseline_validation = _evaluation(score=0.5, pass_rate=0.0, passed=False)
    candidate_train = _evaluation(score=0.7, pass_rate=1.0)
    candidate_validation = _evaluation(score=0.7, pass_rate=1.0)

    decision = evaluate_gate(
        gate_config={},
        baseline_train=baseline_train,
        baseline_validation=baseline_validation,
        candidate_train=candidate_train,
        candidate_validation=candidate_validation,
        candidate_cost_usd=0.0,
    )

    assert decision["accepted"] is False
    cost_check = next(check for check in decision["checks"] if check["name"] == "candidate_cost")
    assert cost_check["passed"] is False
    assert cost_check["threshold"] == "configured finite non-negative budget"


@pytest.mark.asyncio
async def test_source_updates_only_after_accepted_gate(tmp_path: Path) -> None:
    prompt_path = _copy_prompt(tmp_path)
    report = await run_pipeline(
        config_path=EXAMPLE_DIR / "optimizer.json",
        train_path=EXAMPLE_DIR / "train.evalset.json",
        validation_path=EXAMPLE_DIR / "val.evalset.json",
        prompt_path=prompt_path,
        output_dir=tmp_path / "run",
        run_id="pytest-write-back",
        update_source=True,
    )

    updated = prompt_path.read_text(encoding="utf-8")
    assert report["audit"]["source_prompt_updated"] is True
    assert "[RULE:PRESERVE_SAFETY]" in updated
    assert "[RULE:ANSWER_ALL]" not in updated


@pytest.mark.asyncio
async def test_call_agent_mode_uses_the_same_gate(tmp_path: Path) -> None:
    config = _load_json(EXAMPLE_DIR / "optimizer.json")
    config["pipeline"]["execution_mode"] = "call_agent"
    config["pipeline"]["fake_optimizer"]["rounds"] = config["pipeline"]["fake_optimizer"]["rounds"][:1]
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    output_dir = tmp_path / "run"
    report = await run_pipeline(
        config_path=config_path,
        train_path=EXAMPLE_DIR / "train.evalset.json",
        validation_path=EXAMPLE_DIR / "val.evalset.json",
        prompt_path=_copy_prompt(tmp_path),
        output_dir=output_dir,
        run_id="pytest-call-agent",
    )

    assert report["audit"]["execution_mode"] == "call_agent"
    assert report["gate_decision"]["accepted"] is True
    assert not list(output_dir.rglob("*.trace.evalset.json"))


@pytest.mark.parametrize(
    ("invalid_config", "message"),
    [
        ("execution_mode", "Unsupported execution mode"),
        ("missing_optimize", "requires a non-empty optimize object"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_pipeline_config_fails_before_audit_write(
    tmp_path: Path,
    invalid_config: str,
    message: str,
) -> None:
    config = _load_json(EXAMPLE_DIR / "optimizer.json")
    if invalid_config == "execution_mode":
        config["pipeline"]["execution_mode"] = "unsupported"
    else:
        config["pipeline"]["optimizer_backend"] = "agent_optimizer"
        config.pop("optimize")
    config_path = tmp_path / f"{invalid_config}.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_dir = tmp_path / f"{invalid_config}-run"

    with pytest.raises(ValueError, match=message):
        await run_pipeline(
            config_path=config_path,
            train_path=EXAMPLE_DIR / "train.evalset.json",
            validation_path=EXAMPLE_DIR / "val.evalset.json",
            prompt_path=_copy_prompt(tmp_path),
            output_dir=output_dir,
            run_id=f"pytest-{invalid_config}",
        )

    assert not (output_dir / "audit").exists()


@pytest.mark.asyncio
async def test_optimizer_failure_restores_baseline_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _load_json(EXAMPLE_DIR / "optimizer.json")
    config["pipeline"]["optimizer_backend"] = "agent_optimizer"
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    prompt_path = _copy_prompt(tmp_path)
    baseline_prompt = prompt_path.read_text(encoding="utf-8")

    async def fail_after_prompt_write(**kwargs):
        kwargs["prompt_path"].write_text("partial candidate", encoding="utf-8")
        raise RuntimeError("reflection model unavailable")

    monkeypatch.setattr(
        loop_pipeline,
        "_agent_optimizer_candidate",
        fail_after_prompt_write,
    )

    with pytest.raises(RuntimeError, match="reflection model unavailable"):
        await run_pipeline(
            config_path=config_path,
            train_path=EXAMPLE_DIR / "train.evalset.json",
            validation_path=EXAMPLE_DIR / "val.evalset.json",
            prompt_path=prompt_path,
            output_dir=tmp_path / "run",
            run_id="pytest-optimizer-failure",
        )

    assert prompt_path.read_text(encoding="utf-8") == baseline_prompt
