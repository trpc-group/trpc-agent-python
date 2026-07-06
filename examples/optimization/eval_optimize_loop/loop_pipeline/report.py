# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""阶段⑥：optimization_report.json / optimization_report.md 渲染与校验。

``optimization_report.json`` 顶层字段契约（验收标准 6；tests/test_pipeline_e2e
逐一断言，``validate_report`` 供测试与 ``--check`` 共用）：

- ``schema_version`` / ``scenario`` / ``generated_at`` / ``seed``
- ``inputs``        train/val 数据集、optimizer 配置、pipeline 配置、prompt 源文件
- ``baseline``      train/val 两个切分的分数与逐 case 明细（含失败归因与轨迹）
- ``attribution``   失败类型聚类统计（counts_by_type / primary_by_case / details）
- ``optimization``  优化器运行摘要（算法、状态、轮次、成本、审计目录）
- ``candidate``     候选在 train/val 上的复评结果（结构同 baseline）
- ``delta``         逐 case delta（new_pass / new_fail / score_up / score_down）
- ``gate_decision`` 接受/拒绝 + 理由 + 六道闸门明细
- ``runtime``       各阶段耗时与 fake 模型调用计数

``optimization_report.md`` 是给人看的版本：概览表、失败归因统计表、逐 case
delta 表、优化轮次摘要、gate 明细表，以及「是否值得接受」的中文结论段。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .attribution import FAILURE_TYPE_LABELS_ZH, AttributionSummary, attribute_case
from .evaluate import CaseEvalRecord, summarize
from .gates import GateDecision
from .regression import CHANGE_LABELS_ZH, DeltaSummary

SCHEMA_VERSION = "v1"

# 报告顶层必备字段（validate_report / 测试共用）
REQUIRED_TOP_LEVEL_KEYS = (
    "schema_version",
    "scenario",
    "generated_at",
    "seed",
    "inputs",
    "baseline",
    "attribution",
    "optimization",
    "candidate",
    "delta",
    "gate_decision",
    "runtime",
)

_TRUNCATE = 200  # 逐 case 文本截断长度，控制报告体积


def _clip(text: str) -> str:
    text = text or ""
    return text if len(text) <= _TRUNCATE else text[:_TRUNCATE] + "…"


def _case_view(record: CaseEvalRecord) -> dict[str, Any]:
    """逐 case 摘要：分数、失败归因（类型+理由）、关键轨迹。"""
    findings = attribute_case(record)
    return {
        "eval_id": record.eval_id,
        "passed": record.passed,
        "final_status": record.final_status,
        "case_score": round(record.case_score, 6),
        "metric_scores": record.metric_scores,
        "metric_status": record.metric_status,
        "failure_types": [f.type for f in findings],
        "failure_reasons": [f"[{f.metric}] {f.explanation}（{_clip(f.evidence)}）" for f in findings],
        "trajectory": {
            "actual_tool_calls": record.actual_tool_calls,
            "expected_tool_calls": record.expected_tool_calls,
        },
        "actual_response": _clip(record.actual_response),
        "expected_response": _clip(record.expected_response),
    }


def _split_view(records: dict[str, CaseEvalRecord]) -> dict[str, Any]:
    summary = summarize(records)
    return {
        "pass_rate": round(summary.pass_rate, 6),
        "passed": summary.passed,
        "total": summary.total,
        "mean_score": round(summary.mean_case_score, 6),
        "metric_breakdown": {
            k: round(v, 6)
            for k, v in summary.metric_breakdown.items()
        },
        "per_case": [_case_view(records[eval_id]) for eval_id in sorted(records)],
    }


def _attribution_view(train: AttributionSummary, val: AttributionSummary) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for summary in (train, val):
        for failure_type, count in summary.counts.items():
            counts[failure_type] = counts.get(failure_type, 0) + count
    details: dict[str, list[dict]] = {}
    primary: dict[str, str] = {}
    for summary in (train, val):
        primary.update(summary.primary)
        for eval_id, findings in summary.per_case.items():
            details[eval_id] = [asdict(f) for f in findings]
    return {"counts_by_type": counts, "primary_by_case": primary, "details": details}


def _delta_view(delta: DeltaSummary) -> dict[str, Any]:
    return {
        "pass_rate_delta":
        round(delta.pass_rate_delta, 6),
        "score_delta":
        round(delta.score_delta, 6),
        "counts":
        delta.counts,
        "per_case": [{
            "eval_id": d.eval_id,
            "baseline_passed": d.baseline_passed,
            "candidate_passed": d.candidate_passed,
            "baseline_score": round(d.baseline_score, 6),
            "candidate_score": round(d.candidate_score, 6),
            "change": d.change,
        } for d in delta.per_case],
    }


def _prompt_digest(prompts: dict[str, str]) -> dict[str, dict[str, str]]:
    """候选 prompt 的 sha256 + 摘要（完整文本已由 SDK 落盘 best_prompts/）。"""
    return {
        name: {
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "preview": _clip(text.strip()),
        }
        for name, text in prompts.items()
    }


