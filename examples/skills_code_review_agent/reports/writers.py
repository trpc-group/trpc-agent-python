"""Write machine-readable and human-readable review reports."""

import html
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .models import ReviewFinding
from .models import ReviewReport
from security import redact_report


@dataclass(frozen=True)
class ReportArtifacts:
    """Paths generated for a completed report."""

    json_path: Path
    markdown_path: Path


class ReportWriter:
    """Render the two report formats required by the example."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def write(self, report: ReviewReport) -> ReportArtifacts:
        """Write JSON and Markdown files for one report."""
        report = redact_report(report)
        self.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_metadata = os.lstat(self.output_dir)
        if stat.S_ISLNK(output_metadata.st_mode) or not stat.S_ISDIR(
            output_metadata.st_mode
        ):
            raise ValueError("Report output path must be a directory, not a link")
        report_dir = self.output_dir / report.task_id
        try:
            report_dir.mkdir(mode=0o700)
        except FileExistsError:
            metadata = os.lstat(report_dir)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("Task report path must be a directory, not a link")
        report_dir.chmod(0o700)
        json_path = report_dir / "review_report.json"
        markdown_path = report_dir / "review_report.md"
        # Publish the machine-readable report last so partial pairs are not authoritative.
        self._atomic_write(
            markdown_path,
            self._to_markdown(report),
        )
        self._atomic_write(
            json_path,
            report.model_dump_json(indent=2),
        )
        return ReportArtifacts(json_path=json_path, markdown_path=markdown_path)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _text(value: object) -> str:
        """Escape model-controlled text so it cannot create Markdown structure."""
        escaped = html.escape(str(value), quote=False)
        for character in "\\`*_{}[]<>()#+-!|":
            escaped = escaped.replace(character, f"\\{character}")
        return escaped.replace("\r", "").replace("\n", "  \n")

    @staticmethod
    def _inline_code(value: object) -> str:
        text = str(value).replace("\r", " ").replace("\n", " ")
        longest = max(
            (len(match.group(0)) for match in re.finditer(r"`+", text)),
            default=0,
        )
        fence = "`" * max(1, longest + 1)
        padding = " " if text.startswith("`") or text.endswith("`") else ""
        return f"{fence}{padding}{text}{padding}{fence}"

    @staticmethod
    def _code_block(value: object) -> str:
        text = str(value).replace("\r", "")
        longest = max(
            (len(match.group(0)) for match in re.finditer(r"`+", text)),
            default=0,
        )
        fence = "`" * max(3, longest + 1)
        return f"{fence}text\n{text}\n{fence}"

    @staticmethod
    def _to_markdown(report: ReviewReport) -> str:
        lines = [
            "# Code Review Report",
            "",
            f"- Task ID: {ReportWriter._inline_code(report.task_id)}",
            f"- Status: {ReportWriter._inline_code(report.status)}",
            f"- Created: {ReportWriter._inline_code(report.created_at.isoformat())}",
            f"- Completed: {ReportWriter._inline_code(report.completed_at.isoformat())}",
            f"- Repository: {ReportWriter._inline_code(report.repository)}",
            f"- Scope: {ReportWriter._inline_code(report.scope.value)}",
            "- Input: "
            f"{ReportWriter._inline_code(report.input_summary.kind)} / "
            f"{ReportWriter._inline_code(report.input_summary.source)}",
            "",
            "## Summary",
            "",
            ReportWriter._text(report.analysis.summary),
            "",
            f"- Findings: `{len(report.analysis.findings)}`",
            f"- Warnings: `{len(report.analysis.warnings)}`",
            "- Needs human review: "
            f"`{len(report.analysis.needs_human_review)}`",
            "- Severity distribution: "
            f"`{report.monitoring.severity_distribution}`",
            "",
            "## Findings",
            "",
        ]
        if not report.analysis.findings:
            lines.append("No findings.")
        for finding in report.analysis.findings:
            location = finding.file
            if finding.line is not None:
                location = f"{location}:{finding.line}"
            lines.extend(
                [
                    "### "
                    f"[{finding.severity.upper()}] {ReportWriter._text(finding.title)}",
                    "",
                    f"- Category: {ReportWriter._inline_code(finding.category)}",
                    f"- Location: {ReportWriter._inline_code(location)}",
                    f"- Confidence: {ReportWriter._inline_code(f'{finding.confidence:.2f}')}",
                    f"- Source: {ReportWriter._inline_code(finding.source)}",
                    "",
                    ReportWriter._code_block(finding.evidence),
                    "",
                    f"Recommendation: {ReportWriter._text(finding.recommendation)}",
                    "",
                ]
            )
        ReportWriter._append_finding_section(
            lines,
            "Warnings",
            report.analysis.warnings,
        )
        ReportWriter._append_finding_section(
            lines,
            "Needs Human Review",
            report.analysis.needs_human_review,
        )
        lines.extend(["", "## Checks Performed", ""])
        if report.analysis.checks_performed:
            lines.extend(
                f"- {ReportWriter._text(item)}"
                for item in report.analysis.checks_performed
            )
        else:
            lines.append("- None reported.")
        lines.extend(["", "## Filter Decisions", ""])
        if report.filter_decisions:
            for decision in report.filter_decisions:
                lines.append(
                    f"- {ReportWriter._inline_code(decision.decision)} — "
                    f"{ReportWriter._inline_code(decision.command)}: "
                    f"{ReportWriter._text(decision.reason)}"
                )
        else:
            lines.append("- None recorded.")
        lines.extend(["", "## Sandbox Runs", ""])
        if report.sandbox_runs:
            for run in report.sandbox_runs:
                flags = []
                if run.timed_out:
                    flags.append("timed_out")
                if run.output_truncated:
                    flags.append("output_truncated")
                flag_text = f", flags={','.join(flags)}" if flags else ""
                lines.append(
                    f"- {ReportWriter._inline_code(run.status)} — "
                    f"{ReportWriter._inline_code(run.command)} "
                    f"({run.duration_ms:.2f} ms, exit={run.exit_code}{flag_text})"
                )
                if run.stderr_summary:
                    lines.append(f"  Error: {ReportWriter._text(run.stderr_summary)}")
        else:
            lines.append("- No sandbox run recorded.")
        metrics = report.monitoring
        lines.extend(
            [
                "",
                "## Monitoring",
                "",
                f"- Total duration: `{metrics.total_duration_ms:.2f} ms`",
                f"- Sandbox duration: `{metrics.sandbox_duration_ms:.2f} ms`",
                f"- Tool calls: `{metrics.tool_call_count}`",
                f"- Blocked executions: `{metrics.blocked_count}`",
                f"- Findings: `{metrics.finding_count}`",
                f"- Severity distribution: `{metrics.severity_distribution}`",
                f"- Exception distribution: `{metrics.exception_distribution}`",
                "",
                "## Conclusion",
                "",
                ReportWriter._text(report.conclusion),
            ]
        )
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _append_finding_section(
        lines: list[str],
        title: str,
        findings: list[ReviewFinding],
    ) -> None:
        lines.extend(["", f"## {title}", ""])
        if not findings:
            lines.append("None.")
            return
        for finding in findings:
            location = finding.file
            if finding.line is not None:
                location = f"{location}:{finding.line}"
            lines.extend(
                [
                    "- **"
                    f"[{finding.severity.upper()}] {ReportWriter._text(finding.title)}** ",
                    f"  {ReportWriter._inline_code(location)} · "
                    f"{ReportWriter._inline_code(finding.category)} · "
                    f"confidence {ReportWriter._inline_code(f'{finding.confidence:.2f}')}",
                    f"  {ReportWriter._code_block(finding.evidence)}",
                    "  Recommendation: "
                    f"{ReportWriter._text(finding.recommendation)}",
                ]
            )
