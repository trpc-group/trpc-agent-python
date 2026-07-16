# report.py — 报告生成（JSON/MD/SARIF）
import json
from datetime import datetime
from dataclasses import asdict
from pathlib import Path
from typing import Any
from copy import deepcopy

from agent.models import ReviewReport, Severity
from agent.redaction import redact_text


def _redact_report(report: ReviewReport) -> dict[str, Any]:
    """对报告数据进行深度脱敏（Critical 1 修复）

    确保 outputs 文件零明文密钥，对所有可能含密文的字段脱敏：
    - sandbox_runs.stdout/stderr
    - filter_decisions.command/reason
    - findings.evidence/title/recommendation

    Args:
        report: 原始审查报告

    Returns:
        脱敏后的报告字典（深拷贝，不修改原报告）
    """
    # 深拷贝避免修改原报告
    report_dict = deepcopy(asdict(report))

    # 1. 脱敏 sandbox_runs 的 stdout/stderr
    for run in report_dict.get("sandbox_runs", []):
        if run.get("stdout_redacted"):
            run["stdout_redacted"], _ = redact_text(run["stdout_redacted"])
        if run.get("stderr_redacted"):
            run["stderr_redacted"], _ = redact_text(run["stderr_redacted"])

    # 2. 脱敏 filter_decisions 的 command/reason
    for decision in report_dict.get("filter_decisions", []):
        if decision.get("command_redacted"):
            decision["command_redacted"], _ = redact_text(decision["command_redacted"])
        if decision.get("reason"):
            decision["reason"], _ = redact_text(decision["reason"])

    # 3. 脱敏所有 findings 的 evidence/title/recommendation
    for finding_list in ["findings", "warnings", "needs_human_review"]:
        for finding in report_dict.get(finding_list, []):
            if finding.get("evidence"):
                finding["evidence"], _ = redact_text(finding["evidence"])
            if finding.get("title"):
                finding["title"], _ = redact_text(finding["title"])
            if finding.get("recommendation"):
                finding["recommendation"], _ = redact_text(finding["recommendation"])

    # 4. 脱敏 input_summary
    if report_dict.get("input_summary"):
        report_dict["input_summary"], _ = redact_text(report_dict["input_summary"])

    return report_dict


