# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end pipeline for the code-review example."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from .input_parser import parse_diff_file
from .input_parser import parse_file_list
from .input_parser import parse_fixture
from .input_parser import parse_repo_path
from .models import FilterEvent
from .models import Finding
from .models import FindingCategory
from .models import FindingSeverity
from .models import FindingSource
from .models import InputSummary
from .models import InputType
from .models import ReviewReport
from .models import ReviewTask
from .models import RuntimeKind
from .models import SandboxRun
from .models import TaskStatus
from .models import utc_now_iso
from .report import _route_findings
from .report import build_review_report
from .report import write_review_report
from .sandbox import run_rule_script
from .sanitizer import redact_mapping
from .sanitizer import redact_text
from .skill_loader import load_skill
from .store import ReviewStore
from .store import ReviewStoreFactory


@dataclass(frozen=True)
class ReviewPipelineConfig:
    """Configuration for one review pipeline run."""

    input_type: InputType
    input_ref: str
    output_dir: str | Path = "output"
    db_path: str | Path = "output/review.sqlite3"
    runtime: RuntimeKind = RuntimeKind.DRY_RUN
    allow_local: bool = False
    timeout_sec: float = 30.0
    output_limit_bytes: int = 65536
    container_image: str = "python:3-slim"
    docker_base_url: str = ""
    store_factory: ReviewStoreFactory = ReviewStore


@dataclass
class ReviewPipelineResult:
    """Materialized result of one review pipeline run."""

    task: ReviewTask
    report: ReviewReport
    input_summary: InputSummary
    findings: list[Finding]
    warnings: list[Finding]
    needs_human_review: list[Finding]
    filter_events: list[FilterEvent]
    sandbox_runs: list[SandboxRun]
    artifact_paths: dict[str, str] = field(default_factory=dict)


def run_review_pipeline(config: ReviewPipelineConfig) -> ReviewPipelineResult:
    """Run the complete deterministic review pipeline."""
    started = time.monotonic()
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    task = ReviewTask(input_type=config.input_type, input_ref=config.input_ref, status=TaskStatus.RUNNING)
    input_summary = _parse_input(config, task.id)
    input_summary.task_id = task.id
    loaded_skill = load_skill(Path(__file__).parents[1] / "skills" / "code-review")

    artifact_paths = _artifact_paths(output_dir)
    _write_json_atomic(Path(artifact_paths["parsed_input"]), redact_mapping(input_summary.to_dict()))
    _write_json_atomic(Path(artifact_paths["skill_manifest"]), redact_mapping(loaded_skill.manifest.to_dict()))

    sandbox_run, filter_events, rule_result = run_rule_script(
        task_id=task.id,
        runtime=config.runtime,
        allow_local=config.allow_local,
        input_path=artifact_paths["parsed_input"],
        manifest_path=artifact_paths["skill_manifest"],
        output_path=artifact_paths["rule_result"],
        timeout_sec=config.timeout_sec,
        output_limit_bytes=config.output_limit_bytes,
        container_image=config.container_image,
        docker_base_url=config.docker_base_url,
    )
    raw_findings = [_finding_from_dict(item) for item in rule_result.get("findings", []) if isinstance(item, dict)]
    _write_json_atomic(Path(artifact_paths["findings"]),
                       redact_mapping({"findings": [item.to_dict() for item in raw_findings]}))
    _write_json_atomic(Path(artifact_paths["filter_events"]),
                       redact_mapping({"filter_events": [event.to_dict() for event in filter_events]}))
    _write_json_atomic(Path(artifact_paths["sandbox_runs"]), redact_mapping({"sandbox_runs": [sandbox_run.to_dict()]}))

    total_duration_ms = int((time.monotonic() - started) * 1000)
    routed = _route_findings(raw_findings, filter_events, [sandbox_run])
    report = build_review_report(
        task_id=task.id,
        input_summary=input_summary,
        findings=raw_findings,
        filter_events=filter_events,
        sandbox_runs=[sandbox_run],
        total_duration_ms=total_duration_ms,
        routed=routed,
    )
    report_json, report_md = write_review_report(output_dir, report)
    artifact_paths["review_report_json"] = str(report_json)
    artifact_paths["review_report_md"] = str(report_md)
    artifact_paths["db_path"] = str(Path(config.db_path))
    task.status = TaskStatus.DONE
    task.summary = report.conclusion
    task.finished_at = utc_now_iso()
    routed_findings = routed.findings
    warnings = routed.warnings
    needs_human_review = routed.needs_human_review
    with config.store_factory(config.db_path) as store:
        store.save_review(
            task=task,
            input_summary=input_summary,
            findings=routed_findings,
            warnings=warnings,
            needs_human_review=needs_human_review,
            filter_events=filter_events,
            sandbox_runs=[sandbox_run],
            report=report,
            report_json_path=report_json,
            report_md_path=report_md,
        )
    return ReviewPipelineResult(
        task=task,
        report=report,
        input_summary=input_summary,
        findings=routed_findings,
        warnings=warnings,
        needs_human_review=needs_human_review,
        filter_events=filter_events,
        sandbox_runs=[sandbox_run],
        artifact_paths=artifact_paths,
    )


def _parse_input(config: ReviewPipelineConfig, task_id: str) -> InputSummary:
    if config.input_type is InputType.DIFF_FILE:
        return parse_diff_file(config.input_ref, task_id=task_id)
    if config.input_type is InputType.REPO_PATH:
        return parse_repo_path(config.input_ref, task_id=task_id)
    if config.input_type is InputType.FILE_LIST:
        return parse_file_list(config.input_ref, task_id=task_id)
    return parse_fixture(config.input_ref, task_id=task_id)


def _artifact_paths(output_dir: Path) -> dict[str, str]:
    return {
        "parsed_input": str(output_dir / "parsed_input.json"),
        "skill_manifest": str(output_dir / "skill_manifest.json"),
        "rule_result": str(output_dir / "rule_result.json"),
        "findings": str(output_dir / "findings.json"),
        "filter_events": str(output_dir / "filter_events.json"),
        "sandbox_runs": str(output_dir / "sandbox_runs.json"),
    }


def _finding_from_dict(payload: dict[str, Any]) -> Finding:
    try:
        severity = FindingSeverity(str(payload["severity"]))
    except (KeyError, ValueError):
        severity = FindingSeverity.MEDIUM
    try:
        category = FindingCategory(str(payload["category"]))
    except (KeyError, ValueError):
        category = FindingCategory.SANDBOX
    try:
        source = FindingSource(str(payload.get("source", FindingSource.RULE.value)))
    except ValueError:
        source = FindingSource.RULE
    return Finding(
        severity=severity,
        category=category,
        file=redact_text(str(payload.get("file", ""))),
        line=payload.get("line"),
        title=redact_text(str(payload.get("title", ""))),
        evidence=redact_text(str(payload.get("evidence", ""))),
        recommendation=redact_text(str(payload.get("recommendation", ""))),
        confidence=float(payload.get("confidence", 0.0)),
        source=source,
        fingerprint=str(payload.get("fingerprint") or ""),
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(redact_mapping(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
