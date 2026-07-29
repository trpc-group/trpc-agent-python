# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Report writing for the eval-optimize-loop example."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import BaselineOptimizationReport
from .types import ReportPaths

REPORT_FILENAME = "optimization_report.json"
MARKDOWN_REPORT_FILENAME = "optimization_report.md"


def write_optimization_report(report: BaselineOptimizationReport, output_dir: Path) -> ReportPaths:
    """Persist machine-readable and human-readable optimization reports."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dict = report.to_dict()
    report_path = output_dir / REPORT_FILENAME
    markdown_path = output_dir / MARKDOWN_REPORT_FILENAME
    _atomic_write_text(report_path, json.dumps(report_dict, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(markdown_path, render_markdown_report(report_dict))
    return ReportPaths(json_path=report_path, markdown_path=markdown_path)


def render_markdown_report(report: dict[str, Any]) -> str:
    gate = report["gate_decision"]
    delta = report["delta"]
    optimization = report["optimization"]
    baseline = report["baseline"]
    candidate = report["candidate"]
    failure_attribution = report["failure_attribution"]
    gate_config = report["config"]["gate"]
    metadata = report["metadata"]
    lines = [
        "# Evaluation Optimization Pipeline 报告",
        "",
        "## 运行摘要",
        "",
        f"- 决策：**{gate['decision'].upper()}**",
        f"- 推荐动作：`{gate['recommended_action']}`",
        f"- 模式：`{report['run']['mode']}`",
        f"- Schema：`{report['schema_version']}`",
        f"- 验证集分数变化：{_fmt(delta['val']['score_delta'])}",
        f"- 验证集通过率变化：{_fmt(delta['val']['pass_rate_delta'])}",
        f"- 主要原因：{gate['reason']}",
        "",
        "## Baseline 表现",
        "",
        _split_summary("训练集", baseline["train"]),
        _split_summary("验证集", baseline["val"]),
        "",
        "## 错误归因汇总",
        "",
        _failure_table("训练集 baseline", failure_attribution["train_summary"]["baseline"]),
        "",
        _failure_table("训练集 candidate", failure_attribution["train_summary"]["candidate"]),
        "",
        _failure_table("验证集 baseline", failure_attribution["val_summary"]["baseline"]),
        "",
        _failure_table("验证集 candidate", failure_attribution["val_summary"]["candidate"]),
        "",
        "## 优化过程",
        "",
        f"- 目标 Prompt：{', '.join(optimization['target_prompt_names'])}",
        f"- 优化轮数：{optimization['total_rounds']}",
        f"- 优化成本：{optimization['total_cost']}",
        f"- 随机种子：{optimization['seed']}",
    ]
    for round_record in optimization["rounds"]:
        lines.extend([
            "",
            f"### Round {round_record['round']}",
            "",
            f"- 修改字段：{', '.join(round_record['optimized_field_names'])}",
            f"- 是否接受为候选：{round_record['accepted']}",
            f"- 原因：{round_record['reason']}",
        ])
    lines.extend([
        "",
        "## 候选验证",
        "",
        _split_summary("训练集候选", candidate["train"]),
        _split_summary("验证集候选", candidate["val"]),
        "",
        "## 逐 Case Delta",
        "",
        "| split | case id | baseline | candidate | change | failure transition | regression | improvement |",
        "| --- | --- | ---: | ---: | --- | --- | --- | --- |",
    ])
    for split_name in ("train", "val"):
        for case_delta in delta[split_name]["case_deltas"]:
            lines.append(_case_delta_row(case_delta))
    lines.extend([
        "",
        "## Gate 决策",
        "",
        f"- 最终决策：**{gate['decision'].upper()}**",
        f"- 推荐动作：`{gate['recommended_action']}`",
        f"- 拒绝/接受理由：{gate['reason']}",
        f"- 最小验证集分数提升：{gate_config['min_val_score_gain']}",
        f"- 允许新增失败：{gate_config['allow_new_failures']}",
        f"- 允许验证集回归：{gate_config['allow_regressions']}",
        f"- 关键 Case：{', '.join(gate_config['critical_case_ids']) or '无'}",
        "",
        "| rule | passed | severity | message |",
        "| --- | --- | --- | --- |",
    ])
    for rule in gate["rule_results"]:
        lines.append(f"| {rule['rule_name']} | {rule['passed']} | {rule['severity']} | {rule['message']} |")
    lines.extend([
        "",
        "## 元数据与复现",
        "",
        f"- 示例根目录：`{metadata['example_root']}`",
        f"- 复现命令：`{metadata['reproduction_command']}`",
        f"- 输出 JSON：`{metadata['output_paths']['json']}`",
        f"- 输出 Markdown：`{metadata['output_paths']['markdown']}`",
        "",
        "| key | value |",
        "| --- | --- |",
        f"| example_root | {metadata['example_root']} |",
        f"| output_dir | {metadata['output_dir']} |",
    ])
    lines.append("")
    return "\n".join(lines)


def _atomic_write_text(path: Path, content: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _split_summary(label: str, split: dict[str, Any]) -> str:
    pass_rate = split["passed_count"] / split["case_count"] if split["case_count"] else 0.0
    return f"- {label}：{split['passed_count']}/{split['case_count']} 通过，通过率 {_fmt(pass_rate)}"


def _failure_table(label: str, summary: dict[str, int]) -> str:
    lines = [f"### {label}", "", "| category | count |", "| --- | ---: |"]
    for category, count in summary.items():
        lines.append(f"| {category} | {count} |")
    return "\n".join(lines)


def _case_delta_row(case_delta: dict[str, Any]) -> str:
    failure_transition = "{} -> {}".format(
        case_delta["baseline"].get("failure_category"),
        case_delta["candidate"].get("failure_category"),
    )
    return (f"| {case_delta['split']} | {case_delta['id']} | {_fmt(case_delta['baseline'].get('metric_score'))} | "
            f"{_fmt(case_delta['candidate'].get('metric_score'))} | {case_delta['change_type']} | "
            f"{failure_transition} | {case_delta['regression']} | {case_delta['improvement']} |")


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
