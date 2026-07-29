#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""End-to-end tests for the native AgentOptimizer integration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.asyncio

_CANDIDATE = """# Native Optimizer Candidate

Return the labeled reference answer for every registered evaluation query.

TEST_NATIVE_GOOD=true
"""


def _answers(loop_root: Path) -> dict[str, str]:
    answers: dict[str, str] = {}
    for name in ("train.evalset.json", "val.evalset.json"):
        payload = json.loads((loop_root / "data" / name).read_text(encoding="utf-8"))
        for case in payload["eval_cases"]:
            invocation = case["conversation"][0]
            query = invocation["user_content"]["parts"][0]["text"]
            answer = invocation["final_response"]["parts"][0]["text"]
            answers[query] = answer
    return answers


def _install_optimizer_stub(
    monkeypatch,
    *,
    total_tokens: int,
    total_cost: float,
    reflection_calls: int,
) -> list[dict]:
    from trpc_agent_sdk.evaluation import AgentOptimizer

    calls: list[dict] = []

    async def optimize_stub(**kwargs):
        calls.append(kwargs)
        assert kwargs["update_source"] is False
        baseline = await kwargs["target_prompt"].read_all()
        assert "TEST_NATIVE_GOOD" not in baseline["system"]

        output_dir = Path(kwargs["output_dir"])
        (output_dir / "rounds").mkdir(parents=True, exist_ok=True)
        (output_dir / "best_prompts").mkdir(parents=True, exist_ok=True)
        (output_dir / "baseline_prompts").mkdir(parents=True, exist_ok=True)
        (output_dir / "best_prompts" / "system.md").write_text(_CANDIDATE, encoding="utf-8")
        (output_dir / "baseline_prompts" / "system.md").write_text(baseline["system"], encoding="utf-8")
        round_record = {
            "round_index": 1,
            "input_prompt_sha256": "baseline",
            "candidate_prompt": _CANDIDATE,
            "accepted": True,
            "validation_score": 1.0,
        }
        (output_dir / "rounds" / "round_001.json").write_text(
            json.dumps(round_record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "result.json").write_text(
            json.dumps({"status": "SUCCEEDED", "best_prompts": {"system": _CANDIDATE}}),
            encoding="utf-8",
        )
        (output_dir / "config.snapshot.json").write_text("{}\n", encoding="utf-8")
        (output_dir / "run.log").write_text("stub optimizer succeeded\n", encoding="utf-8")
        (output_dir / "summary.txt").write_text("stub optimizer succeeded\n", encoding="utf-8")

        return SimpleNamespace(
            algorithm="test_native_stub",
            status="SUCCEEDED",
            finish_reason="COMPLETED",
            stop_reason="completed",
            error_message="",
            best_prompts={"system": _CANDIDATE},
            total_rounds=1,
            rounds=[round_record],
            baseline_pass_rate=0.0,
            best_pass_rate=1.0,
            pass_rate_improvement=1.0,
            total_token_usage={
                "prompt": total_tokens,
                "completion": 0,
                "total": total_tokens,
            },
            total_llm_cost=total_cost,
            total_reflection_lm_calls=reflection_calls,
        )

    monkeypatch.setattr(AgentOptimizer, "optimize", staticmethod(optimize_stub))
    return calls


def _call_agent(loop_root: Path):
    answers = _answers(loop_root)
    prompt_path = loop_root / "prompts" / "system.md"

    async def call_agent(query: str) -> str:
        if query not in answers:
            raise KeyError(query)
        if "TEST_NATIVE_GOOD" in prompt_path.read_text(encoding="utf-8"):
            return answers[query]
        return "答案：0"

    # This deterministic callback makes no provider call; zero is measured.
    call_agent.cost_status = "measured"
    call_agent.total_tokens = 0
    call_agent.total_cost = 0.0
    return call_agent


async def test_native_optimize_candidate_reaches_regression_and_gate(
    monkeypatch,
    loop_root: Path,
) -> None:
    calls = _install_optimizer_stub(
        monkeypatch,
        total_tokens=123,
        total_cost=0.0123,
        reflection_calls=1,
    )

    import pipeline

    rc = await pipeline.amain(
        ["--mode", "optimize"],
        call_agent=_call_agent(loop_root),
    )
    assert rc == 0
    assert len(calls) == 1

    optimize_call = calls[0]
    assert optimize_call["update_source"] is False
    assert Path(optimize_call["train_dataset_path"]).name == "train.evalset.json"
    assert Path(optimize_call["validation_dataset_path"]).name == "val.evalset.json"

    payload = json.loads((loop_root / "optimization_report.json").read_text(encoding="utf-8"))
    assert payload["candidate_source"] == "agent_optimizer"
    assert payload["decision"]["accepted"] is True
    assert payload["results"]["train"]["delta"] > 0
    assert payload["results"]["val"]["delta"] > 0
    assert payload["optimizer"]["rounds"][0]["candidate_prompt"] == _CANDIDATE
    assert Path(payload["optimizer"]["rounds"][0]["artifact_path"]).is_file()
    assert payload["cost"] == {
        "status": "measured",
        "total_tokens": 123,
        "total_usd": 0.0123,
    }
    assert all(Path(path).exists() for path in payload["optimizer"]["artifacts"].values())

    audit_path = Path(payload["optimizer"]["artifacts"]["call_agent_audit"])
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit
    assert all("query" in entry and "eval_contexts" in entry for entry in audit)
    assert all(context["eval_id"] != entry["query"] for entry in audit for context in entry["eval_contexts"])


async def test_cost_unavailable_rejects_apply_and_preserves_champion(
    monkeypatch,
    loop_root: Path,
) -> None:
    _install_optimizer_stub(
        monkeypatch,
        total_tokens=0,
        total_cost=0.0,
        reflection_calls=1,
    )
    champion = loop_root / "prompts" / "system.md"
    before = champion.read_bytes()

    import pipeline

    rc = await pipeline.amain(
        ["--mode", "optimize", "--apply"],
        call_agent=_call_agent(loop_root),
    )
    assert rc == 2
    assert champion.read_bytes() == before

    payload = json.loads((loop_root / "optimization_report.json").read_text(encoding="utf-8"))
    assert payload["decision"]["accepted"] is False
    assert "G6" in payload["decision"]["violated"]
    assert payload["audit"]["applied"] is False
    assert payload["cost"] == {
        "status": "unavailable",
        "total_tokens": None,
        "total_usd": None,
    }


async def test_missing_model_config_writes_auditable_reject(
    monkeypatch,
    loop_root: Path,
) -> None:
    for key in (
        "TRPC_AGENT_API_KEY",
        "TRPC_AGENT_BASE_URL",
        "TRPC_AGENT_MODEL_NAME",
    ):
        monkeypatch.delenv(key, raising=False)

    import pipeline

    rc = await pipeline.amain(["--mode", "optimize"])
    assert rc == 1
    payload = json.loads(
        (loop_root / "optimization_report.json").read_text(encoding="utf-8")
    )
    assert payload["decision"]["violated"] == ["OPTIMIZER_FAILURE", "G6"]
    assert payload["cost"] == {
        "status": "unavailable",
        "total_tokens": None,
        "total_usd": None,
    }
    assert "TRPC_AGENT_API_KEY" in payload["optimizer"]["error"]
    assert Path(
        payload["optimizer"]["artifacts"]["optimizer_error"]
    ).is_file()
