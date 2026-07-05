# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end deterministic review pipeline (issue #92 backbone).

parse -> materialize changed files -> run scanners -> dedup/denoise -> build+redact report ->
(optionally) persist. This path needs no LLM and no real sandbox, so it satisfies the dry-run /
fake-model requirement on its own. The agent path (``agent/``) reuses ``run_review`` as its tool.

Exceptions are caught at the pipeline boundary, classified by type into ``exception_dist``, and
surfaced in the report's monitoring section — a failing scanner degrades the result but never
crashes the review (requirement 7 "failure logging" / requirement 9 "exception-type distribution").
"""
from __future__ import annotations

import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import diff_parser, report as report_mod, scanners
from .dedup import dedup_and_denoise
from .policy import ReviewPolicy
from .types import DiffSummary, Finding, ReviewReport


@dataclass
class ReviewResult:
    """Everything one review produced — the report to render plus what the DB layer persists."""

    task_id: str
    report: ReviewReport
    findings: list[Finding]  # deduped/denoised (active + warning + needs_human_review)
    summary: DiffSummary
    source_type: str
    source_ref: str
    monitoring: dict = field(default_factory=dict)


def _materialize(diff_text: str) -> tuple[DiffSummary, str]:
    """Parse a diff and write its post-change files into a temp dir for scanning."""
    summary = diff_parser.parse_unified_diff(diff_text)
    files = diff_parser.materialize_new_files(diff_text)
    tmp = tempfile.mkdtemp(prefix="cr_scan_")
    for rel, content in files.items():
        dest = Path(tmp) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return summary, tmp


def run_review(
    *,
    task_id: Optional[str] = None,
    diff_text: Optional[str] = None,
    repo_path: Optional[str] = None,
    runtime: str = "inprocess",
    sandbox_timeout: float | None = None,
    max_output_bytes: int | None = None,
    policy: ReviewPolicy | None = None,
    warn_threshold: float | None = None,
    review_threshold: float | None = None,
) -> ReviewResult:
    """Run one review (deterministic, no LLM). Provide either ``diff_text`` or ``repo_path``.

    ``runtime``: ``inprocess`` (default, fast) runs scanners in-process; ``local`` runs them in a
    subprocess sandbox with timeout + output cap (dev fallback) and records a sandbox run. The
    ``container`` runtime (production isolation) is async — see ``run_review_container``.

    Returns a ``ReviewResult``; persistence is done separately by the async ``storage.dao.ReviewStore``
    so this core stays synchronous and dependency-light.
    """
    task_id = task_id or f"cr-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    exception_dist: dict[str, int] = {}

    if diff_text is not None:
        summary, scan_dir = _materialize(diff_text)
        source_type, source_ref = "diff_file", "<diff>"
    elif repo_path is not None:
        summary = diff_parser.parse_git_worktree(repo_path)
        scan_dir = repo_path
        source_type, source_ref = "repo_path", repo_path
    else:
        raise ValueError("run_review requires diff_text or repo_path")

    sandbox_runs: list = []
    if runtime == "local":
        from . import sandbox as sandbox_mod
        raw, run = sandbox_mod.run_local(
            scan_dir,
            timeout=sandbox_timeout if sandbox_timeout is not None else sandbox_mod.DEFAULT_TIMEOUT_SEC,
            max_bytes=max_output_bytes if max_output_bytes is not None else sandbox_mod.MAX_OUTPUT_BYTES,
            policy=policy if policy is not None else ReviewPolicy())
        sandbox_runs = [run]
        if run.timed_out or (not run.blocked and run.exit_code not in (0, 1)):  # 1 = issues found (normal)
            exception_dist["sandbox_failure"] = exception_dist.get("sandbox_failure", 0) + 1
    else:  # "inprocess"
        try:
            raw = scanners.scan(scan_dir, summary)
        except Exception as exc:  # noqa: BLE001 - boundary; never crash the task
            exception_dist[type(exc).__name__] = exception_dist.get(type(exc).__name__, 0) + 1
            raw = []

    # missing_tests is a diff-level check (no file content / sandbox needed) — add it for every runtime.
    raw = list(raw) + scanners.detect_missing_tests(summary)
    return _assemble(task_id, summary, raw, sandbox_runs, source_type, source_ref, started, exception_dist,
                     warn_threshold, review_threshold)


def _assemble(task_id, summary, raw, sandbox_runs, source_type, source_ref, started, exception_dist, warn_threshold,
              review_threshold) -> ReviewResult:
    """Shared tail: dedup/denoise -> monitoring -> build+redact report -> ReviewResult."""
    findings = dedup_and_denoise(
        raw,
        warn_threshold if warn_threshold is not None else dedup_thresholds()[0],
        review_threshold if review_threshold is not None else dedup_thresholds()[1],
    )
    for f in findings:
        if f.category == "scanner_error":
            exception_dist["scanner_error"] = exception_dist.get("scanner_error", 0) + 1

    active = [f for f in findings if f.status == "active"]
    severity_dist: dict[str, int] = {}
    for f in active:
        severity_dist[f.severity] = severity_dist.get(f.severity, 0) + 1

    filter_blocks = [{
        "script": r.script,
        "reason": r.block_reason,
        "category": r.block_category
    } for r in sandbox_runs if r.blocked]

    monitoring = {
        "total_sec": round(time.monotonic() - started, 3),
        "sandbox_sec": round(sum(r.duration_sec for r in sandbox_runs), 3),
        "tool_calls": len(scanners.ADAPTERS),
        "block_count": len(filter_blocks),
        "finding_count": len(active),
        "severity_dist": severity_dist,
        "exception_dist": exception_dist,
    }

    report = report_mod.build_report(task_id,
                                     findings,
                                     sandbox_runs=sandbox_runs,
                                     filter_blocks=filter_blocks,
                                     monitoring=monitoring)
    return ReviewResult(task_id=task_id,
                        report=report,
                        findings=findings,
                        summary=summary,
                        source_type=source_type,
                        source_ref=source_ref,
                        monitoring=monitoring)


async def run_review_container(
    *,
    task_id: Optional[str] = None,
    diff_text: str,
    sandbox_timeout: float | None = None,
    max_output_bytes: int | None = None,
) -> ReviewResult:
    """Run a review with scanners inside a Container workspace (production isolation; needs Docker)."""
    from . import sandbox as sandbox_mod
    task_id = task_id or f"cr-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    summary, scan_dir = _materialize(diff_text)
    raw, run = await sandbox_mod.run_container(
        scan_dir,
        timeout=sandbox_timeout if sandbox_timeout is not None else sandbox_mod.DEFAULT_TIMEOUT_SEC,
        max_bytes=max_output_bytes if max_output_bytes is not None else sandbox_mod.MAX_OUTPUT_BYTES)
    exception_dist: dict[str, int] = {}
    if run.timed_out or run.exit_code not in (0, 1):
        exception_dist["sandbox_failure"] = 1
    raw = list(raw) + scanners.detect_missing_tests(summary)
    return _assemble(task_id, summary, raw, [run], "diff_file", "<diff>", started, exception_dist, None, None)


def dedup_thresholds() -> tuple[float, float]:
    from .dedup import REVIEW_THRESHOLD, WARN_THRESHOLD
    return WARN_THRESHOLD, REVIEW_THRESHOLD


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Deterministic code-review pipeline (no LLM).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--diff-file")
    src.add_argument("--repo-path")
    ap.add_argument("--out-dir", default=".")
    args = ap.parse_args()

    if args.diff_file:
        result = run_review(diff_text=Path(args.diff_file).read_text(encoding="utf-8"))
    else:
        result = run_review(repo_path=args.repo_path)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "review_report.json").write_text(report_mod.render_json(result.report), encoding="utf-8")
    (out / "review_report.md").write_text(report_mod.render_md(result.report), encoding="utf-8")
    print(f"[{result.task_id}] {result.report.findings_summary} -> {out}/review_report.json")


if __name__ == "__main__":
    _main()
