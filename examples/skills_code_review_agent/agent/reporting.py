"""Finding validation, deduplication, and JSON/Markdown reporting."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import MAX_JSON_DEPTH
from .constants import MAX_JSONL_LINE_BYTES
from .constants import MAX_JSONL_LINES
from .constants import MAX_OUTPUT_BYTES
from .constants import MAX_TEXT_FIELD_LENGTH
from .constants import MIN_ACTIONABLE_CONFIDENCE
from .models import Finding
from .models import ReviewReport
from .models import Severity
from .policy import SecretRedactor
from .storage import finding_fingerprint

_MARKDOWN_SPECIAL_PATTERN = re.compile(r"([\\`*_[\]{}()#+\-.!|>])")

FINDING_FIELDS = frozenset({
    "severity",
    "category",
    "file",
    "line",
    "title",
    "evidence",
    "recommendation",
    "confidence",
    "source",
})
REPORT_JSON_NAME = "review_report.json"
REPORT_MARKDOWN_NAME = "review_report.md"
MERGE_SEPARATOR = " | "
_SEVERITY_RANK = {
    Severity.WARNING: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class FindingOutputError(ValueError):
    """Raised for malformed or over-budget scanner output."""


@dataclass(frozen=True)
class FindingBuckets:
    """Actionable, warning, and human-review finding groups."""

    actionable: list[Finding]
    warnings: list[Finding]
    needs_human_review: list[Finding]

    @property
    def all_findings(self) -> list[Finding]:
        """Return every unique finding once."""
        return [*self.actionable, *self.warnings]

    @property
    def human_fingerprints(self) -> set[str]:
        """Return storage fingerprints for manual findings."""
        return {finding_fingerprint(item) for item in self.needs_human_review}


def _validate_json_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise FindingOutputError("finding JSON exceeds depth limit")
    if isinstance(value, dict):
        for item in value.values():
            _validate_json_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_json_depth(item, depth + 1)
    elif isinstance(value, str) and len(value) > MAX_TEXT_FIELD_LENGTH:
        raise FindingOutputError("finding text exceeds length limit")


def _parse_finding(line: str, redactor: SecretRedactor) -> Finding:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise FindingOutputError(f"invalid finding JSON: {exc.msg}") from exc
    if not isinstance(payload, dict) or set(payload) != FINDING_FIELDS:
        raise FindingOutputError("finding must contain exactly the nine required fields")
    _validate_json_depth(payload)
    return Finding.model_validate(redactor.redact_value(payload))


def parse_findings_jsonl(text: str, redactor: SecretRedactor) -> list[Finding]:
    """Parse bounded JSONL emitted by the sandbox."""
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise FindingOutputError("finding output exceeds total byte limit")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_JSONL_LINES:
        raise FindingOutputError("finding output exceeds line limit")
    findings = []
    for line in lines:
        if len(line.encode("utf-8")) > MAX_JSONL_LINE_BYTES:
            raise FindingOutputError("finding output line exceeds byte limit")
        findings.append(_parse_finding(line, redactor))
    return findings


def _merge_text(left: str, right: str) -> str:
    values = []
    for value in (left, right):
        if value and value not in values:
            values.append(value)
    return MERGE_SEPARATOR.join(values)[:MAX_TEXT_FIELD_LENGTH]


def _merge_findings(left: Finding, right: Finding) -> Finding:
    preferred = right
    if (
            _SEVERITY_RANK[left.severity],
            left.confidence,
    ) > (
            _SEVERITY_RANK[right.severity],
            right.confidence,
    ):
        preferred = left
    return preferred.model_copy(update={
        "evidence": _merge_text(left.evidence, right.evidence),
        "recommendation": _merge_text(left.recommendation, right.recommendation),
        "source": _merge_text(left.source, right.source),
        "confidence": max(left.confidence, right.confidence),
    }, )


def deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    """Deduplicate by file, line, and category as required by Issue #92."""
    selected: dict[tuple[str, int | None, str], Finding] = {}
    for finding in findings:
        key = (finding.file, finding.line, finding.category.value)
        previous = selected.get(key)
        selected[key] = finding if previous is None else _merge_findings(previous, finding)
    return sorted(
        selected.values(),
        key=lambda item: (item.file, item.line or 0, item.category.value),
    )