def write_reports(report: ReviewReport, out_dir: str) -> None:
    """
    生成三种格式的报告：JSON、Markdown、SARIF v2.1.0

    Args:
        report: 审查报告对象
        out_dir: 输出目录路径
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. JSON 报告
    _write_json_report(report, out_path)

    # 2. Markdown 报告
    _write_markdown_report(report, out_path)

    # 3. SARIF v2.1.0 报告
    _write_sarif_report(report, out_path)


def _write_json_report(report: ReviewReport, out_path: Path) -> None:
    """生成 JSON 报告（完整 report 对象，脱敏后）"""
    json_path = out_path / "review_report.json"

    # 脱敏后转换为 dict（Critical 1 修复）
    report_dict = _redact_report(report)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, ensure_ascii=False, indent=2)


def _write_markdown_report(report: ReviewReport, out_path: Path) -> None:
    """
    生成 Markdown 报告（8 段式，脱敏后）
    1. 标题
    2. Findings
    3. Warnings
    4. Needs Human Review
    5. Filter Decisions
    6. Sandbox Runs
    7. Monitoring
    8. Conclusion
    """
    md_path = out_path / "review_report.md"

    # 脱敏 input_summary（Critical 1 修复）
    input_summary_redacted, _ = redact_text(report.input_summary)

    lines = []

    # 1. 标题
    lines.append("# Code Review Report")
    lines.append("")
    lines.append(f"**Task ID:** {report.task_id}")
    lines.append(f"**Repository:** {report.repository}")
    lines.append(f"**Status:** {report.status}")
    lines.append(f"**Input Summary:** {input_summary_redacted}")  # 使用脱敏版本
    lines.append("")

    # 2. Findings
    lines.append("## Findings")
    lines.append("")
    if report.findings:
        for i, finding in enumerate(report.findings, 1):
            # 脱敏 finding 字段（Critical 1 修复）
            title_redacted, _ = redact_text(finding.title)
            evidence_redacted, _ = redact_text(finding.evidence)
            recommendation_redacted, _ = redact_text(finding.recommendation)

            lines.append(f"### {i}. {title_redacted}")  # 使用脱敏版本
            lines.append(f"- **Severity:** `{finding.severity.value}`")
            lines.append(f"- **Category:** {finding.category}")
            lines.append(f"- **File:** `{finding.file}:{finding.line}`")
            lines.append(f"- **Rule ID:** `{finding.rule_id}`")
            lines.append(f"- **Confidence:** {finding.confidence:.2f}")
            lines.append(f"- **Source:** {finding.source}")
            lines.append("")
            lines.append("**Evidence:**")
            lines.append("```")
            lines.append(evidence_redacted)  # 使用脱敏版本
            lines.append("```")
            lines.append("")
            lines.append("**Recommendation:**")
            lines.append(recommendation_redacted)  # 使用脱敏版本
            lines.append("")
    else:
        lines.append("No findings detected.")
        lines.append("")

    # 3. Warnings
    lines.append("## Warnings")
    lines.append("")
    if report.warnings:
        for i, warning in enumerate(report.warnings, 1):
            # 脱敏 warning 字段（Critical 1 修复）
            title_redacted, _ = redact_text(warning.title)
            recommendation_redacted, _ = redact_text(warning.recommendation)

            lines.append(f"### {i}. {title_redacted}")  # 使用脱敏版本
            lines.append(f"- **Severity:** `{warning.severity.value}`")
            lines.append(f"- **File:** `{warning.file}:{warning.line}`")
            lines.append(f"- **Recommendation:** {recommendation_redacted}")  # 使用脱敏版本
            lines.append("")
    else:
        lines.append("No warnings.")
        lines.append("")

    # 4. Needs Human Review
    lines.append("## Needs Human Review")
    lines.append("")
    if report.needs_human_review:
        for i, item in enumerate(report.needs_human_review, 1):
            # 脱敏 item 字段（Critical 1 修复）
            title_redacted, _ = redact_text(item.title)
            recommendation_redacted, _ = redact_text(item.recommendation)

            lines.append(f"### {i}. {title_redacted}")  # 使用脱敏版本
            lines.append(f"- **Severity:** `{item.severity.value}`")
            lines.append(f"- **File:** `{item.file}:{item.line}`")
            lines.append(f"- **Recommendation:** {recommendation_redacted}")  # 使用脱敏版本
            lines.append("")
    else:
        lines.append("No items need human review.")
        lines.append("")

    # 5. Filter Decisions
    lines.append("## Filter Decisions")
    lines.append("")
    if report.filter_decisions:
        for decision in report.filter_decisions:
            # 脱敏 decision 字段（Critical 1 修复）
            reason_redacted, _ = redact_text(decision.reason)
            command_redacted, _ = redact_text(decision.command_redacted)

            emoji = "✅" if decision.decision == "allow" else "🚫"
            lines.append(f"- {emoji} **{decision.stage}**: {decision.decision} - {reason_redacted}")  # 使用脱敏版本
            lines.append(f"  - Command: `{command_redacted}`")  # 使用脱敏版本
            lines.append("")
    else:
        lines.append("No filter decisions recorded.")
        lines.append("")

    # 6. Sandbox Runs
    lines.append("## Sandbox Runs")
    lines.append("")
    if report.sandbox_runs:
        for i, run in enumerate(report.sandbox_runs, 1):
            # 脱敏 sandbox_run 字段（Critical 1 修复）
            stdout_redacted, _ = redact_text(run.stdout_redacted)
            stderr_redacted, _ = redact_text(run.stderr_redacted)

            status_emoji = "✅" if run.status == "success" else "❌"
            lines.append(f"### {i}. {status_emoji} {run.runtime} - {run.status}")
            lines.append(f"- **Duration:** {run.duration_ms}ms")
            if run.exit_code is not None:
                lines.append(f"- **Exit Code:** {run.exit_code}")
            if run.error_type:
                lines.append(f"- **Error Type:** {run.error_type}")
            lines.append("")
            if stdout_redacted:  # 使用脱敏版本
                lines.append("**Stdout:**")
                lines.append("```")
                lines.append(stdout_redacted)
                lines.append("```")
                lines.append("")
            if stderr_redacted:  # 使用脱敏版本
                lines.append("**Stderr:**")
                lines.append("```")
                lines.append(stderr_redacted)
                lines.append("```")
                lines.append("")
            if run.truncated:
                lines.append("*Output truncated due to size limit*")
                lines.append("")
    else:
        lines.append("No sandbox runs recorded.")
        lines.append("")

    # 7. Monitoring
    lines.append("## Monitoring")
    lines.append("")
    lines.append("### Performance Metrics")
    lines.append(f"- **Total Duration:** {report.monitoring.total_duration_ms}ms")
    lines.append(f"- **Sandbox Duration:** {report.monitoring.sandbox_duration_ms}ms")
    lines.append(f"- **Tool Calls:** {report.monitoring.tool_call_count}")
    lines.append(f"- **Blocked Operations:** {report.monitoring.blocked_count}")
    lines.append("")

    lines.append("### Findings Summary")
    lines.append(f"- **Total Findings:** {report.monitoring.finding_count}")
    lines.append("- **Severity Distribution:**")
    for sev, count in report.monitoring.severity_distribution.items():
        lines.append(f"  - `{sev}`: {count}")
    lines.append("")

    if report.monitoring.exception_distribution:
        lines.append("### Exception Summary")
        lines.append("- **Exception Distribution:**")
        for exc_type, count in report.monitoring.exception_distribution.items():
            lines.append(f"  - `{exc_type}`: {count}")
        lines.append("")

    # 8. Conclusion
    lines.append("## Conclusion")
    lines.append("")
    conclusion_emoji = {
        "approve": "✅",
        "changes_requested": "⚠️",
        "needs_human_review": "👥",
        "completed_with_warnings": "⚡",
    }
    emoji = conclusion_emoji.get(report.conclusion, "ℹ️")
    lines.append(f"{emoji} **Conclusion:** {report.conclusion}")
    lines.append("")
    lines.append(f"Review completed with status: **{report.status}**. "
                 f"Please review the findings above and take appropriate action.")
    lines.append("")

    md_content = "\n".join(lines)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)


def _write_sarif_report(report: ReviewReport, out_path: Path) -> None:
    """
    生成 SARIF v2.1.0 报告（脱敏后）
    结构：runs[].results[].locations[].physicalLocation
    """
    sarif_path = out_path / "review_report.sarif"

    # 合并所有 findings（findings + warnings + needs_review）
    all_findings = [
        *report.findings,
        *report.warnings,
        *report.needs_human_review,
    ]

    # 构建 SARIF 结果
    results = []
    for finding in all_findings:
        # 脱敏 title 和 recommendation（Critical 1 修复）
        title_redacted, _ = redact_text(finding.title)
        recommendation_redacted, _ = redact_text(finding.recommendation)

        result = {
            "ruleId":
            finding.rule_id,
            "level":
            _map_severity_to_level(finding.severity),
            "message": {
                "text": f"{title_redacted}: {recommendation_redacted}"  # 使用脱敏版本
            },
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": finding.file
                    },
                    "region": {
                        "startLine": finding.line if finding.line else 1,
                    },
                }
            }],
        }
        results.append(result)

    # 构建 SARIF v2.1.0 结构
    sarif = {
        "version":
        "2.1.0",
        "$schema":
        "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "trpc-code-review-agent",
                    "version": "1.0.0",
                    "informationUri": "https://github.com/your-org/trpc-agent-python",
                }
            },
            "results":
            results,
            "invocations": [{
                "startTimeUtc": datetime.utcnow().isoformat() + "Z",
                "endTimeUtc": datetime.utcnow().isoformat() + "Z",
                "exitCode": 0,
                "toolExecutionNotifications": [],
            }],
        }],
    }

    with open(sarif_path, "w", encoding="utf-8") as f:
        json.dump(sarif, f, ensure_ascii=False, indent=2)


def _map_severity_to_level(severity: Severity) -> str:
    """
    映射 Severity 到 SARIF level
    - CRITICAL/HIGH -> error
    - MEDIUM -> warning
    - LOW -> note
    """
    if severity in [Severity.CRITICAL, Severity.HIGH]:
        return "error"
    elif severity == Severity.MEDIUM:
        return "warning"
    else:  # LOW
        return "note"
