"""eval_optimize_loop 闭环测试。

覆盖 issue #91 验收点：
- 三类场景决策（robust accept / ineffective reject / overfit reject）
- 过拟合检测（val 退化、train 提升）
- 失败归因 coverage ≥ 75% & 类别准确率 ≥ 75%
- fake 模式全流程 ≤ 180s（issue 要求 ≤3 分钟）
- 报告必含字段 + 逐 case delta 五桶
- 隐藏样本归因泛化
- CLI 退出码 0=accept
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
EXAMPLE_DIR = HERE.parent
sys.path.insert(0, str(EXAMPLE_DIR))

from offline.fixtures import CASES  # noqa: E402
from pipeline.attribution import attribute_failure  # noqa: E402
from pipeline.config import load_eval_config, load_gate_config  # noqa: E402
from run_pipeline import run_fake  # noqa: E402


@pytest.fixture
def configs():
    gate = load_gate_config(EXAMPLE_DIR / "gate.json")
    eval_config = load_eval_config(EXAMPLE_DIR / "optimizer.json")
    return gate, eval_config


@pytest.mark.asyncio
async def test_three_scenarios(tmp_path, configs):
    """三类场景：robust 接受、ineffective 拒绝、overfit 拒绝。"""
    gate, eval_config = configs
    report = await run_fake(gate, eval_config, tmp_path, "pytest", "fake")
    decisions = {c.candidate_id: c.gate.decision for c in report.candidates}
    assert decisions["robust"] == "accept"
    assert decisions["ineffective"] == "reject"
    assert decisions["overfit"] == "reject"
    assert report.selected_candidate_id == "robust"
    assert report.status == "accept"


@pytest.mark.asyncio
async def test_overfit_detection(tmp_path, configs):
    """过拟合：train 提升 + val 退化 → overfit_detected=True。"""
    gate, eval_config = configs
    report = await run_fake(gate, eval_config, tmp_path, "pytest", "fake")
    overfit = next(c for c in report.candidates if c.candidate_id == "overfit")
    assert overfit.gate.overfitting_detected is True
    assert overfit.delta.train.pass_rate_delta > 0
    assert overfit.delta.validation.pass_rate_delta < 0
    # critical case 必须被检出退化（new_fail=pass→fail 或 regressed=分数降，都算退化）
    degraded = overfit.delta.buckets.new_fail + overfit.delta.buckets.regressed
    assert "val_fiction_key" in degraded
    # 且 gate 的 critical 回归检查必须 fail
    critical_check = next(ch for ch in overfit.gate.checks if ch.check == "no_critical_regression")
    assert not critical_check.passed


@pytest.mark.asyncio
async def test_robust_not_flagged_overfit(tmp_path, configs):
    """健康候选（val 也提升）不应被误判过拟合。"""
    gate, eval_config = configs
    report = await run_fake(gate, eval_config, tmp_path, "pytest", "fake")
    robust = next(c for c in report.candidates if c.candidate_id == "robust")
    assert robust.gate.overfitting_detected is False
    assert robust.delta.validation.pass_rate_delta > 0


@pytest.mark.asyncio
async def test_attribution_coverage_and_accuracy(tmp_path, configs):
    """归因 coverage ≥ 75%、类别准确率 ≥ 75%（issue 验收点 4）。"""
    gate, eval_config = configs
    report = await run_fake(gate, eval_config, tmp_path, "pytest", "fake")
    fa = report.failure_attribution
    assert fa.coverage_rate >= 0.75
    # 类别准确率：归因结果对 gold（expected_category）
    gold = {c["eval_id"]: c["expected_category"] for c in CASES}
    explained = {eid: a for eid, a in fa.by_case.items() if a.category != "unknown"}
    correct = sum(1 for eid, a in explained.items() if a.category == gold.get(eid))
    assert correct / len(explained) >= 0.75
    # 每个失败 case 至少一个可解释原因
    assert fa.explained_failed_cases == fa.total_failed_cases


@pytest.mark.asyncio
async def test_duration_under_3min(tmp_path, configs):
    """fake 全流程 ≤ 180s（issue 验收点 5）。"""
    gate, eval_config = configs
    t0 = time.time()
    await run_fake(gate, eval_config, tmp_path, "pytest", "fake")
    elapsed = time.time() - t0
    assert elapsed <= 180, f"耗时 {elapsed:.1f}s 超过 180s 预算"


def test_report_required_fields(tmp_path, configs):
    gate, eval_config = configs
    import asyncio

    report = asyncio.run(run_fake(gate, eval_config, tmp_path, "pytest", "fake"))
    data = json.loads(report.model_dump_json())
    for field in [
            "schema_version",
            "status",
            "mode",
            "seed",
            "baseline",
            "candidates",
            "selected_candidate_id",
            "failure_attribution",
            "audit",
    ]:
        assert field in data, f"报告缺字段 {field}"
    # baseline 含 train+val 分数
    assert "pass_rate" in data["baseline"]["train"]
    assert "pass_rate" in data["baseline"]["validation"]
    # 每个候选含逐 case delta 五桶 + gate decision + 理由
    buckets_keys = {"new_pass", "new_fail", "improved", "regressed", "unchanged"}
    for cand in data["candidates"]:
        assert buckets_keys <= set(cand["delta"]["buckets"])
        assert "decision" in cand["gate"]
        assert cand["gate"]["checks"]  # 有 checks 才有理由


def test_hidden_attribution_samples():
    """隐藏归因样本：测归因器对未见 case 的泛化（≥75%）。"""
    hidden = [
        (
            {
                "expected_response": "category",
                "expected_tool_uses": [],
                "variants": {
                    "x": {
                        "response": "没有结构的纯文本",
                        "tool_uses": []
                    }
                },
            },
            "format_violation",
        ),
        (
            {
                "expected_response": "ok",
                "expected_tool_uses": [{
                    "name": "search",
                    "args": {
                        "q": "a"
                    }
                }],
                "variants": {
                    "x": {
                        "response": "ok",
                        "tool_uses": []
                    }
                },
            },
            "knowledge_recall_insufficient",
        ),
        (
            {
                "expected_response": "ok",
                "expected_tool_uses": [{
                    "name": "search",
                    "args": {
                        "q": "a"
                    }
                }],
                "variants": {
                    "x": {
                        "response": "ok",
                        "tool_uses": [{
                            "name": "search",
                            "args": {
                                "q": "WRONG"
                            }
                        }]
                    }
                },
            },
            "tool_parameter_error",
        ),
        (
            {
                "expected_response": "answer",
                "expected_tool_uses": [],
                "variants": {
                    "x": {
                        "response": "完全不同的内容",
                        "tool_uses": []
                    }
                },
            },
            "final_response_mismatch",
        ),
        (
            {
                "expected_response": "ok",
                "expected_tool_uses": [{
                    "name": "calc",
                    "args": {}
                }],
                "variants": {
                    "x": {
                        "response": "ok",
                        "tool_uses": [{
                            "name": "WRONG_TOOL",
                            "args": {}
                        }]
                    }
                },
            },
            "tool_selection_error",
        ),
    ]
    correct = 0
    for spec, expected_cat in hidden:
        attr = attribute_failure(spec, "x")
        if attr.category == expected_cat:
            correct += 1
    assert correct / len(hidden) >= 0.75, (f"隐藏归因准确率 {correct}/{len(hidden)} 不足 75%")


def test_cli_exit_code_accept(tmp_path):
    """fake 模式 CLI：accept → 退出码 0。"""
    env = {**os.environ, "PYTHONUTF8": "1"}
    r = subprocess.run(
        [sys.executable,
         str(EXAMPLE_DIR / "run_pipeline.py"), "--mode", "fake", "--output-dir",
         str(tmp_path)],
        capture_output=True,
        env=env,
    )
    assert r.returncode == 0, f"期望退出码 0，实际 {r.returncode}; stderr={r.stderr.decode(errors='replace')}"
    assert (tmp_path / "optimization_report.json").exists()


def test_data_quality_no_cross_split_leakage(configs):
    """train/val 无 eval_id 重复（防数据污染）。"""
    from run_pipeline import check_data_quality

    dq = check_data_quality(CASES)
    assert dq.passed
    assert dq.cross_split_duplicates == 0
    assert dq.train_cases == 3
    assert dq.validation_cases == 3
