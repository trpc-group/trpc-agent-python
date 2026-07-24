"""Automatic code-review orchestration."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections import Counter
from dataclasses import replace
from pathlib import Path

from diff_parser import DiffParser
from models import Finding
from models import ReviewReport
from redaction import redact_text
from reporting import write_reports
from rules import RuleEngine
from sandbox import SandboxRunner
from storage import SQLiteReviewStore


class CodeReviewAgent:
    def __init__(
        self,
        root: str | Path,
        database: str | Path,
        policy: str | Path,
        dry_run: bool = False,
    ):
        self.root = Path(root)
        self.store = SQLiteReviewStore(database)
        self.runner = SandboxRunner(policy, self.root, dry_run=dry_run)
        self.rules = RuleEngine()
        self.confidence_threshold = float(self.runner.policy["confidence_threshold"])

    def review_diff(
        self,
        diff: str,
        output_dir: str | Path,
        commands: list[list[str]] | None = None,
    ) -> ReviewReport:
        started = time.perf_counter()
        task_id = str(uuid.uuid4())
        clean_diff, _ = redact_text(diff)
        # Rules inspect the in-memory source so secret detectors retain their
        # signal. Only redacted derivatives cross a logging/persistence boundary.
        lines = DiffParser.parse(diff)
        files = sorted({line.file for line in lines})
        summary = {
            "sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "files": files,
            "added_lines": len(lines),
            "redacted": clean_diff != diff,
        }
        self.store.create_task(task_id, summary["sha256"], summary)
        exceptions: Counter[str] = Counter()
        sandbox_runs = []
        try:
            raw_findings = [self._redact_finding(item) for item in self.rules.scan(lines)]
            findings = [item for item in raw_findings if item.confidence >= self.confidence_threshold]
            warnings = [item for item in raw_findings if item.confidence < self.confidence_threshold]
            for index, command in enumerate(commands or [["python", "-m", "compileall", "-q", "."]], 1):
                run = self.runner.run(command, index)
                sandbox_runs.append(run)
                if run.error_type:
                    exceptions[run.error_type] += 1
            filter_blocks = [
                {"command": run.command, "decision": run.filter_decision, "reason": run.filter_reason}
                for run in sandbox_runs if run.status == "blocked"
            ]
            severity = Counter(item.severity for item in findings)
            conclusion = "changes_requested" if any(
                item.severity in ("critical", "high") for item in findings
            ) else "needs_human_review" if warnings or filter_blocks else "approve"
            status = "completed_with_errors" if exceptions else "completed"
            monitoring = {
                "total_duration_ms": (time.perf_counter() - started) * 1000,
                "sandbox_duration_ms": sum(run.duration_ms for run in sandbox_runs),
                "tool_call_count": len(sandbox_runs),
                "blocked_count": len(filter_blocks),
                "finding_count": len(findings),
                "warning_count": len(warnings),
                "severity_distribution": dict(severity),
                "exception_type_distribution": dict(exceptions),
            }
            report = ReviewReport(
                task_id=task_id,
                status=status,
                conclusion=conclusion,
                input_summary=summary,
                findings=findings,
                warnings=warnings,
                filter_blocks=filter_blocks,
                sandbox_runs=sandbox_runs,
                monitoring=monitoring,
            )
        except Exception as exc:
            exceptions[type(exc).__name__] += 1
            report = ReviewReport(
                task_id=task_id,
                status="failed",
                conclusion="needs_human_review",
                input_summary=summary,
                monitoring={
                    "total_duration_ms": (time.perf_counter() - started) * 1000,
                    "sandbox_duration_ms": 0.0,
                    "tool_call_count": 0,
                    "blocked_count": 0,
                    "finding_count": 0,
                    "warning_count": 0,
                    "severity_distribution": {},
                    "exception_type_distribution": dict(exceptions),
                },
            )
        self.store.save_report(report)
        write_reports(report, output_dir)
        return report

    def review_file(self, diff_file: str | Path, output_dir: str | Path) -> ReviewReport:
        diff = Path(diff_file).read_text(encoding="utf-8")
        return self.review_diff(diff, output_dir)

    def review_repo(self, repo_path: str | Path, output_dir: str | Path) -> ReviewReport:
        diff, _ = DiffParser.from_repo(repo_path)
        return self.review_diff(diff, output_dir)

    @staticmethod
    def _redact_finding(finding: Finding) -> Finding:
        evidence, _ = redact_text(finding.evidence)
        title, _ = redact_text(finding.title)
        recommendation, _ = redact_text(finding.recommendation)
        return replace(finding, evidence=evidence, title=title, recommendation=recommendation)
