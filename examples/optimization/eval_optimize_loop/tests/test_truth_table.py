#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""端到端真值表测试：直接调用 pipeline + runner + SDK evaluator，
对三个 fake 场景断言 case-level 分数、train/val delta、decision。

该测试是 Issue #91 Milestone A 的验收测试：
test_fake_agent.py 单独通过不能视为验收完成。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """强制无 API key，验证 fake 模式不需 key 也能跑。"""
    for k in ("OPENAI_API_KEY", "TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"):
        monkeypatch.delenv(k, raising=False)


def _per_case_scores(report: dict) -> dict[str, dict[str, float]]:
    """把 per_case 列表整理成 {eval_id: {champ, chall, delta}}。"""
    out = {}
    for c in report["per_case"]:
        out[c["eval_id"]] = {
            "champion": c["champion_score"],
            "challenger": c["challenger_score"],
            "delta": c["delta"],
            "split": c["split"],
            "protected": c["protected"],
        }
    return out


async def _run(scenario: str) -> dict:
    import pipeline

    rc = await pipeline.amain(["--mode", "fake", "--scenario", scenario])
    report_path = pipeline._HERE / "optimization_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["_exit_code"] = rc
    return report


# ---- 期望的真值表（与 README 一致） ----
# Champion:    train=[0,0,0]  val=[0,1,0]  (protected case 初始正确)
# success:     train=[1,1,1]  val=[1,1,1]  ACCEPT
# no_effect:   同 Champion                 REJECT G1
# overfit:     train=[1,1,1]  val=[0,0,0]  REJECT G2 + 过拟合

# eval_id 来自 data/train.evalset.json + data/val.evalset.json
_TRAIN_IDS = ["train_add_steps", "train_noop", "train_overfit_mem"]
_VAL_IDS = ["val_add_steps_b", "val_protected_highrisk", "val_noop_b"]


async def test_success_candidate_truth_table() -> None:
    """success 场景：train/val 全对，ACCEPT。"""
    report = await _run("success")

    scores = _per_case_scores(report)
    # Champion baseline
    for tid in _TRAIN_IDS:
        assert scores[tid]["champion"] == 0.0, f"{tid} champion should be 0"
    assert scores["val_protected_highrisk"]["champion"] == 1.0, "protected case 初始正确"
    for vid in ("val_add_steps_b", "val_noop_b"):
        assert scores[vid]["champion"] == 0.0, f"{vid} champion should be 0"

    # Challenger 全对
    for tid in _TRAIN_IDS:
        assert scores[tid]["challenger"] == 1.0, f"{tid} challenger should be 1"
    for vid in _VAL_IDS:
        assert scores[vid]["challenger"] == 1.0, f"{vid} challenger should be 1"

    # delta
    assert report["train_delta"] > 0
    assert report["val_delta"] > 0
    assert report["val_delta"] >= 0.02, "val_delta >= min_val_lift"

    # decision
    assert report["decision"]["accepted"] is True
    assert report["decision"]["violated"] == []


async def test_no_effect_candidate_truth_table() -> None:
    """no_effect 场景：candidate 行为 = Champion，REJECT G1。"""
    report = await _run("no_effect")

    scores = _per_case_scores(report)
    # Champion 与 Challenger 分数完全相同
    for eid in _TRAIN_IDS + _VAL_IDS:
        assert scores[eid]["champion"] == scores[eid]["challenger"], f"{eid} champ=chall"
    # delta == 0
    assert abs(report["train_delta"]) < 1e-9
    assert abs(report["val_delta"]) < 1e-9

    # decision: REJECT 且违反 G1
    assert report["decision"]["accepted"] is False
    assert "G1" in report["decision"]["violated"]


async def test_overfit_candidate_truth_table() -> None:
    """overfit 场景：train 涨 val 跌，REJECT G2 且理由含'过拟合'。"""
    report = await _run("overfit")

    scores = _per_case_scores(report)
    # Champion: train=[0,0,0], val=[0,1,0]
    for tid in _TRAIN_IDS:
        assert scores[tid]["champion"] == 0.0
    assert scores["val_protected_highrisk"]["champion"] == 1.0
    for vid in ("val_add_steps_b", "val_noop_b"):
        assert scores[vid]["champion"] == 0.0

    # Challenger: train=[1,1,1], val=[0,0,0]  (含 protected 也跌)
    for tid in _TRAIN_IDS:
        assert scores[tid]["challenger"] == 1.0, f"{tid} challenger should be 1 (memorized)"
    for vid in _VAL_IDS:
        assert scores[vid]["challenger"] == 0.0, f"{vid} challenger should be 0 (forgot)"

    # delta: train > 0, val < 0
    assert report["train_delta"] > 0
    assert report["val_delta"] < 0, f"val_delta={report['val_delta']} should be negative"

    # decision: REJECT 且违反 G2（过拟合）
    assert report["decision"]["accepted"] is False
    assert "G2" in report["decision"]["violated"], report["decision"]
    assert any("过拟合" in r for r in report["decision"]["reasons"]), report["decision"]
    # G3（high-risk 新增 fail）与 G4（protected 退化）也应触发
    assert "G3" in report["decision"]["violated"]
    assert "G4" in report["decision"]["violated"]
