# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""三场景端到端测试：报告契约（AC1/AC6）、过拟合必拒（AC3）、时限（AC5）、确定性。

三个场景在 module 级 fixture 里各跑一次并计时（AC5 直接用这份计时断言），
其余测试消费同一份结果 —— 既贴近 `--scenario all` 的真实用法，又避免重复
跑 pipeline 拖慢测试。fixture 会快照并恢复 prompt 源文件，任何用例失败都
不会把候选 prompt 留在工作区。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
_EXAMPLE_ROOT = _HERE.parent
_REPO_ROOT = _EXAMPLE_ROOT.parents[2]
for _p in (str(_REPO_ROOT), str(_EXAMPLE_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from trpc_agent_sdk.evaluation._agent_evaluator import _EvaluationCasesFailed  # noqa: E402

import run_pipeline  # noqa: E402
from loop_pipeline import evaluate as evaluate_module  # noqa: E402
from loop_pipeline.attribution import cluster  # noqa: E402
from loop_pipeline.evaluate import run_eval  # noqa: E402
from loop_pipeline.report import REQUIRED_TOP_LEVEL_KEYS, validate_report  # noqa: E402

PROMPT_FILES = (
    _EXAMPLE_ROOT / "loop_agent" / "prompts" / "system.md",
    _EXAMPLE_ROOT / "loop_agent" / "prompts" / "skill.md",
)

ALL_EVAL_IDS = {
    "train_convert_3km",
    "train_intro_shenzhen",
    "train_identity",
    "val_convert_5km",
    "val_identity",
    "val_intro_hangzhou",
}


@pytest.fixture(autouse=True)
def _restore_prompts():
    """快照 + 恢复 prompt 源文件：测试永不污染工作区。"""
    snapshot = {path: path.read_bytes() for path in PROMPT_FILES}
    yield
    for path, content in snapshot.items():
        if path.read_bytes() != content:
            path.write_bytes(content)


@pytest.fixture(scope="module")
def all_scenarios(tmp_path_factory):
    """顺序跑三场景（同 --scenario all），返回 {场景: (报告, 输出目录)} 与总耗时。"""
    snapshot = {path: path.read_bytes() for path in PROMPT_FILES}
    output_root = tmp_path_factory.mktemp("runs")
    reports: dict[str, dict] = {}
    started = time.monotonic()
    try:
        for scenario in ("success", "no_effect", "overfit"):
            reports[scenario] = asyncio.run(run_pipeline.run_scenario(scenario, output_root, quiet=True))
    finally:
        for path, content in snapshot.items():
            if path.read_bytes() != content:
                path.write_bytes(content)
    elapsed = time.monotonic() - started
    return {"reports": reports, "elapsed": elapsed, "output_root": output_root}


def _report_dir(output_root: Path, scenario: str) -> Path:
    dirs = sorted(output_root.glob(f"{scenario}-*"))
    assert dirs, f"未找到 {scenario} 的输出目录"
    return dirs[-1]


# ---------------------------------------------------------------------------
# AC1：六条 case 全部可运行并产出完整报告；AC6：报告字段契约
# ---------------------------------------------------------------------------


def test_success_scenario_end_to_end(all_scenarios):
    report = all_scenarios["reports"]["success"]
    out_dir = _report_dir(all_scenarios["output_root"], "success")

    # 报告文件 + 审计产物齐全
    assert (out_dir / "optimization_report.json").is_file()
    assert (out_dir / "optimization_report.md").is_file()
    assert (out_dir / "baseline_eval.json").is_file()
    assert (out_dir / "candidate_eval.json").is_file()
    assert (out_dir / "attribution.json").is_file()
    assert (out_dir / "pipeline_config.snapshot.json").is_file()
    optimize_dir = out_dir / "optimize"
    assert (optimize_dir / "result.json").is_file()
    assert (optimize_dir / "config.snapshot.json").is_file()
    assert list((optimize_dir / "rounds").glob("round_*.json")), "每轮审计记录缺失"
    assert list((optimize_dir / "best_prompts").glob("*.md")), "最优候选 prompt 快照缺失"
    assert list((optimize_dir / "baseline_prompts").glob("*.md")), "baseline prompt 快照缺失"

    # 6 条公开 case 全部出现在 baseline 明细里
    seen = {case["eval_id"] for split in ("train", "val") for case in report["baseline"][split]["per_case"]}
    assert seen == ALL_EVAL_IDS

    # 优化成功且被接受
    assert report["optimization"]["status"] == "SUCCEEDED"
    decision = report["gate_decision"]
    assert decision["accepted"] is True
    assert all(g["passed"] for g in decision["gates"])
    # 独立验证集：2 条 new_pass（convert + intro），保护 case 不动
    assert report["delta"]["val"]["counts"]["new_pass"] == 2
    assert report["delta"]["val"]["counts"]["new_fail"] == 0
    assert report["delta"]["val"]["pass_rate_delta"] == pytest.approx(2 / 3)
    assert report["delta"]["train"]["pass_rate_delta"] == pytest.approx(2 / 3)


def test_report_contract(all_scenarios):
    """AC6：三份报告全部满足字段契约；md 含关键章节；sample_output 同样校验。"""
    for scenario, report in all_scenarios["reports"].items():
        assert validate_report(report) == [], scenario
        for key in REQUIRED_TOP_LEVEL_KEYS:
            assert key in report, f"{scenario} 缺少 {key}"
        # baseline / candidate 分数、逐 case delta、gate 决策、接受/拒绝理由（AC6 原文点名）
        assert isinstance(report["baseline"]["val"]["pass_rate"], float)
        assert isinstance(report["candidate"]["val"]["pass_rate"], float)
        assert report["delta"]["val"]["per_case"], scenario
        assert isinstance(report["gate_decision"]["accepted"], bool)
        assert report["gate_decision"]["reason"]

        md_path = _report_dir(all_scenarios["output_root"], scenario) / "optimization_report.md"
        md = md_path.read_text(encoding="utf-8")
        for keyword in ("baseline", "candidate", "失败归因", "逐 case delta", "gate 决策", "是否值得接受", "理由"):
            assert keyword in md, f"{scenario} 的 md 缺少「{keyword}」章节"

    # 提交在仓库里的 sample_output 与运行时报告遵守同一契约
    for sample in sorted((_EXAMPLE_ROOT / "sample_output").glob("*/optimization_report.json")):
        report = json.loads(sample.read_text(encoding="utf-8"))
        assert validate_report(report) == [], sample


# ---------------------------------------------------------------------------
# 交付物：优化无效场景（REJECT）
# ---------------------------------------------------------------------------


def test_no_effect_scenario_rejected(all_scenarios):
    report = all_scenarios["reports"]["no_effect"]
    decision = report["gate_decision"]
    assert decision["accepted"] is False
    assert "提升不足" in decision["reason"] or "min_val_improvement" in decision["reason"]
    counts = report["delta"]["val"]["counts"]
    assert counts["unchanged"] == 3 and counts["new_pass"] == 0 and counts["new_fail"] == 0
    assert report["delta"]["val"]["pass_rate_delta"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# AC3：过拟合（train 提升、val 退化）必须拒绝
# ---------------------------------------------------------------------------


def test_overfit_scenario_rejected(all_scenarios):
    report = all_scenarios["reports"]["overfit"]

    # 优化器视角一路变好（它看到的是泄漏的 probe 集）……
    opt_view = report["optimization"]["optimizer_val_pass_rate"]
    assert opt_view["best"] > opt_view["baseline"]
    # ……但独立数据集复评：train 提升、val 退化
    assert report["delta"]["train"]["pass_rate_delta"] > 0
    assert report["delta"]["val"]["pass_rate_delta"] < 0

    decision = report["gate_decision"]
    assert decision["accepted"] is False
    assert "过拟合" in decision["reason"]
    gates = {g["name"]: g["passed"] for g in decision["gates"]}
    assert gates["overfit_guard"] is False
    assert gates["protected_cases"] is False  # 保护 case val_identity 退化
    assert gates["no_new_hard_fail"] is False

    # 保护 case val_identity 是 new_fail
    val_changes = {c["eval_id"]: c["change"] for c in report["delta"]["val"]["per_case"]}
    assert val_changes["val_identity"] == "new_fail"


# ---------------------------------------------------------------------------
# AC5：fake/trace 模式下完整 pipeline ≤ 3 分钟；trace 模式路径可用
# ---------------------------------------------------------------------------


def test_all_scenarios_under_time_budget(all_scenarios):
    assert all_scenarios["elapsed"] < 180.0, f"三场景总耗时 {all_scenarios['elapsed']:.1f}s 超过 3 分钟"
    for report in all_scenarios["reports"].values():
        assert report["runtime"]["pipeline_duration_seconds"] < 180.0


def test_trace_mode_baseline(tmp_path):
    """trace 模式：不执行 agent，直接对预录轨迹评测并归因。"""
    records = asyncio.run(
        run_eval(str(_EXAMPLE_ROOT / "data" / "trace_baseline.evalset.json"),
                 str(_EXAMPLE_ROOT / "data" / "eval_config.json"),
                 agent_module=None))
    assert set(records) == {"trace_convert_3km", "trace_intro_shenzhen"}
    assert all(not r.passed for r in records.values())
    summary = cluster(records)
    assert summary.primary["trace_convert_3km"] == "wrong_tool_args"
    assert summary.primary["trace_intro_shenzhen"] == "wrong_tool_call"
    assert "knowledge_recall_miss" in {f.type for f in summary.per_case["trace_intro_shenzhen"]}

    # --baseline-from-trace 的 CLI 路径：trace 评测 + 归因明细随场景一起落盘
    report = asyncio.run(run_pipeline.run_scenario("success", tmp_path, baseline_from_trace=True, quiet=True))
    assert report["runtime"]["baseline_from_trace"] is True
    out_dir = sorted(tmp_path.glob("success-*"))[-1]
    trace_eval = json.loads((out_dir / "trace_eval.json").read_text(encoding="utf-8"))
    assert set(trace_eval) == {"trace_convert_3km", "trace_intro_shenzhen"}
    trace_attr = json.loads((out_dir / "trace_attribution.json").read_text(encoding="utf-8"))
    assert set(trace_attr) == {"trace_convert_3km", "trace_intro_shenzhen"}
    assert all(f["type"] and f["explanation"] for findings in trace_attr.values() for f in findings)
    assert {f["type"] for f in trace_attr["trace_intro_shenzhen"]} >= {"wrong_tool_call", "knowledge_recall_miss"}


# ---------------------------------------------------------------------------
# run_eval 只吞框架的 _EvaluationCasesFailed；真实断言失败必须冒出来
# ---------------------------------------------------------------------------


def test_run_eval_only_swallows_eval_cases_failed(monkeypatch):

    class _FakeEvaluator:

        exc: Exception = AssertionError("genuine third-party assertion")

        @staticmethod
        def get_executer(*_args, **_kwargs):

            class _Executer:

                async def evaluate(self):
                    raise _FakeEvaluator.exc

                def get_result(self):
                    return SimpleNamespace(results_by_eval_set_id={})

            return _Executer()

    monkeypatch.setattr(evaluate_module, "AgentEvaluator", _FakeEvaluator)
    with pytest.raises(AssertionError, match="genuine third-party assertion"):
        asyncio.run(run_eval("ds.json", "cfg.json"))

    # 框架的 _EvaluationCasesFailed（case 失败信号）仍被吞掉，照常取结果
    _FakeEvaluator.exc = _EvaluationCasesFailed("cases failed")
    assert asyncio.run(run_eval("ds.json", "cfg.json")) == {}


# ---------------------------------------------------------------------------
# --check：README 命令必须 cwd 无关；文件缺失给友好错误而不是 traceback
# ---------------------------------------------------------------------------


def test_check_command_is_cwd_independent():
    script = _EXAMPLE_ROOT / "run_pipeline.py"
    ok = subprocess.run(
        [sys.executable, str(script), "--check", "sample_output/success/optimization_report.json"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0, ok.stderr
    assert "报告契约校验通过" in ok.stdout

    missing = subprocess.run(
        [sys.executable, str(script), "--check", "no/such/report.json"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 1
    assert "Traceback" not in (missing.stdout + missing.stderr)
    assert "报告文件不存在" in missing.stderr


# ---------------------------------------------------------------------------
# --apply --scenario all：写回延后到全部场景结束，后续场景 baseline 不被污染
# ---------------------------------------------------------------------------


def test_apply_with_scenario_all_defers_write_until_all_done(tmp_path):
    baseline_system = PROMPT_FILES[0].read_text(encoding="utf-8")
    args = argparse.Namespace(scenario="all",
                              output=str(tmp_path),
                              baseline_from_trace=False,
                              apply=True,
                              quiet=True,
                              check=None)
    assert asyncio.run(run_pipeline._amain(args)) == 0
    # 全部结束后：success 被接受，最优候选已写回（源文件发生变化）
    assert PROMPT_FILES[0].read_text(encoding="utf-8") != baseline_system
    # 后跑的 no_effect / overfit 的 baseline 仍是干净 baseline（val 通过率 1/3），
    # 若 success 的写回发生在场景循环中，这里会被污染成更高的通过率
    for scenario in ("no_effect", "overfit"):
        out_dir = sorted(tmp_path.glob(f"{scenario}-*"))[-1]
        report = json.loads((out_dir / "optimization_report.json").read_text(encoding="utf-8"))
        assert report["baseline"]["val"]["pass_rate"] == pytest.approx(1 / 3), scenario


# ---------------------------------------------------------------------------
# 确定性：同输入两次运行，决策与逐 case delta 完全一致
# ---------------------------------------------------------------------------


def test_deterministic_reruns(all_scenarios, tmp_path):
    first = all_scenarios["reports"]["success"]
    second = asyncio.run(run_pipeline.run_scenario("success", tmp_path, quiet=True))

    def stable_view(report: dict) -> dict:
        return {
            "delta": report["delta"],
            "accepted": report["gate_decision"]["accepted"],
            "gates": [(g["name"], g["passed"]) for g in report["gate_decision"]["gates"]],
            "baseline_pass": {
                s: report["baseline"][s]["pass_rate"]
                for s in ("train", "val")
            },
            "candidate_pass": {
                s: report["candidate"][s]["pass_rate"]
                for s in ("train", "val")
            },
        }

    assert stable_view(first) == stable_view(second)


def test_prompts_untouched_after_runs(all_scenarios):
    """三场景跑完后源 prompt 仍是 baseline（write_all 快照恢复生效）。"""
    system_text = PROMPT_FILES[0].read_text(encoding="utf-8")
    assert "output_format: plain" in system_text
    assert "memorize: off" in system_text
