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

from . import diff_parser, report as report_mod
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


#: Dropped next to the materialized files so the skill script can locate its own scan root and
#: restrict findings to changed lines. Hidden, so the scanners never scan it (it carries the diff's
#: raw secrets verbatim).
DIFF_SIDECAR = ".changes.diff"


def new_task_id() -> str:
    """A fresh review task id."""
    return f"cr-{uuid.uuid4().hex[:12]}"


def materialize_diff(diff_text: str) -> tuple[DiffSummary, str]:
    """Parse a diff and write its post-change files into a temp dir for scanning.

    The diff itself is written alongside as ``.changes.diff``. That sidecar is how the skill script
    finds its scan root: the workspace runtimes stage inputs at *different* depths (the local
    runtime nests them under a directory named after the source, the container runtime does not), so
    a fixed ``--target`` path cannot be relied on. Anchoring on the sidecar makes findings' file
    paths match the diff's paths under every layout — and gives the script the diff it needs for
    changed-line filtering and the missing-tests rule.
    """
    summary = diff_parser.parse_unified_diff(diff_text)
    files = diff_parser.materialize_new_files(diff_text)
    tmp = tempfile.mkdtemp(prefix="cr_scan_")
    for rel, content in files.items():
        dest = Path(tmp) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    (Path(tmp) / DIFF_SIDECAR).write_text(diff_text, encoding="utf-8")
    return summary, tmp


#: Historical private name; several call sites inside this module still use it.
_materialize = materialize_diff


def _materialize_files(paths: list[str], repo_root: str) -> str:
    """Copy a list of files into a temp dir for scanning (the --files / file-list input mode)."""
    tmp = tempfile.mkdtemp(prefix="cr_scan_")
    for rel in paths:
        dest = Path(tmp) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            dest.write_text((Path(repo_root) / rel).read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        except OSError:
            continue
    return tmp


def run_review(
    *,
    task_id: Optional[str] = None,
    diff_text: Optional[str] = None,
    repo_path: Optional[str] = None,
    files: Optional[list[str]] = None,
    repo_root: str = ".",
    runtime: str = "auto",
    sandbox_timeout: float | None = None,
    max_output_bytes: int | None = None,
    policy: ReviewPolicy | None = None,
    warn_threshold: float | None = None,
    review_threshold: float | None = None,
) -> ReviewResult:
    """Run one review deterministically, with no LLM. Provide ``diff_text``, ``files`` or ``repo_path``.

    This is the **development / acceptance** path: it launches the code-review Skill's own script in
    a subprocess (see ``devrun``). Production isolation is the container workspace the agent drives
    through ``skill_run`` — issue #92 allows a local runtime only as a development fallback, so
    ``runtime`` accepts just ``auto``/``local``, and both mean the same thing here.

    Returns a ``ReviewResult``; persistence is done separately by the async ``storage.dao.ReviewStore``
    so this core stays synchronous and dependency-light.
    """
    from . import devrun, skill_results

    if runtime not in ("auto", "local"):
        raise ValueError(f"unsupported runtime {runtime!r}: the deterministic CLI runs the skill script in a "
                         f"subprocess. For sandboxed production execution use the agent path "
                         f"(`run_agent.py --runtime container`), which drives the skill through skill_run.")

    task_id = task_id or new_task_id()
    started = time.monotonic()
    exception_dist: dict[str, int] = {}

    summary, scan_dir, source_type, source_ref = _resolve_input(diff_text, files, repo_path, repo_root)

    payload, run = devrun.run_checks_subprocess(
        scan_dir,
        timeout=sandbox_timeout if sandbox_timeout is not None else devrun.DEFAULT_TIMEOUT_SEC,
        max_bytes=max_output_bytes if max_output_bytes is not None else devrun.MAX_OUTPUT_BYTES,
        policy=policy if policy is not None else ReviewPolicy())
    # The script always exits 0; a non-zero code means the harness itself failed.
    if run.timed_out or (not run.blocked and run.exit_code != 0):
        exception_dist["sandbox_failure"] = exception_dist.get("sandbox_failure", 0) + 1

    # missing_tests comes from the skill too — it reads the diff sidecar materialize_diff wrote.
    return _assemble(task_id,
                     summary,
                     skill_results.findings_from_payload(payload),
                     [run],
                     source_type,
                     source_ref,
                     started,
                     exception_dist,
                     warn_threshold,
                     review_threshold,
                     tool_calls=skill_results.tool_calls_from_payload(payload))


def _assemble(task_id,
              summary,
              raw,
              sandbox_runs,
              source_type,
              source_ref,
              started,
              exception_dist,
              warn_threshold,
              review_threshold,
              tool_calls: Optional[int] = None) -> ReviewResult:
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
        # How many scanners actually ran, as reported by the sandbox envelope itself — measured
        # where the work happened, so it cannot drift from the host PATH.
        "tool_calls": tool_calls or 0,
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


def assemble_from_skill_findings(
    *,
    task_id: str,
    summary: DiffSummary,
    payload: dict,
    source_type: str,
    source_ref: str,
    started: float,
    skill_run_result: Optional[dict] = None,
    sandbox_runs: Optional[list] = None,
) -> ReviewResult:
    """Build a review result from what the code-review Skill produced.

    This is the agent path's counterpart to ``run_review``: the findings already exist (the skill
    computed them in a sandbox), so this only does the host-side half — dedup/denoise, monitoring,
    and the redacted report.

    ``skill_run_result`` is the raw ``skill_run`` tool result; when given, its exit code, duration
    and output sizes become the report's sandbox-execution summary. ``sandbox_runs`` overrides that
    entirely, for the case where the guard Filter blocked the run before it ever reached a sandbox.
    """
    from . import skill_results

    runs = list(sandbox_runs) if sandbox_runs is not None else []
    if not runs and skill_run_result is not None:
        runs = [skill_results.sandbox_run_from_skill_result(skill_run_result)]

    exception_dist: dict[str, int] = {}
    for run in runs:
        if run.timed_out or (not run.blocked and run.exit_code != 0):
            exception_dist["sandbox_failure"] = exception_dist.get("sandbox_failure", 0) + 1

    return _assemble(task_id,
                     summary,
                     skill_results.findings_from_payload(payload),
                     runs,
                     source_type,
                     source_ref,
                     started,
                     exception_dist,
                     None,
                     None,
                     tool_calls=skill_results.tool_calls_from_payload(payload))


def _resolve_input(diff_text: Optional[str], files: Optional[list[str]], repo_path: Optional[str],
                   repo_root: str) -> tuple[DiffSummary, str, str, str]:
    """Materialize any of the three input modes into (summary, scan_dir, source_type, source_ref).

    Shared by every entry point so all three input modes reach the same skill script.
    """
    if diff_text is not None:
        summary, scan_dir = _materialize(diff_text)
        return summary, scan_dir, "diff_file", "<diff>"
    if files is not None:
        return diff_parser.parse_file_list(files, repo_root), _materialize_files(files, repo_root), \
            "file_list", ",".join(files)[:200]
    if repo_path is not None:
        return diff_parser.parse_git_worktree(repo_path), repo_path, "repo_path", repo_path
    raise ValueError("a review requires diff_text, files, or repo_path")



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
