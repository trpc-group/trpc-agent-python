# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""ReviewPipeline: the end-to-end orchestration of one review task.

Flow (each phase in a telemetry tracer span):
  1. persist task (status=running) + host-side diff parse → diff summary
  2. governance gate (Filter) → sandbox check run (or block, recorded)
  3. sandbox failure → host-fallback rule run (failure recorded, task survives)
  4. findings post-processing: dedup → noise split → secret redaction
  5. optional LLM summary (fake/real/off)
  6. persist findings / filter events / report; render json + md reports
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from trpc_agent_sdk.telemetry import tracer

from .config import ReviewConfig
from .diff_parser import build_diff_summary
from .diff_parser import parse_unified_diff
from .diff_parser import run_all_rules
from .findings import BUCKET_FINDING
from .findings import BUCKET_NEEDS_HUMAN_REVIEW
from .findings import Finding
from .findings import dedup_findings
from .findings import severity_distribution
from .findings import split_noise
from .governance import ACTION_ALLOW
from .governance import PolicyDecision
from .governance import SandboxGovernanceFilter
from .governance import SandboxRunRequest
from .governance import gated_sandbox_run
from .inputs import RawChangeSet
from .llm_summary import summarize
from .metrics import ReviewMetrics
from .redaction import SecretRedactor
from .report import build_report
from .report import write_reports
from .sandbox import STATUS_BLOCKED
from .sandbox import STATUS_OK
from .sandbox import SandboxExecutor
from .sandbox import SandboxRunOutcome
from .sandbox import create_sandbox_runtime
from .store import ReviewStore

STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
STATUS_FAILED = "failed"


@dataclass
class ReviewResult:
    """What the CLI gets back from one pipeline run."""

    task_id: str
    status: str
    report: Dict[str, Any]
    report_paths: Dict[str, str]
    metrics: ReviewMetrics
    findings: List[Finding] = field(default_factory=list)
    needs_human_review: List[Finding] = field(default_factory=list)


