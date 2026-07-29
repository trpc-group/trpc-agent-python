#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Build machine-readable and human-readable optimization reports."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import attribution
import gates
from runner import FrozenInputs, RunArtifact, SplitResult


def _split_to_dict(split: SplitResult) -> dict[str, float]:
    return {
        "champion_avg": round(split.champion_avg, 6),
        "challenger_avg": round(split.challenger_avg, 6),
        "delta": round(split.delta, 6),
    }


def build_report_dict(
    artifact: RunArtifact,
    decision: gates.Decision,
    *,
    applied: bool,
    before_apply_sha256: str,
    after_apply_sha256: Optional[str],
    repro_cmd: str,
) -> dict[str, Any]:
    """Build the complete report from real case-level evaluator evidence."""

    per_case: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    transition_counts: dict[str, int] = {}
    for case in artifact.cases:
        result = attribution.from_case_record(case)
        category_counts[result.category] = category_counts.get(result.category, 0) + 1
        transition_counts[case.transition] = transition_counts.get(case.transition, 0) + 1
        per_case.append(
            {
                "eval_id": case.eval_id,
                "split": case.split,
                "slice": case.slice_name,
                "risk_level": case.risk_level,
                "protected": case.protected,
                "scenario_tag": case.scenario_tag,
                "champion_status": case.champion_status,
                "challenger_status": case.challenger_status,
                "champion_score": round(case.champion_score, 6),
                "challenger_score": round(case.challenger_score, 6),
                "delta": round(case.delta, 6),
                "transition": case.transition,
                "failure_kind": case.failure_kind,
                "category": result.category,
                "failure_reason": result.reason,
                "runs": 1,
                "evidence": {
                    "actual_text": case.actual_text,
                    "expected_text": case.expected_text,
                    "metric_results": case.metric_results,
                    "actual_tool_uses": case.actual_tool_uses,
                    "expected_tool_uses": case.expected_tool_uses,
                    "actual_tool_responses": case.actual_tool_responses,
                    "expected_tool_responses": case.expected_tool_responses,
                    "error_message": case.error_message,
                    "metric_reasons": case.failure_reasons,
                    "trace_ref": case.trace_ref,
                    "attribution": result.evidence,
                },
            }
        )

    frozen = asdict(artifact.frozen)
    return {
        "version": "2.0",
        "candidate_source": artifact.frozen.candidate_source,
        "frozen": frozen,
        "results": {
            "train": _split_to_dict(artifact.train),
            "val": _split_to_dict(artifact.val),
        },
        "train_delta": round(artifact.train.delta, 6),
        "val_delta": round(artifact.val.delta, 6),
        "per_case": per_case,
        "transition_counts": transition_counts,
        "fail_category_counts": category_counts,
        "decision": {
            "accepted": decision.accepted,
            "violated": decision.violated,
            "reasons": decision.reasons,
        },
        "cost_status": artifact.cost_status,
        "cost": {
            "status": artifact.cost_status,
            "total_tokens": artifact.total_tokens,
            "total_usd": artifact.total_cost,
        },
        "optimizer": {
            "info": artifact.frozen.optimizer_info,
            "rounds": artifact.optimizer_rounds,
            "artifacts": artifact.optimizer_artifacts,
        },
        "audit": {
            "run_id": artifact.frozen.run_id,
            "applied": applied,
            "duration_seconds": round(artifact.duration_seconds, 4),
            "candidate_source": artifact.frozen.candidate_source,
            "scenario": artifact.frozen.scenario,
            "cost_status": artifact.cost_status,
            "cost": artifact.total_cost,
            "artifact_dir": str(artifact.artifact_dir),
            "artifacts": {
                "frozen_json": str(artifact.artifact_dir / "frozen.json"),
                "champion_prompts": str(artifact.artifact_dir / "champion_prompts" / "system.md"),
                "challenger_prompts": str(artifact.artifact_dir / "challenger_prompts" / "system.md"),
                "train_eval_log": str(artifact.artifact_dir / "train_eval.json"),
                "val_eval_log": str(artifact.artifact_dir / "val_eval.json"),
                **artifact.optimizer_artifacts,
            },
            "before_apply_sha256": before_apply_sha256,
            "after_apply_sha256": after_apply_sha256,
            "repro_cmd": repro_cmd,
        },
        "limitations": {
            "hidden_sample_accuracy": (
                "未使用官方隐藏集，不声称满足隐藏样本准确率；" "公开 fake/标注 fixture 仅证明可复现管线行为。"
            )
        },
    }


