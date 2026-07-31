"""Environment metadata and render/persist operations for OptimizationReport."""

from __future__ import annotations

import platform
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.version import __version__ as sdk_version

from .artifacts import AuditSink
from .configuration import ValidatedRunConfig
from .models import (
    ArtifactRecord,
    AttributionPair,
    CandidateProposal,
    ComparisonPair,
    CostSummary,
    Decision,
    GateDecision,
    InnerSplit,
    OptimizationReport,
    Reproducibility,
    RunError,
    SnapshotPair,
    SourceApplication,
)

_GIT_OBJECT_ID_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


@dataclass(frozen=True)
class ReportContext:
    """Static run facts shared by pre-apply, terminal and error reports."""

    validated: ValidatedRunConfig
    started_at: str
    started_clock: float
    reproducibility: Reproducibility
    baseline_prompts: dict[str, str]
    baseline_hashes: dict[str, str]


def create_report_context(
    validated: ValidatedRunConfig,
    *,
    started_at: str,
    started_clock: float,
    baseline_prompts: dict[str, str],
    baseline_hashes: dict[str, str],
    callback_spec: Optional[str],
    programmatic_component: bool,
) -> ReportContext:
    settings = validated.config.pipeline
    reproducibility = build_reproducibility(
        validated.root_dir,
        mode=settings.mode,
        config_path=validated.config_path,
        train_path=validated.train_path,
        validation_path=validated.validation_path,
        run_id=validated.run_id,
        apply_candidate=settings.apply_candidate,
        callback_spec=callback_spec,
        trace_fixture=validated.trace_fixture_path,
        trace_hash=validated.input_hashes.get("trace"),
        programmatic_component=programmatic_component,
        input_paths=validated.reproducibility_paths,
    )
    return ReportContext(
        validated=validated,
        started_at=started_at,
        started_clock=started_clock,
        reproducibility=reproducibility,
        baseline_prompts=baseline_prompts,
        baseline_hashes=baseline_hashes,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_reproducibility(
        repo_root: str,
        *,
        mode: str,
        config_path: str,
        train_path: str,
        validation_path: str,
        run_id: str,
        apply_candidate: bool,
        callback_spec: Optional[str] = None,
        trace_fixture: Optional[str] = None,
        trace_hash: Optional[str] = None,
        programmatic_component: bool = False,
        input_paths: tuple[str, ...] = (),
) -> Reproducibility:
    commit: Optional[str] = None
    dirty: Optional[bool] = None
    reason: Optional[str] = None
    git_root: Optional[Path] = None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip())
        git_root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        reason = "git_metadata_unavailable"
    # Git repositories may use SHA-1 or SHA-256 object IDs. ``rev-parse``
    # already verified that HEAD resolves; this check rejects malformed output.
    if reason is None and (commit is None or _GIT_OBJECT_ID_PATTERN.fullmatch(commit) is None):
        reason = "git_commit_invalid"
    elif reason is None and dirty:
        reason = "worktree_dirty"
    elif reason is None and programmatic_component:
        reason = "programmatic_custom_component"
    elif reason is None and mode == "live" and not callback_spec:
        reason = "live_callback_not_importable"
    elif reason is None and mode == "trace" and (not trace_fixture or not trace_hash):
        reason = "trace_fixture_not_pinned"
    elif reason is None and git_root is None:
        reason = "git_root_unavailable"
    if reason is None:
        for input_path in input_paths:
            resolved = Path(input_path).resolve()
            try:
                relative = resolved.relative_to(git_root)
            except ValueError:
                reason = "input_outside_git"
                break
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--",
                 relative.as_posix()],
                cwd=git_root,
                check=False,
                capture_output=True,
                text=True,
            )
            if tracked.returncode != 0:
                reason = "input_not_tracked"
                break
    reproducible = reason is None
    command = None
    if reproducible:
        if git_root is None:
            raise RuntimeError("reproducible replay requires a resolved Git root")

        def replay_path(path: str | Path) -> str:
            resolved = Path(path).resolve()
            try:
                return resolved.relative_to(git_root).as_posix()
            except ValueError:
                return str(resolved)

        args = [
            # Keep the report portable across checkouts; environment metadata
            # records the interpreter version used for the original run.
            "python",
            replay_path(Path(repo_root) / "run_pipeline.py"),
            "--mode",
            mode,
            "--config",
            replay_path(config_path),
            "--train",
            replay_path(train_path),
            "--validation",
            replay_path(validation_path),
            "--run-id",
            f"{run_id}-replay",
            "--apply-candidate" if apply_candidate else "--no-apply-candidate",
        ]
        if callback_spec:
            args.extend(("--call-agent", callback_spec))
        command = shlex.join(args)
    return Reproducibility(
        reproducible=reproducible,
        command=command,
        reason=reason,
        git_commit=commit,
        git_dirty=dirty,
    )


def environment_metadata() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "sdk": sdk_version,
        "platform": platform.platform(),
    }


