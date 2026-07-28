"""Application service that orchestrates a complete review."""

from __future__ import annotations

import hashlib
from pathlib import Path
import uuid
from typing import Any, Callable, Iterable

from .metrics import MetricsCollector
from .models import Finding, ReviewReport, ReviewRequest
from .sandbox import run_sandbox_checks
from .sanitizer import normalize_findings, redact_sensitive_text
from .storage import ReviewRepository

Analyzer = Callable[[Any], Iterable[Finding | dict[str, Any]]]


def _rule_set_digest() -> str:
    rules = Path(__file__).parents[1] / "skills" / "code-review" / "rules"
    digest = hashlib.sha256()
    for path in sorted(rules.glob("*.yaml")):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def run_review(
    request: ReviewRequest,
    *,
    runtime: Any = None,
    repository: ReviewRepository | None = None,
    analyzer: Analyzer | None = None,
    metrics: MetricsCollector | None = None,
) -> ReviewReport:
    metrics = metrics or MetricsCollector()
    task_id = request.task_id or uuid.uuid4().hex
    if repository:
        repository.create_task(
            task_id,
            input_type=request.review_input.source_type,
            input_digest=request.review_input.digest,
            summary=request.review_input.summary,
        )
    try:
        runs, candidates, warnings = await run_sandbox_checks(
            runtime,
            request.review_input,
            runtime_name=request.runtime,
            dry_run=request.dry_run,
            metrics=metrics,
        )
        if analyzer and not request.dry_run:
            try:
                candidates.extend(
                    item if isinstance(item, Finding) else Finding.model_validate(item)
                    for item in analyzer(request.review_input)
                )
            except Exception as exc:
                metrics.record_error(exc)
                warnings.append(f"model analyzer failed: {type(exc).__name__}")
        findings, normalization_warnings, human = normalize_findings(candidates, request.review_input)
        warnings.extend(normalization_warnings)
        for finding in findings:
            metrics.record_finding(finding.severity)
        failed_checks = any(item.status in {"failed", "timed_out"} for item in runs)
        status = "partial" if failed_checks or warnings else "completed"
        if request.dry_run:
            conclusion = (
                f"Dry run only: {len(runs)} sandbox task(s) were planned and "
                "no checks were executed."
            )
        elif findings:
            conclusion = f"Found {len(findings)} actionable issue(s)."
        elif human:
            conclusion = f"No confirmed issues; {len(human)} item(s) need human review."
        else:
            conclusion = "No actionable issues found."
        report = ReviewReport(
            task_id=task_id,
            status=status,
            conclusion=conclusion,
            input_summary=request.review_input.summary,
            findings=findings,
            needs_human_review=human,
            warnings=[redact_sensitive_text(item) for item in warnings],
            sandbox_runs=runs,
            filter_decisions=[item.decision for item in runs],
            metrics=metrics.snapshot(),
            dry_run=request.dry_run,
            rule_set_digest=_rule_set_digest(),
        )
        if repository:
            repository.save_review_result(report)
        return report
    except Exception as exc:
        if repository:
            repository.mark_failed(task_id, type(exc).__name__)
        raise