class ReviewPipeline:
    """Orchestrates skill rules, governed sandbox runs, storage and reporting."""

    def __init__(self, store: ReviewStore, config: ReviewConfig) -> None:
        self._store = store
        self._config = config

    async def run(self, changeset: RawChangeSet) -> ReviewResult:
        task_id = uuid.uuid4().hex
        metrics = ReviewMetrics()
        redactor = SecretRedactor()
        filter_events: List[Dict[str, Any]] = []
        sandbox_rows: List[Dict[str, Any]] = []
        start = time.perf_counter()
        status = STATUS_COMPLETED

        with tracer.start_as_current_span("code_review.total"):
            await self._store.create_task(
                task_id=task_id,
                input_type=changeset.input_type,
                input_ref=redactor.redact_str(changeset.input_ref),
                config=self._config.to_dict(),
            )
            await self._store.update_task(task_id, status="running")

            try:
                # Phase 1: host-side diff parsing (tool call #1).
                with tracer.start_as_current_span("code_review.parse"):
                    parsed = parse_unified_diff(changeset.unified_diff_text)
                    diff_summary = build_diff_summary(parsed)
                    metrics.tool_call_count += 1
                await self._store.update_task(task_id, diff_summary=diff_summary)

                # Phase 2+3: governed sandbox execution with host fallback.
                raw_findings, sandbox_status = await self._run_checks_governed(
                    task_id, parsed, changeset, metrics, redactor, filter_events, sandbox_rows)
                if sandbox_status != STATUS_OK:
                    status = STATUS_COMPLETED_WITH_ERRORS

                # Phase 4: dedup → noise split → redaction.
                findings, needs_review = self._postprocess_findings(raw_findings, metrics, redactor)

                # Phase 5: optional LLM summary.
                summary = await self._summarize(findings, needs_review, metrics, redactor)

                metrics.total_duration_ms = (time.perf_counter() - start) * 1000.0

                # Phase 6: persist + render.
                report = build_report(
                    task_id=task_id,
                    input_type=changeset.input_type,
                    input_ref=redactor.redact_str(changeset.input_ref),
                    status=status,
                    summary=summary,
                    findings=findings,
                    needs_human_review=needs_review,
                    # Public view: internal bookkeeping keys (e.g. _persisted)
                    # must not leak into the report document.
                    filter_events=[{key: value for key, value in event.items()
                                    if not key.startswith("_")}
                                   for event in filter_events],
                    sandbox_runs=[self._public_run_view(row) for row in sandbox_rows],
                    metrics=metrics,
                    diff_summary=diff_summary,
                )
                # Defense in depth: scrub the whole document once more.
                report = redactor.redact_obj(report)
                await self._persist_results(task_id, report, findings, needs_review, metrics)
                report_paths = write_reports(report, self._config.out_dir)
                await self._store.update_task(task_id, status=status)
                return ReviewResult(task_id=task_id, status=status, report=report,
                                    report_paths=report_paths, metrics=metrics,
                                    findings=findings, needs_human_review=needs_review)
            except Exception as ex:
                metrics.record_error(type(ex).__name__)
                await self._store.update_task(
                    task_id, status=STATUS_FAILED, error_type=type(ex).__name__,
                    error_message=redactor.redact_str(str(ex))[:2000])
                raise

    # -- phase helpers -------------------------------------------------------

    async def _run_checks_governed(self, task_id: str, parsed: Dict[str, Any],
                                   changeset: RawChangeSet, metrics: ReviewMetrics,
                                   redactor: SecretRedactor,
                                   filter_events: List[Dict[str, Any]],
                                   sandbox_rows: List[Dict[str, Any]]) -> tuple:
        """Governance gate → sandbox run → host fallback. Returns (findings, status)."""
        cfg = self._config
        runtime = create_sandbox_runtime(cfg.sandbox)
        executor = SandboxExecutor(runtime, cfg.sandbox)

        def on_decision(req: SandboxRunRequest, decision: PolicyDecision) -> None:
            metrics.record_filter_decision(decision.action)
            filter_events.append({
                "stage": "sandbox_gate",
                "target": req.args[0] if req.args else req.cmd,
                "action": decision.action,
                "rule": decision.rule,
                "reasons": list(decision.reasons),
            })

        governance = SandboxGovernanceFilter(cfg.policy, on_decision=on_decision)
        # The gate must see the exact command + full argv the sandbox will run.
        request = SandboxRunRequest(
            kind="static_checks",
            cmd=executor.check_cmd,
            args=executor.build_check_args(changeset.file_contents),
            script_host_path=executor.check_script_host_path,
            wants_network=False,
            est_timeout=cfg.sandbox.timeout_sec,
            run_index=metrics.sandbox_run_count,
            total_sandbox_seconds=metrics.sandbox_duration_ms / 1000.0,
        )

        sandbox_started = time.perf_counter()
        with tracer.start_as_current_span("code_review.sandbox"):
            outcome = await gated_sandbox_run(
                request, governance,
                handler=lambda: executor.run_checks(task_id, parsed, changeset.file_contents))
        sandbox_ms = (time.perf_counter() - sandbox_started) * 1000.0

        if isinstance(outcome, PolicyDecision):
            # Blocked before execution: record, then fall back to host rules
            # so the review itself still completes (the block is visible in
            # the report and the DB).
            row = self._make_run_row(task_id, metrics, request, None, STATUS_BLOCKED,
                                     filter_action=outcome.action, filter_reasons=outcome.reasons)
            sandbox_rows.append(row)
            await self._store.add_sandbox_run(dict(row))
            await self._add_filter_events(task_id, filter_events)
            findings = self._host_fallback_rules(parsed, changeset, metrics)
            return findings, STATUS_BLOCKED

        metrics.sandbox_run_count += 1
        metrics.tool_call_count += 1
        metrics.sandbox_duration_ms += outcome.duration_ms or sandbox_ms
        if outcome.error_type:
            metrics.record_error(outcome.error_type)

        row = self._make_run_row(task_id, metrics, request, outcome, outcome.status,
                                 filter_action=ACTION_ALLOW, filter_reasons=[],
                                 redactor=redactor)
        sandbox_rows.append(row)
        await self._store.add_sandbox_run(dict(row))
        await self._add_filter_events(task_id, filter_events)

        if outcome.status == STATUS_OK and outcome.findings_payload is not None:
            raw = outcome.findings_payload.get("findings", [])
            return [Finding.from_dict(item) for item in raw], STATUS_OK

        # Sandbox timeout/failure/error: the review must not crash — rerun the
        # same stdlib rule engine in-process (source stays static_rule; the
        # sandbox_run row keeps the failure evidence).
        findings = self._host_fallback_rules(parsed, changeset, metrics)
        return findings, outcome.status

    def _host_fallback_rules(self, parsed: Dict[str, Any], changeset: RawChangeSet,
                             metrics: ReviewMetrics) -> List[Finding]:
        metrics.tool_call_count += 1
        raw = run_all_rules(parsed, changeset.file_contents)
        return [Finding.from_dict(item) for item in raw]

    def _make_run_row(self, task_id: str, metrics: ReviewMetrics, request: SandboxRunRequest,
                      outcome: Optional[SandboxRunOutcome], run_status: str,
                      filter_action: str, filter_reasons: List[str],
                      redactor: Optional[SecretRedactor] = None) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "task_id": task_id,
            "run_index": max(0, metrics.sandbox_run_count - (1 if outcome else 0)),
            "kind": request.kind,
            "runtime_kind": self._config.sandbox.runtime_kind,
            "cmd": request.cmd,
            "args": list((outcome.args if outcome else None) or request.args),
            "duration_ms": outcome.duration_ms if outcome else 0.0,
            "exit_code": outcome.result.exit_code if outcome and outcome.result else None,
            "timed_out": bool(outcome.result.timed_out) if outcome and outcome.result else False,
            "status": run_status,
            "filter_action": filter_action,
            "filter_reasons": list(filter_reasons),
            "stdout_excerpt": "",
            "stderr_excerpt": "",
            "output_truncated": bool(outcome.output_truncated) if outcome else False,
            "error_type": outcome.error_type if outcome else "",
        }
        if outcome and redactor:
            row["stdout_excerpt"] = redactor.redact_str(outcome.stdout[:4000])
            row["stderr_excerpt"] = redactor.redact_str(outcome.stderr[:4000])
        return row

    async def _add_filter_events(self, task_id: str, filter_events: List[Dict[str, Any]]) -> None:
        for event in filter_events:
            if event.get("_persisted"):
                continue
            await self._store.add_filter_event({key: value for key, value in event.items()
                                                if not key.startswith("_")} | {"task_id": task_id})
            event["_persisted"] = True

    def _postprocess_findings(self, raw_findings: List[Finding], metrics: ReviewMetrics,
                              redactor: SecretRedactor) -> tuple:
        with tracer.start_as_current_span("code_review.postprocess"):
            deduped, removed = dedup_findings(raw_findings)
            metrics.deduplicated_count = removed
            findings, needs_review = split_noise(deduped, self._config.noise.min_confidence)
            for finding in findings + needs_review:
                finding.evidence = redactor.redact_str(finding.evidence)
                finding.title = redactor.redact_str(finding.title)
                finding.recommendation = redactor.redact_str(finding.recommendation)
            metrics.finding_count = len(findings)
            metrics.needs_human_review_count = len(needs_review)
            metrics.severity_distribution = severity_distribution(findings)
            metrics.redaction_count = redactor.redaction_count
            return findings, needs_review

    async def _summarize(self, findings: List[Finding], needs_review: List[Finding],
                         metrics: ReviewMetrics, redactor: SecretRedactor) -> str:
        if self._config.model_mode == "off":
            return ""
        digest = {
            "finding_count": len(findings),
            "needs_human_review_count": len(needs_review),
            "severity_distribution": severity_distribution(findings),
            "top_findings": [{
                "severity": finding.severity,
                "file": finding.file,
                "line": finding.line,
                "title": finding.title,
            } for finding in sorted(findings, key=lambda f: -f.severity_rank)[:5]],
        }
        try:
            with tracer.start_as_current_span("code_review.llm"):
                summary = await summarize(digest, self._config.model_mode, self._config.model_name)
            metrics.llm_call_count += 1
            metrics.tool_call_count += 1
            return redactor.redact_str(summary)
        except Exception as ex:  # pylint: disable=broad-except
            # Summary is auxiliary — its failure must not sink the review.
            metrics.record_error(type(ex).__name__)
            return f"(summary unavailable: {type(ex).__name__})"

    async def _persist_results(self, task_id: str, report: Dict[str, Any],
                               findings: List[Finding], needs_review: List[Finding],
                               metrics: ReviewMetrics) -> None:
        rows: List[Dict[str, Any]] = []
        for bucket, items in ((BUCKET_FINDING, findings), (BUCKET_NEEDS_HUMAN_REVIEW, needs_review)):
            for finding in items:
                rows.append({
                    "severity": finding.severity,
                    "category": finding.category,
                    "file": finding.file,
                    "line": finding.line,
                    "title": finding.title,
                    "evidence": finding.evidence,
                    "recommendation": finding.recommendation,
                    "confidence": finding.confidence,
                    "source": finding.source,
                    "rule_id": finding.rule_id,
                    "bucket": bucket,
                    "dedup_key": f"{finding.file}:{finding.line}:{finding.category}",
                })
        await self._store.add_findings(task_id, rows)
        await self._store.save_report(task_id, {
            "summary": report.get("summary", ""),
            "findings_total": len(findings),
            "severity_stats": report.get("severity_stats"),
            "filter_summary": report.get("filter_summary"),
            "sandbox_summary": {
                "total_runs": report.get("sandbox_summary", {}).get("total_runs", 0),
                "statuses": [run.get("status") for run in
                             report.get("sandbox_summary", {}).get("runs", [])],
            },
            "metrics": metrics.to_dict(),
            "report": report,
        })

    @staticmethod
    def _public_run_view(row: Dict[str, Any]) -> Dict[str, Any]:
        """Sandbox-run fields exposed in the report document."""
        keys = ("run_index", "kind", "runtime_kind", "cmd", "status", "filter_action",
                "filter_reasons", "exit_code", "timed_out", "duration_ms",
                "output_truncated", "error_type")
        return {key: row.get(key) for key in keys}