def build_report(
    *,
    scenario: str,
    seed: int,
    inputs: dict[str, Any],
    baseline: dict[str, dict[str, CaseEvalRecord]],
    attribution_train: AttributionSummary,
    attribution_val: AttributionSummary,
    optimize_result,
    candidate: dict[str, dict[str, CaseEvalRecord]],
    delta_train: DeltaSummary,
    delta_val: DeltaSummary,
    decision: GateDecision,
    runtime: dict[str, Any],
    optimize_artifacts_dir: str,
) -> dict[str, Any]:
    """组装完整报告 dict（可 json 序列化）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "scenario": scenario,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "inputs": inputs,
        "baseline": {
            split: _split_view(records)
            for split, records in baseline.items()
        },
        "attribution": _attribution_view(attribution_train, attribution_val),
        "optimization": {
            "algorithm": optimize_result.algorithm,
            "status": optimize_result.status,
            "finish_reason": optimize_result.finish_reason,
            "stop_reason": optimize_result.stop_reason,
            "total_rounds": optimize_result.total_rounds,
            "rounds_accepted": sum(1 for r in optimize_result.rounds if r.accepted),
            "optimizer_val_pass_rate": {
                "baseline": round(optimize_result.baseline_pass_rate, 6),
                "best": round(optimize_result.best_pass_rate, 6),
            },
            "best_prompts": _prompt_digest(optimize_result.best_prompts),
            "cost": {
                "total_llm_cost": optimize_result.total_llm_cost,
                "reflection_lm_calls": optimize_result.total_reflection_lm_calls,
                "budget_used": (optimize_result.rounds[-1].budget_used if optimize_result.rounds else None),
                "budget_total": (optimize_result.rounds[-1].budget_total if optimize_result.rounds else None),
                "token_usage": optimize_result.total_token_usage,
            },
            "duration_seconds": round(optimize_result.duration_seconds, 3),
            "artifacts_dir": optimize_artifacts_dir,
        },
        "candidate": {
            split: _split_view(records)
            for split, records in candidate.items()
        },
        "delta": {
            "train": _delta_view(delta_train),
            "val": _delta_view(delta_val),
        },
        "gate_decision": {
            "accepted": decision.accepted,
            "reason": decision.reason,
            "gates": [{
                "name": g.name,
                "passed": g.passed,
                "detail": g.detail
            } for g in decision.gates],
        },
        "runtime": runtime,
    }


def validate_report(report: dict[str, Any]) -> list[str]:
    """校验报告契约；返回问题列表（空 = 通过）。测试与 --check 共用。"""
    problems: list[str] = []
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in report:
            problems.append(f"缺少顶层字段：{key}")
    if problems:
        return problems
    for split in ("train", "val"):
        for section in ("baseline", "candidate"):
            view = report[section].get(split)
            if not isinstance(view, dict):
                problems.append(f"{section}.{split} 缺失")
                continue
            for key in ("pass_rate", "mean_score", "metric_breakdown", "per_case"):
                if key not in view:
                    problems.append(f"{section}.{split} 缺少 {key}")
        delta = report["delta"].get(split)
        if not isinstance(delta, dict):
            problems.append(f"delta.{split} 缺失")
        else:
            for key in ("pass_rate_delta", "score_delta", "counts", "per_case"):
                if key not in delta:
                    problems.append(f"delta.{split} 缺少 {key}")
    for key in ("counts_by_type", "primary_by_case", "details"):
        if key not in report["attribution"]:
            problems.append(f"attribution 缺少 {key}")
    decision = report["gate_decision"]
    for key in ("accepted", "reason", "gates"):
        if key not in decision:
            problems.append(f"gate_decision 缺少 {key}")
    for key in ("status", "total_rounds", "cost", "artifacts_dir", "optimizer_val_pass_rate"):
        if key not in report["optimization"]:
            problems.append(f"optimization 缺少 {key}")
    # 每个失败 case 必须至少给出一个可解释原因（验收标准 4）
    for section in ("baseline", "candidate"):
        for split in ("train", "val"):
            for case in report[section][split].get("per_case", []):
                if not case.get("passed") and not case.get("failure_reasons"):
                    problems.append(f"{section}.{split} 的失败 case {case.get('eval_id')} 缺少失败原因")
    return problems


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _status_icon(passed: bool) -> str:
    return "✅" if passed else "❌"


def render_markdown(report: dict[str, Any]) -> str:
    """人话版报告：概览 / 归因 / 逐 case delta / 轮次 / gate / 结论。"""
    baseline_val = report["baseline"]["val"]
    baseline_train = report["baseline"]["train"]
    candidate_val = report["candidate"]["val"]
    candidate_train = report["candidate"]["train"]
    delta_val = report["delta"]["val"]
    delta_train = report["delta"]["train"]
    decision = report["gate_decision"]
    optimization = report["optimization"]

    lines: list[str] = []
    lines.append(f"# 优化报告 — 场景 `{report['scenario']}`")
    lines.append("")
    verdict = "✅ **接受候选 prompt**" if decision["accepted"] else "❌ **拒绝候选 prompt**"
    lines.append(f"> 结论：{verdict}")
    lines.append(f"> 理由：{decision['reason']}")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}　随机种子：{report['seed']}　"
                 f"报告 schema：{report['schema_version']}")
    lines.append(f"- 优化算法：{optimization['algorithm']}（status={optimization['status']}，"
                 f"{optimization['total_rounds']} 轮，接受 {optimization['rounds_accepted']} 轮，"
                 f"耗时 {optimization['duration_seconds']}s）")
    lines.append(f"- 审计产物目录：`{optimization['artifacts_dir']}`（每轮候选 prompt、评测结果、"
                 f"接受理由、成本、seed 快照均在其中）")
    lines.append("")

    lines.append("## 一、baseline vs candidate 概览")
    lines.append("")
    lines.append("| 切分 | baseline 通过率 | candidate 通过率 | baseline 平均分 | candidate 平均分 | 通过率 Δ |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for split_name, b, c, d in (
        ("train", baseline_train, candidate_train, delta_train),
        ("val", baseline_val, candidate_val, delta_val),
    ):
        lines.append(f"| {split_name} | {_pct(b['pass_rate'])} ({b['passed']}/{b['total']}) "
                     f"| {_pct(c['pass_rate'])} ({c['passed']}/{c['total']}) "
                     f"| {b['mean_score']:.3f} | {c['mean_score']:.3f} "
                     f"| {d['pass_rate_delta']:+.3f} |")
    lines.append("")

    lines.append("## 二、baseline 失败归因统计")
    lines.append("")
    counts = report["attribution"]["counts_by_type"]
    if counts:
        lines.append("| 失败类型 | 中文说明 | 涉及 case 数 |")
        lines.append("| --- | --- | --- |")
        for failure_type, count in counts.items():
            lines.append(f"| `{failure_type}` | {FAILURE_TYPE_LABELS_ZH.get(failure_type, failure_type)} | {count} |")
        lines.append("")
        lines.append("主要归因（每个失败 case 的根因）：")
        for eval_id, primary in sorted(report["attribution"]["primary_by_case"].items()):
            lines.append(f"- `{eval_id}` → `{primary}`（{FAILURE_TYPE_LABELS_ZH.get(primary, primary)}）")
    else:
        lines.append("baseline 无失败 case。")
    lines.append("")

    lines.append("## 三、逐 case delta（验证集为准，训练集附后）")
    lines.append("")
    for split_name, delta in (("val", delta_val), ("train", delta_train)):
        lines.append(f"### {split_name}")
        lines.append("")
        lines.append("| case | baseline | candidate | 分数变化 | 判定 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for case in delta["per_case"]:
            lines.append(f"| `{case['eval_id']}` "
                         f"| {_status_icon(case['baseline_passed'])} {case['baseline_score']:.3f} "
                         f"| {_status_icon(case['candidate_passed'])} {case['candidate_score']:.3f} "
                         f"| {case['candidate_score'] - case['baseline_score']:+.3f} "
                         f"| {CHANGE_LABELS_ZH.get(case['change'], case['change'])}（`{case['change']}`） |")
        lines.append("")

    lines.append("## 四、优化过程（优化器视角）")
    lines.append("")
    opt_view = optimization["optimizer_val_pass_rate"]
    lines.append(f"- 优化器内部验证集通过率：{_pct(opt_view['baseline'])} → {_pct(opt_view['best'])}"
                 f"（注意：优化器只看 optimizer.json 里的弱指标；overfit 场景中它看到的还是"
                 f"泄漏调参集 —— 是否真的变好以上面的独立验证集复评为准）")
    cost = optimization["cost"]
    lines.append(f"- 成本：${cost['total_llm_cost']:.4f}，反思 LM 调用 {cost['reflection_lm_calls']} 次，"
                 f"metric 调用 {cost['budget_used']}/{cost['budget_total']}")
    lines.append("")

    lines.append("## 五、gate 决策明细")
    lines.append("")
    lines.append("| 闸门 | 结果 | 说明 |")
    lines.append("| --- | --- | --- |")
    for gate in decision["gates"]:
        lines.append(f"| `{gate['name']}` | {_status_icon(gate['passed'])} | {gate['detail']} |")
    lines.append("")

    lines.append("## 六、是否值得接受")
    lines.append("")
    if decision["accepted"]:
        lines.append("候选 prompt 在独立验证集上带来实际提升，且未引入任何回归："
                     "无新增失败、保护 case 完好、成本与耗时都在预算内。"
                     "**建议接受**，可用 `--apply` 将最优候选写回源 prompt 文件。")
    else:
        lines.append(f"候选 prompt 未能通过接受策略：{decision['reason']} "
                     "**建议拒绝**，保持 baseline prompt 不变；"
                     "可根据上面的失败归因调整评测集或优化配置后重试。")
    lines.append("")
    return "\n".join(lines)


def write_reports(output_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    """落盘 optimization_report.json / optimization_report.md。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "optimization_report.json"
    md_path = output_dir / "optimization_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