def build_optimizer_failure_report(
    *,
    frozen: FrozenInputs,
    artifact_dir: Path,
    error: BaseException,
    repro_cmd: str,
    optimizer_artifacts: dict[str, str],
) -> dict[str, Any]:
    """Persist an auditable REJECT when optimization cannot produce a candidate."""

    reason = f"{type(error).__name__}: {error}"
    return {
        "version": "2.0",
        "candidate_source": "agent_optimizer",
        "frozen": asdict(frozen),
        "results": {"train": None, "val": None},
        "train_delta": None,
        "val_delta": None,
        "per_case": [],
        "transition_counts": {},
        "fail_category_counts": {"infrastructure_failure": 1},
        "decision": {
            "accepted": False,
            "violated": ["OPTIMIZER_FAILURE", "G6"],
            "reasons": [
                f"优化器未产生可验证 Candidate：{reason}",
                "成本或优化证据不完整，禁止自动 ACCEPT/--apply。",
            ],
        },
        "cost_status": "unavailable",
        "cost": {"status": "unavailable", "total_tokens": None, "total_usd": None},
        "optimizer": {
            "info": frozen.optimizer_info,
            "rounds": [],
            "artifacts": optimizer_artifacts,
            "error": reason,
        },
        "audit": {
            "run_id": frozen.run_id,
            "applied": False,
            "duration_seconds": 0.0,
            "candidate_source": "agent_optimizer",
            "scenario": None,
            "cost_status": "unavailable",
            "cost": None,
            "artifact_dir": str(artifact_dir),
            "artifacts": {
                "frozen_json": str(artifact_dir / "frozen.json"),
                **optimizer_artifacts,
            },
            "before_apply_sha256": frozen.champion_sha256,
            "after_apply_sha256": None,
            "repro_cmd": repro_cmd,
        },
        "limitations": {"hidden_sample_accuracy": "本次优化失败，未产生可用于隐藏样本验证的 Candidate。"},
    }


def render_markdown(report: dict[str, Any]) -> str:
    decision = report["decision"]
    verdict = "ACCEPT" if decision["accepted"] else "REJECT"
    lines = [
        "# Evaluation + Optimization 报告",
        "",
        f"- **决策**: `{verdict}`",
        f"- **运行 ID**: `{report['audit']['run_id']}`",
        f"- **候选来源**: `{report['candidate_source']}`",
        f"- **运行模式**: `{report['frozen']['mode']}`",
        f"- **是否写回**: `{report['audit']['applied']}`",
        "",
        "## 聚合分数",
        "",
    ]
    if report["results"]["train"] is None:
        lines.append("优化器未产生 Candidate，未执行 Champion/Challenger 回归比较。")
    else:
        lines.extend(
            [
                "| split | baseline | candidate | delta |",
                "|---|---:|---:|---:|",
            ]
        )
        for split in ("train", "val"):
            values = report["results"][split]
            lines.append(
                f"| {split} | {values['champion_avg']:.4f} | "
                f"{values['challenger_avg']:.4f} | {values['delta']:+.4f} |"
            )

    lines.extend(["", "## Gate 决策", ""])
    if decision["violated"]:
        lines.append(f"违反的 gate：`{', '.join(decision['violated'])}`")
    else:
        lines.append("全部 gate 通过。")
    lines.extend(f"- {reason}" for reason in decision["reasons"])

    lines.extend(["", "## 失败归因统计", "", "| category | count |", "|---|---:|"])
    for category, count in sorted(report["fail_category_counts"].items()):
        lines.append(f"| {category} | {count} |")

    lines.extend(
        [
            "",
            "## 逐 case 明细",
            "",
            "| eval_id | split | transition | baseline | candidate | delta | category | reason |",
            "|---|---|---|---:|---:|---:|---|---|",
        ]
    )
    for case in report["per_case"]:
        reason = str(case["failure_reason"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {case['eval_id']} | {case['split']} | {case['transition']} | "
            f"{case['champion_score']:.2f} | {case['challenger_score']:.2f} | "
            f"{case['delta']:+.2f} | {case['category']} | {reason} |"
        )

    cost = report["cost"]
    lines.extend(
        [
            "",
            "## 成本与优化器产物",
            "",
            f"- cost_status: `{cost['status']}`",
            f"- total_tokens: `{cost['total_tokens']}`",
            f"- total_usd: `{cost['total_usd']}`",
            f"- optimizer rounds: `{len(report['optimizer']['rounds'])}`",
            "",
            "## 审计",
            "",
            f"- artifact_dir: `{report['audit']['artifact_dir']}`",
            f"- before_apply_sha256: `{report['audit']['before_apply_sha256']}`",
            f"- after_apply_sha256: `{report['audit']['after_apply_sha256']}`",
            f"- repro_cmd: `{report['audit']['repro_cmd']}`",
            "",
            "## 限制",
            "",
            f"- {report['limitations']['hidden_sample_accuracy']}",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(report: dict[str, Any], *, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "optimization_report.json"
    markdown_path = out_dir / "optimization_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