def _report_path(path: str, root: Path) -> str:
    resolved = Path(path)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def build_optimization_report(
        context: ReportContext,
        *,
        status: Decision,
        stage: str,
        applied: bool,
        proposal: Optional[CandidateProposal],
        candidate_hashes: Optional[dict[str, str]],
        baseline: Optional[SnapshotPair],
        candidate: Optional[SnapshotPair],
        delta: Optional[ComparisonPair],
        baseline_attribution: Optional[AttributionPair],
        candidate_attribution: Optional[AttributionPair],
        inner: Optional[InnerSplit],
        cost: CostSummary,
        gate_decision: Optional[GateDecision],
        artifacts: tuple[ArtifactRecord, ...],
        errors: tuple[RunError, ...] = (),
) -> OptimizationReport:
    """Assemble report-model facts without rerunning aggregation or gate logic."""

    validated = context.validated
    settings = validated.config.pipeline
    report_root = Path(validated.root_dir)
    final_hashes = candidate_hashes if applied and candidate_hashes else context.baseline_hashes
    return OptimizationReport(
        run_id=validated.run_id,
        status=status,
        mode=settings.mode,
        stage=stage,
        started_at=context.started_at,
        finished_at=utc_now(),
        duration_seconds=time.monotonic() - context.started_clock,
        reproducibility=context.reproducibility,
        inputs={
            "hashes": validated.input_hashes,
            "auditHashes": validated.audit_hashes,
            "paths": {
                "config": _report_path(validated.config_path, report_root),
                "train": _report_path(validated.train_path, report_root),
                "validation": _report_path(validated.validation_path, report_root),
            },
            "environment": environment_metadata(),
            "seed": settings.seed,
            "adapterIdentity": validated.adapter_identity,
        },
        prompts={
            "baseline": context.baseline_prompts,
            "baselineHashes": context.baseline_hashes,
            "candidate": proposal.prompts if proposal else None,
            "candidateHashes": candidate_hashes,
        },
        baseline=baseline,
        candidate=candidate,
        delta=delta,
        failure_attribution=baseline_attribution,
        candidate_failure_attribution=candidate_attribution,
        optimization=({
            "proposal": proposal.model_dump(mode="json", by_alias=True),
            "innerSplit": inner.model_dump(mode="json", by_alias=True) if inner else None,
        } if proposal else None),
        cost=cost,
        gate_decision=gate_decision,
        source_application=SourceApplication(
            requested=settings.apply_candidate,
            applied=applied,
            baseline_hashes=context.baseline_hashes,
            final_hashes=final_hashes,
        ),
        artifacts=artifacts,
        errors=errors,
    )


def render_markdown(report: OptimizationReport) -> str:
    """Render only report-model facts; no business aggregation occurs here."""

    lines = [
        f"# Optimization Report: {report.status.value}",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Mode: `{report.mode}`",
        f"- Final stage: `{report.stage}`",
        f"- Duration: `{report.duration_seconds:.3f}s`",
        f"- Reproducible: `{'yes' if report.reproducibility.reproducible else 'no'}`",
        f"- Source prompt applied: `{'yes' if report.source_application.applied else 'no'}`",
    ]
    if (report.baseline and report.candidate and report.baseline.train and report.baseline.validation
            and report.candidate.train and report.candidate.validation):
        lines.extend([
            "",
            "## Regression",
            "",
            "| Split | Baseline score | Candidate score | Baseline pass rate | Candidate pass rate |",
            "|---|---:|---:|---:|---:|",
            (f"| train | {report.baseline.train.dataset_score:.4f} | "
             f"{report.candidate.train.dataset_score:.4f} | "
             f"{report.baseline.train.pass_rate:.4f} | {report.candidate.train.pass_rate:.4f} |"),
            (f"| validation | {report.baseline.validation.dataset_score:.4f} | "
             f"{report.candidate.validation.dataset_score:.4f} | "
             f"{report.baseline.validation.pass_rate:.4f} | "
             f"{report.candidate.validation.pass_rate:.4f} |"),
        ])
    if report.delta and report.delta.validation:
        lines.extend(["", "## Validation Transitions", ""])
        for case in report.delta.validation.cases:
            lines.append(f"- `{case.case_id}`: `{case.transition.value}` ({case.delta:+.4f})")
    if report.gate_decision:
        failed = [check.code for check in report.gate_decision.checks if not check.passed]
        lines.extend([
            "",
            "## Gate",
            "",
            f"- Decision: `{report.gate_decision.decision.value}`",
            f"- Failed checks: `{', '.join(failed) if failed else 'none'}`",
            f"- Reasons: `{', '.join(report.gate_decision.reasons) if report.gate_decision.reasons else 'none'}`",
            ("- Overfit detected: `yes`"
             if "OVERFIT_TRAIN_UP_VALIDATION_DOWN" in report.gate_decision.reasons else "- Overfit detected: `no`"),
        ])
    if report.cost:
        cost = "unknown" if report.cost.total_cost_usd is None else f"${report.cost.total_cost_usd:.6f}"
        lines.extend(["", "## Cost", "", f"- Total: `{cost}`"])
    if report.errors:
        lines.extend(["", "## Errors", ""])
        for error in report.errors:
            lines.append(f"- `{error.stage}/{error.error_type}`: {error.message}")
    return "\n".join(lines) + "\n"


def persist_report(sink: AuditSink, report: OptimizationReport) -> None:
    sink.write_json("optimization_report.json", report.model_dump(mode="json", by_alias=True))
    sink.write_text("optimization_report.md", render_markdown(report))


def persist_terminal_report(
    sink: AuditSink,
    report: OptimizationReport,
    *,
    duration_seconds: float,
) -> OptimizationReport:
    """Commit one authoritative terminal report and publish its latest snapshots."""

    completed = OptimizationReport.model_validate({
        **report.model_dump(mode="python", by_alias=False),
        "finished_at":
        utc_now(),
        "duration_seconds":
        duration_seconds,
    })
    persist_report(sink, completed)
    sink.write_manifest()
    sink.publish_latest_snapshot("optimization_report.json", "optimization_report.json")
    sink.publish_latest_snapshot("optimization_report.md", "optimization_report.md")
    return completed
