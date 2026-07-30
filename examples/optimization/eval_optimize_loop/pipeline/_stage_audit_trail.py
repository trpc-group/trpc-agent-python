"""Stage 6: 审计轨迹 — 生成 optimization_report.json 和 optimization_report.md。

本阶段是流水线的终点，将 PipelineReport 导出为两种格式：
  - JSON 格式：机器可解析，适合 diff 对比和自动化分析
  - Markdown 格式：人类可读，适合非技术干系人查看

两份报告包含相同的完整信息：
  - 基线评测结果（训练集 + 验证集，逐 case 逐 metric）
  - 失败归因聚类
  - 优化执行记录（算法、轮次、候选 prompt）
  - 候选验证评分和逐 case delta
  - 门控决策及所有检查项
  - 运行时间和成本统计

调用方式：
    json_path = ReportGenerator.generate_json(report, "output/")
    md_path = ReportGenerator.generate_markdown(report, "output/")
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from pipeline._models import PipelineReport


class ReportGenerator:
    """报告生成器：将 PipelineReport 导出为 JSON 和 Markdown 文件。

    所有报告写入指定的 output_dir 目录，确保每次运行完全可复现。
    """

    @staticmethod
    def generate_json(report: PipelineReport, output_dir: str) -> str:
        """生成 optimization_report.json。

        Args:
            report: 完整的 PipelineReport。
            output_dir: 输出目录路径。

        Returns:
            写入的 JSON 文件完整路径。
        """
        path = os.path.join(output_dir, "optimization_report.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))
        return path

    @staticmethod
    def generate_markdown(report: PipelineReport, output_dir: str) -> str:
        """生成 optimization_report.md。

        Args:
            report: 完整的 PipelineReport。
            output_dir: 输出目录路径。

        Returns:
            写入的 Markdown 文件完整路径。
        """
        path = os.path.join(output_dir, "optimization_report.md")
        content = ReportGenerator._render_markdown(report)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    @staticmethod
    def _render_markdown(report: PipelineReport) -> str:
        """渲染完整的 Markdown 报告内容。

        按 6 个阶段组织内容，使用表格和列表呈现数据。

        Args:
            report: 完整的 PipelineReport。

        Returns:
            Markdown 格式的报告字符串。
        """
        lines = [
            "# 优化报告",
            "",
            f"**生成时间**: {report.timestamp}",
            f"**流水线版本**: {report.pipeline_version}",
            f"**模式**: {'Demo (trace 模式)' if report.demo_mode else 'Real (LLM)'}",
            f"**耗时**: {report.pipeline_duration_seconds:.2f}s",
            "",
            "---",
            "",
            "## 总体判决",
            "",
        ]

        # 总体判决（ACCEPTED / REJECTED）
        verdict = report.overall_verdict
        if verdict == "ACCEPTED":
            lines.append(f"**判决: 接受 (ACCEPTED)** — 候选 prompt 建议用于生产环境。")
        elif verdict == "REJECTED":
            lines.append(f"**判决: 拒绝 (REJECTED)** — 候选 prompt 不应采用。")
        else:
            lines.append(f"**判决: {verdict}**")

        if report.gate_decision:
            lines.append(f"**原因**: {report.gate_decision.reason}")
            lines.append(f"**Pass Rate 变化**: {report.overall_pass_rate_change:+.4f}")

        lines.extend([
            "",
            "---",
            "",
            "## 1. 基线评测",
            "",
        ])

        # Stage 1: 基线评测结果
        if report.baseline_train:
            lines.extend(ReportGenerator._render_eval_set("训练集", report.baseline_train))
        if report.baseline_val:
            lines.extend(ReportGenerator._render_eval_set("验证集", report.baseline_val))

        lines.extend([
            "---",
            "",
            "## 2. 失败归因",
            "",
        ])

        # Stage 2: 失败归因
        if report.failure_attribution:
            fa = report.failure_attribution
            lines.append(f"**总 Case 数**: {fa.total_cases_evaluated}")
            lines.append(f"**失败 Case 数**: {fa.total_failed}")
            lines.append(f"**摘要**: {fa.summary}")
            lines.append("")
            if fa.clusters:
                lines.append("| 失败类别 | 数量 | Case ID |")
                lines.append("|---------|------|--------|")
                for category, case_ids in sorted(fa.clusters.items()):
                    lines.append(f"| {category} | {len(case_ids)} | {', '.join(case_ids)} |")
            lines.append("")

            if fa.per_case_categories:
                lines.append("### 逐 Case 归因")
                lines.append("")
                for eval_id, categories in sorted(fa.per_case_categories.items()):
                    if categories:
                        lines.append(f"- **{eval_id}**: {', '.join(categories)}")
                lines.append("")

        lines.extend([
            "---",
            "",
            "## 3. 优化执行",
            "",
        ])

        # Stage 3: 优化执行
        if report.optimization_execution:
            oe = report.optimization_execution
            lines.append(f"| 字段 | 值 |")
            lines.append(f"|------|----|")
            lines.append(f"| 算法 | {oe.algorithm} |")
            lines.append(f"| 状态 | {oe.status} |")
            lines.append(f"| 总轮次 | {oe.total_rounds} |")
            lines.append(f"| 基线 Pass Rate | {oe.baseline_pass_rate:.4f} |")
            lines.append(f"| 最优 Pass Rate | {oe.best_pass_rate:.4f} |")
            lines.append(f"| 提升 | {oe.pass_rate_improvement:+.4f} |")
            lines.append(f"| 耗时 | {oe.duration_seconds:.2f}s |")
            lines.append(f"| 成本 | ${oe.total_llm_cost:.4f} |")
            lines.append("")

            if oe.best_prompts:
                lines.append("### 优化后 Prompt")
                lines.append("")
                for name, content in oe.best_prompts.items():
                    lines.append(f"**{name}**:")
                    lines.append("```")
                    lines.append(content.strip())
                    lines.append("```")
                    lines.append("")

        lines.extend([
            "---",
            "",
            "## 4. 候选验证",
            "",
        ])

        # Stage 4: 候选验证
        if report.candidate_validation:
            lines.extend(ReportGenerator._render_eval_set("候选验证", report.candidate_validation))

        lines.extend([
            "---",
            "",
            "## 5. 逐 Case Delta",
            "",
        ])

        # Stage 4: 逐 case delta 表
        if report.case_deltas:
            lines.append("| Eval ID | 场景 | 基线 | 候选 | 转换 | 分数变化 |")
            lines.append("|---------|------|------|------|------|---------|")
            for delta in report.case_deltas:
                score_str = ", ".join(
                    f"{k}: {v:+.2f}" for k, v in delta.score_delta.items()
                ) if delta.score_delta else "-"
                lines.append(
                    f"| {delta.eval_id} | {delta.scenario} | {delta.baseline_status} "
                    f"| {delta.candidate_status} | {delta.transition} | {score_str} |"
                )
            lines.append("")

        lines.extend([
            "---",
            "",
            "## 6. 接受门控决策",
            "",
        ])

        # Stage 5: 门控决策
        if report.gate_decision:
            gd = report.gate_decision
            lines.append(f"**决定**: {'接受 (ACCEPTED)' if gd.accepted else '拒绝 (REJECTED)'}")
            lines.append(f"**原因**: {gd.reason}")
            lines.append(f"**基线 Pass Rate**: {gd.baseline_pass_rate:.4f}")
            lines.append(f"**候选 Pass Rate**: {gd.candidate_pass_rate:.4f}")
            lines.append(f"**改善**: {gd.improvement:+.4f}")
            lines.append("")

            lines.append("### 门控检查项")
            lines.append("")
            lines.append("| 检查项 | 结果 | 详情 |")
            lines.append("|--------|------|------|")
            for check in gd.checks:
                icon = "通过 (PASS)" if check.passed else "失败 (FAIL)"
                lines.append(f"| {check.check_name} | {icon} | {check.detail} |")
            lines.append("")

            if gd.regressed_case_ids:
                lines.append(f"**退化的 Case**: {', '.join(gd.regressed_case_ids)}")
                lines.append("")

        lines.extend([
            "---",
            "",
            "## 附录: 可复现性",
            "",
            f"- **时间戳**: {report.timestamp}",
            f"- **流水线版本**: {report.pipeline_version}",
            f"- **Demo 模式**: {report.demo_mode}",
        ])

        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_eval_set(title: str, report) -> list[str]:
        """渲染单个评测集报告为 Markdown 表格。

        包含评测集概览、metric 平均分表格和逐 case 评分表格。

        Args:
            title: 评测集标题（如 "训练集"）。
            report: EvalSetReport 实例。

        Returns:
            Markdown 行列表。
        """
        from pipeline._models import EvalSetReport

        lines = [
            f"### {title}",
            "",
            f"- **评测集**: {report.eval_set_id}",
            f"- **Case 数**: {report.num_cases} 总计（{report.num_passed} 通过, {report.num_failed} 失败）",
            f"- **Pass Rate**: {report.pass_rate:.4f}",
            "",
        ]

        if report.metric_breakdown:
            lines.append("| Metric | 平均分 |")
            lines.append("|--------|--------|")
            for name, score in report.metric_breakdown.items():
                lines.append(f"| {name} | {score:.4f} |")
            lines.append("")

        if report.per_case:
            lines.append("| Eval ID | 状态 | 分数 |")
            lines.append("|---------|------|------|")
            for case in report.per_case:
                score_str = ", ".join(
                    f"{k}: {v:.2f}" for k, v in case.metric_scores.items()
                ) if case.metric_scores else "-"
                lines.append(f"| {case.eval_id} | {case.overall_status} | {score_str} |")
            lines.append("")

        return lines
