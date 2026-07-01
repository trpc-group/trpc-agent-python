# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""审计阶段：把闭环全过程落成结构化 JSON + 人可读 Markdown。

产物
----
- ``optimization_report.json`` : baseline / candidate / 逐 case delta /
  gate 决策 / 失败归因统计 / 成本 / 耗时 / 复现实验配置（seed、mode、数据路径）。
- ``optimization_report.md``   : 用人能读懂的方式说明是否值得接受。
"""

from __future__ import annotations

import json
from pathlib import Path

from .evaluate import SetEval
from .gate import CaseDelta, GateDecision
from .optimize import CandidateResult


def _set_to_dict(s: SetEval) -> dict:
    return {
        "set_id": s.set_id,
        "pass_count": s.pass_count,
        "total": s.total,
        "avg_score": round(s.avg_score, 4),
        "cases": {
            eid: {
                "passed": c.passed,
                "score": round(c.score, 4),
                "expected": c.expected_text,
                "actual": c.actual_text,
                "error": c.error,
                # 每条 case 的 metric 明细（分/阈值/pass-fail/失败原因）
                "metrics": [
                    {
                        "name": m.name,
                        "score": round(m.score, 4),
                        "passed": m.passed,
                        "threshold": m.threshold,
                        "reason": m.reason,
                    }
                    for m in c.metrics
                ],
                "trajectory": c.trajectory,  # 关键轨迹
            }
            for eid, c in s.cases.items()
        },
    }


def _attrib_to_dict(attrib: dict) -> dict:
    return {
        "clusters": attrib["clusters"],
        "cases": {
            eid: {
                "category": a.category,
                "category_label": a.category_label,
                "reason": a.reason,
                "source": a.source,
            }
            for eid, a in attrib["attributions"].items()
        },
    }


def build_report(
    *,
    run_meta: dict,
    baseline_train: SetEval,
    baseline_val: SetEval,
    train_attrib: dict,
    val_attrib: dict,
    candidate: CandidateResult,
    candidate_train: SetEval,
    candidate_val: SetEval,
    deltas: list[CaseDelta],
    train_deltas: list[CaseDelta],
    gate: GateDecision,
) -> dict:
    """汇总为一个可直接 json.dump 的报告字典。"""
    return {
        "schema_version": "eol-v1",
        "run": run_meta,
        "baseline": {
            "train": _set_to_dict(baseline_train),
            "val": _set_to_dict(baseline_val),
        },
        "failure_attribution": {
            "train": _attrib_to_dict(train_attrib),
            "val": _attrib_to_dict(val_attrib),
        },
        "candidate": {
            "status": candidate.status,
            "stop_reason": candidate.stop_reason,
            "optimized_fields": candidate.optimized_fields,
            "rounds": candidate.rounds,
            "cost_usd": round(candidate.cost_usd, 6),
            "duration_seconds": round(candidate.duration_seconds, 4),
            "meta": candidate.meta,
            "prompts": candidate.prompts,
            "rounds_detail": candidate.rounds_detail,  # 每轮候选 prompt 审计
        },
        "candidate_train": _set_to_dict(candidate_train),
        "candidate_val": _set_to_dict(candidate_val),
        "overfitting_signal": {
            "train_score_delta": round(candidate_train.avg_score - baseline_train.avg_score, 4),
            "val_score_delta": gate.val_score_delta,
            "train_up_val_down": (
                candidate_train.avg_score > baseline_train.avg_score
                and gate.val_score_delta <= 0
            ),
        },
        "delta": {
            "val_score_delta": gate.val_score_delta,
            "train_score_delta": round(candidate_train.avg_score - baseline_train.avg_score, 4),
            "baseline_val_pass": f"{baseline_val.pass_count}/{baseline_val.total}",
            "candidate_val_pass": f"{candidate_val.pass_count}/{candidate_val.total}",
            "baseline_train_pass": f"{baseline_train.pass_count}/{baseline_train.total}",
            "candidate_train_pass": f"{candidate_train.pass_count}/{candidate_train.total}",
            "per_case": [
                {
                    "eval_id": d.eval_id,
                    "baseline_passed": d.baseline_passed,
                    "candidate_passed": d.candidate_passed,
                    "baseline_score": round(d.baseline_score, 4),
                    "candidate_score": round(d.candidate_score, 4),
                    "score_delta": d.score_delta,
                    "status": d.status,
                }
                for d in deltas
            ],
            "newly_passed": gate.newly_passed,
            "newly_failed": gate.newly_failed,
            "regressed": gate.regressed,
        },
        "gate_decision": {
            "accepted": gate.accepted,
            "summary": gate.summary,
            "rules": [
                {"name": r.name, "passed": r.passed, "detail": r.detail}
                for r in gate.rules
            ],
        },
    }


def save_json(report: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _status_emoji(status: str) -> str:
    return {
        "newly_passed": "🟢 新增通过",
        "newly_failed": "🔴 新增失败",
        "improved": "🔼 分数提升",
        "regressed": "🔻 分数下降",
        "unchanged": "⚪ 不变",
    }.get(status, status)


def render_markdown(report: dict) -> str:
    r = report
    run = r["run"]
    gate = r["gate_decision"]
    delta = r["delta"]
    decision = "✅ 接受 (ACCEPT)" if gate["accepted"] else "❌ 拒绝 (REJECT)"

    lines: list[str] = []
    lines.append("# Evaluation + Optimization 闭环报告")
    lines.append("")
    lines.append(f"- **决策**：{decision}")
    lines.append(f"- **结论**：{gate['summary']}")
    lines.append(f"- 运行模式：`{run['mode']}` ｜ seed：`{run['seed']}` ｜ 耗时：{run['elapsed_seconds']}s")
    lines.append(f"- 时间：{run['started_at']} → {run['finished_at']}")
    lines.append("")

    # 分数总览
    bt, bv = r["baseline"]["train"], r["baseline"]["val"]
    ct, cv = r["candidate_train"], r["candidate_val"]
    ovf = r["overfitting_signal"]
    lines.append("## 1. 分数总览")
    lines.append("")
    lines.append("| 数据集 | baseline 通过 | baseline 均分 | candidate 通过 | candidate 均分 | Δ均分 |")
    lines.append("|---|---|---|---|---|---|")
    lines.append(
        f"| 训练集 | {bt['pass_count']}/{bt['total']} | {bt['avg_score']:.3f} "
        f"| {ct['pass_count']}/{ct['total']} | {ct['avg_score']:.3f} | {ovf['train_score_delta']:+.3f} |"
    )
    lines.append(
        f"| 验证集 | {bv['pass_count']}/{bv['total']} | {bv['avg_score']:.3f} "
        f"| {cv['pass_count']}/{cv['total']} | {cv['avg_score']:.3f} | {ovf['val_score_delta']:+.3f} |"
    )
    lines.append("")
    if ovf["train_up_val_down"]:
        lines.append(
            f"> ⚠️ **过拟合信号**：训练集提升 {ovf['train_score_delta']:+.3f}，"
            f"验证集却未提升（{ovf['val_score_delta']:+.3f}）——候选在训练分布上过度特化。"
        )
        lines.append("")

    # 失败归因
    lines.append("## 2. Baseline 失败归因")
    lines.append("")
    for split in ("train", "val"):
        fa = r["failure_attribution"][split]
        base_cases = r["baseline"][split]["cases"]
        lines.append(f"**{'训练集' if split == 'train' else '验证集'}** 失败聚类：{fa['clusters'] or '无失败'}")
        for eid, a in fa["cases"].items():
            lines.append(f"- `{eid}` → **{a['category_label']}**（{a['source']}）：{a['reason']}")
            traj = base_cases.get(eid, {}).get("trajectory", [])
            if traj:
                lines.append(f"  - 关键轨迹：{' → '.join(traj)}")
        lines.append("")

    # 逐 case delta
    lines.append("## 3. 候选验证 · 逐 case delta")
    lines.append("")
    lines.append("| case | baseline | candidate | Δ分 | 状态 |")
    lines.append("|---|---|---|---|---|")
    for d in delta["per_case"]:
        lines.append(
            f"| `{d['eval_id']}` | {'PASS' if d['baseline_passed'] else 'FAIL'} "
            f"| {'PASS' if d['candidate_passed'] else 'FAIL'} "
            f"| {d['score_delta']:+.3f} | {_status_emoji(d['status'])} |"
        )
    lines.append("")
    lines.append(f"- 新增通过：{delta['newly_passed'] or '无'}")
    lines.append(f"- 新增失败：{delta['newly_failed'] or '无'}")
    lines.append("")

    # gate 明细
    lines.append("## 4. 门控决策明细")
    lines.append("")
    lines.append("| 规则 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    for rule in gate["rules"]:
        lines.append(f"| `{rule['name']}` | {'✅' if rule['passed'] else '❌'} | {rule['detail']} |")
    lines.append("")

    # 每轮候选审计
    cand = r["candidate"]
    lines.append("## 5. 每轮候选审计")
    lines.append("")
    if cand["rounds_detail"]:
        for rd in cand["rounds_detail"]:
            fields = rd.get("optimized_fields", [])
            extra = ""
            if "validation_pass_rate" in rd:
                extra = f" ｜ val_pass={rd['validation_pass_rate']} ｜ accepted={rd.get('accepted')}"
            lines.append(f"- **Round {rd['round']}**：改写字段 {fields}{extra}")
            note = rd.get("note") or rd.get("acceptance_reason")
            if note:
                lines.append(f"  - {note}")
    else:
        lines.append("- （优化器未产出任何轮次；候选=baseline）")
    lines.append("")
    lines.append("> 每轮候选 prompt 全文见 `optimization_report.json` 的 `candidate.rounds_detail`。")
    lines.append("")

    # 候选与成本
    lines.append("## 6. 候选与成本审计")
    lines.append("")
    lines.append(f"- 优化状态：`{cand['status']}` ｜ stop_reason：`{cand['stop_reason']}`")
    lines.append(f"- 被改写字段：{cand['optimized_fields']} ｜ 轮数：{cand['rounds']}")
    lines.append(f"- 成本：${cand['cost_usd']:.6f} ｜ 优化耗时：{cand['duration_seconds']}s")
    lines.append(f"- 后端：`{cand['meta'].get('backend', 'unknown')}`")
    lines.append("")
    lines.append("> 候选 prompt 全文与逐 case 明细见 `optimization_report.json`。")
    lines.append("")
    return "\n".join(lines)


def save_markdown(report: dict, path: Path) -> None:
    Path(path).write_text(render_markdown(report), encoding="utf-8")
