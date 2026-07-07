# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Regression tests for the eval_optimize_loop example."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_CONFIG
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_TRAIN
from examples.optimization.eval_optimize_loop.run_pipeline import DEFAULT_VAL
from examples.optimization.eval_optimize_loop.run_pipeline import run_pipeline


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _config_for_tmp(tmp_path: Path, *, critical_case_ids: list[str] | None = None) -> Path:
    config = deepcopy(_load_json(DEFAULT_CONFIG))
    prompt_dir = DEFAULT_CONFIG.parent / "prompts"
    config["optimize"]["target_prompts"] = [
        {"name": "system_prompt", "path": str(prompt_dir / "system.md")},
        {"name": "skill_prompt", "path": str(prompt_dir / "skill.md")},
    ]
    if critical_case_ids is not None:
        config["gate"]["critical_case_ids"] = critical_case_ids
    return _write_json(tmp_path / "optimizer.json", config)


def _prompt_dir(tmp_path: Path, *, system: str | None = None, skill: str | None = None) -> Path:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "system.md").write_text(system or "Baseline prompt.\n", encoding="utf-8")
    (prompt_dir / "skill.md").write_text(skill or "Use tools when needed.\n", encoding="utf-8")
    return prompt_dir


def _config_with_prompts(tmp_path: Path, prompt_dir: Path) -> Path:
    config = deepcopy(_load_json(DEFAULT_CONFIG))
    config["optimize"]["target_prompts"] = [
        {"name": "system_prompt", "path": str(prompt_dir / "system.md")},
        {"name": "skill_prompt", "path": str(prompt_dir / "skill.md")},
    ]
    return _write_json(tmp_path / "optimizer.json", config)


def _renamed_evalset(source: Path, replacements: dict[str, str]) -> dict:
    payload = deepcopy(_load_json(source))
    payload["eval_set_id"] = payload["eval_set_id"] + "_renamed"
    for case in payload["eval_cases"]:
        case["eval_id"] = replacements.get(case["eval_id"], case["eval_id"])
    return payload


@pytest.mark.asyncio
async def test_eval_optimize_loop_rejects_validation_regression(tmp_path: Path):
    report = await run_pipeline(
        config_path=DEFAULT_CONFIG,
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        output_dir=tmp_path,
    )

    assert report["gate_decision"]["decision"] == "reject"
    assert report["delta"]["train"]["total_score_delta"] > 0
    assert report["delta"]["validation"]["total_score_delta"] < 0
    assert report["delta"]["validation"]["new_passes"] == ["val_format_json"]
    assert set(report["delta"]["validation"]["new_failures"]) == {
        "val_critical_discount",
        "val_stable_refund",
    }
    assert "val_critical_discount" in report["gate_decision"]["checks"]["critical_cases_not_degraded"]["regressed"]

    all_case_ids = {case["eval_id"] for case in report["baseline"]["train"]["cases"]}
    all_case_ids.update(case["eval_id"] for case in report["baseline"]["validation"]["cases"])
    assert all_case_ids == {
        "train_format_json",
        "train_tool_args",
        "train_knowledge_gap",
        "val_format_json",
        "val_critical_discount",
        "val_stable_refund",
    }

    for phase in (
            report["baseline"]["train"],
            report["baseline"]["validation"],
            report["candidate"]["train"],
            report["candidate"]["validation"],
    ):
        for case in phase["cases"]:
            if case["status"] == "failed":
                assert case["failure_reasons"]

    report_json = tmp_path / "optimization_report.json"
    report_md = tmp_path / "optimization_report.md"
    assert report_json.exists()
    assert report_md.exists()
    persisted = json.loads(report_json.read_text(encoding="utf-8"))
    assert persisted["gate_decision"]["decision"] == "reject"
    rendered = report_md.read_text(encoding="utf-8")
    assert "Train Case Delta" in rendered
    assert "Validation Case Delta" in rendered
    assert "knowledge_recall_insufficient" in rendered