def prepare_findings(
    findings: list[Finding],
    redactor: SecretRedactor,
) -> FindingBuckets:
    """Redact, deduplicate, and route low-confidence findings."""
    safe = [Finding.model_validate(redactor.redact_value(item.model_dump(mode="json"))) for item in findings]
    actionable = []
    warnings = []
    for finding in deduplicate_findings(safe):
        if finding.confidence < MIN_ACTIONABLE_CONFIDENCE:
            warnings.append(finding.model_copy(update={"severity": Severity.WARNING}))
        else:
            actionable.append(finding)
    return FindingBuckets(
        actionable=actionable,
        warnings=warnings,
        needs_human_review=list(warnings),
    )


def render_markdown(report: ReviewReport) -> str:
    """Render the fixed report sections required by Issue #92."""
    task_id = _markdown_text(report.task_id)
    conclusion = _markdown_text(report.conclusion)
    file_count = _markdown_text(report.input_summary.get("file_count", 0))
    lines = [
        f"# Code Review Report: {task_id}",
        "",
        "## Task summary",
        "",
        f"- Status: `{report.status.value}`",
        f"- Files: {file_count}",
        f"- Conclusion: {conclusion}",
        "",
        "## Findings",
        "",
    ]
    lines.extend(_finding_markdown(report.findings))
    lines.extend(["", "## Warnings / needs human review", ""])
    lines.extend(_finding_markdown(report.warnings))
    lines.extend(["", "## Filter decisions", ""])
    lines.extend(f"- `{item.action.value}` {_markdown_text(item.rule_id)}: {_markdown_text(item.reason)}"
                 for item in report.filter_decisions)
    lines.extend(["", "## Sandbox runs", ""])
    lines.extend(f"- `{item.status.value}` exit={item.exit_code} "
                 f"timeout={item.timed_out} duration_ms={item.duration_ms}" for item in report.sandbox_runs)
    lines.extend(["", "## Exceptions", ""])
    lines.extend(f"- {_markdown_text(failure)}" for failure in report.failures)
    lines.extend(["", "## Metrics", "", "```json"])
    lines.append(json.dumps(report.metrics.model_dump(mode="json"), indent=2, sort_keys=True))
    lines.extend(["```", "", "## Final conclusion", "", conclusion, ""])
    return "\n".join(lines)


def _markdown_text(value: Any) -> str:
    normalized = str(value).replace("\r", " ").replace("\n", " ")
    escaped = html.escape(normalized, quote=False)
    return _MARKDOWN_SPECIAL_PATTERN.sub(r"\\\1", escaped)


def _finding_markdown(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["- None"]
    return [
        f"- **{item.severity.value} / {item.category.value}** "
        f"{_markdown_text(item.file)}:{item.line} — {_markdown_text(item.title)}  \n"
        f"  Evidence: {_markdown_text(item.evidence)}  \n"
        f"  Recommendation: {_markdown_text(item.recommendation)}  \n"
        f"  Confidence: {item.confidence:.2f}; Source: {_markdown_text(item.source)}" for item in findings
    ]


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_report_files(
    report: ReviewReport,
    output_dir: Path,
) -> tuple[Path, Path, str]:
    """Atomically write JSON and Markdown report files."""
    safe_report = ReviewReport.model_validate(SecretRedactor().redact_value(report.model_dump(mode="json")), )
    markdown = render_markdown(safe_report)
    task_dir = output_dir / safe_report.task_id
    json_path = task_dir / REPORT_JSON_NAME
    markdown_path = task_dir / REPORT_MARKDOWN_NAME
    payload = json.dumps(
        safe_report.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    _atomic_write(json_path, payload + "\n")
    _atomic_write(markdown_path, markdown)
    return json_path, markdown_path, markdown