@pytest.mark.asyncio
async def test_eval_optimize_loop_rejects_semantic_regression_without_public_ids(tmp_path: Path):
    train = _renamed_evalset(
        DEFAULT_TRAIN,
        {
            "train_format_json": "hidden_train_json",
            "train_tool_args": "hidden_train_weather",
            "train_knowledge_gap": "hidden_train_private_fact",
        },
    )
    val = _renamed_evalset(
        DEFAULT_VAL,
        {
            "val_format_json": "hidden_val_json",
            "val_critical_discount": "hidden_val_critical_vip",
            "val_stable_refund": "hidden_val_refund_sla",
        },
    )
    train_path = _write_json(tmp_path / "train.evalset.json", train)
    val_path = _write_json(tmp_path / "val.evalset.json", val)
    config_path = _config_for_tmp(tmp_path, critical_case_ids=["hidden_val_critical_vip"])

    report = await run_pipeline(
        config_path=config_path,
        train_path=train_path,
        val_path=val_path,
        output_dir=tmp_path,
    )

    assert report["gate_decision"]["decision"] == "reject"
    assert report["delta"]["train"]["total_score_delta"] > 0
    assert report["delta"]["validation"]["total_score_delta"] < 0
    assert report["delta"]["validation"]["new_passes"] == ["hidden_val_json"]
    assert set(report["delta"]["validation"]["new_failures"]) == {
        "hidden_val_critical_vip",
        "hidden_val_refund_sla",
    }
    assert "hidden_val_critical_vip" in report["gate_decision"]["checks"]["critical_cases_not_degraded"]["regressed"]


@pytest.mark.asyncio
async def test_eval_optimize_loop_accepts_clean_validation_gain(tmp_path: Path):
    train_path = DEFAULT_TRAIN
    val = deepcopy(_load_json(DEFAULT_VAL))
    val["eval_set_id"] = "eval_optimize_loop_val_gain_only"
    val["eval_cases"] = [case for case in val["eval_cases"] if case["eval_id"] == "val_format_json"]
    val["eval_cases"][0]["eval_id"] = "hidden_val_json_gain"
    val_path = _write_json(tmp_path / "val.evalset.json", val)
    config_path = _config_for_tmp(tmp_path, critical_case_ids=[])

    report = await run_pipeline(
        config_path=config_path,
        train_path=train_path,
        val_path=val_path,
        output_dir=tmp_path,
    )

    assert report["gate_decision"]["decision"] == "accept"
    assert report["delta"]["validation"]["total_score_delta"] > 0
    assert report["delta"]["validation"]["new_passes"] == ["hidden_val_json_gain"]
    assert report["delta"]["validation"]["new_failures"] == []
    assert report["gate_decision"]["reasons"] == ["candidate passed every configured gate"]


@pytest.mark.asyncio
async def test_eval_optimize_loop_candidate_behavior_depends_on_candidate_prompt(tmp_path: Path):
    config = deepcopy(_load_json(DEFAULT_CONFIG))
    prompt_dir = DEFAULT_CONFIG.parent / "prompts"
    config["optimize"]["target_prompts"] = [
        {"name": "system_prompt", "path": str(prompt_dir / "system.md")},
        {"name": "skill_prompt", "path": str(prompt_dir / "skill.md")},
    ]
    config["optimize"]["fake_model"]["candidate_patch"] = []
    config_path = _write_json(tmp_path / "optimizer.json", config)

    report = await run_pipeline(
        config_path=config_path,
        train_path=DEFAULT_TRAIN,
        val_path=DEFAULT_VAL,
        output_dir=tmp_path,
    )

    assert report["delta"]["train"]["new_passes"] == []
    assert report["delta"]["validation"]["new_passes"] == []
    assert report["delta"]["validation"]["new_failures"] == []
    assert report["delta"]["validation"]["total_score_delta"] == 0


@pytest.mark.asyncio
async def test_eval_optimize_loop_agent_optimizer_requires_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    prompt_dir = _prompt_dir(tmp_path)
    config_path = _config_with_prompts(tmp_path, prompt_dir)
    monkeypatch.delenv("TRPC_AGENT_OPT_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="TRPC_AGENT_OPT_API_KEY"):
        await run_pipeline(
            config_path=config_path,
            train_path=DEFAULT_TRAIN,
            val_path=DEFAULT_VAL,
            output_dir=tmp_path,
            backend_override="agent_optimizer",
        )
