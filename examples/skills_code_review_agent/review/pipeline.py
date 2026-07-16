# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic review pipeline: parse -> govern -> sandbox -> merge -> persist -> report."""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from trpc_agent_sdk.skills import create_default_skill_repository

from agent.agent import create_review_agent
from filters_cr.governance_filter import GovernanceToolFilter
from storage.store import ReviewStore

from .findings import Finding, dedupe, gate, severity_distribution
from .governance import DEFAULT_ALLOWED_SCRIPTS, GovernanceEngine
from .llm_review import run_llm_review
from .report import build_report, write_reports
from .sandbox import DIFF_WS_PATH, SandboxSession, create_runtime

DEFAULT_SKILL_ROOT = str(Path(__file__).resolve().parents[1] / "skills" / "code-review")

CHECKERS = [
    ("check_security.py", "security"),
    ("check_async_leak.py", "async_resource_leak"),
    ("check_db_lifecycle.py", "db_lifecycle"),
    ("check_tests_missing.py", "missing_test"),
    ("check_secrets.py", "secret_leak"),
]


@dataclass
class ReviewOptions:
    diff_text: str
    input_type: str
    input_ref: str
    runtime: str = "local"
    dry_run: bool = True
    db_url: str = "sqlite:///code_review.db"
    output_dir: str = "out"
    skill_root: str = DEFAULT_SKILL_ROOT
    checkers: list = field(default_factory=lambda: list(CHECKERS))
    allowed_scripts: tuple = DEFAULT_ALLOWED_SCRIPTS
    timeout_sec: float = 60.0
    enable_llm: bool = True


@dataclass
class ReviewResult:
    task_id: str
    report: dict
    json_path: str
    md_path: str


def _parse_script_findings(stdout: str, warnings: list) -> list[Finding]:
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        warnings.append("checker produced invalid JSON output")
        return []
    findings = []
    for raw in payload.get("findings", []):
        try:
            findings.append(Finding(**raw))
        except (TypeError, ValueError):
            warnings.append(f"skipped malformed finding: {raw!r}")
    return findings


async def run_review(opts: ReviewOptions) -> ReviewResult:
    """Run the full review. Sandbox failures degrade, never crash."""
    start = time.monotonic()
    store = ReviewStore(db_url=opts.db_url)
    engine = GovernanceEngine(allowed_scripts=opts.allowed_scripts)
    task_id = await store.create_task(opts.input_type, opts.input_ref,
                                      opts.runtime, opts.dry_run)
    warnings: list[str] = []
    filter_events = []
    sandbox_outcomes = []
    raw_findings: list[Finding] = []
    diff_summary: dict = {}
    error_distribution: dict[str, int] = {}
    sandbox_ms = 0
    try:
        runtime = await create_runtime(opts.runtime)
        if opts.runtime == "local":
            warnings.append("local runtime is a development fallback; "
                            "use container or cube in production")
        session = SandboxSession(runtime, opts.skill_root, timeout_sec=opts.timeout_sec)
        await session.open(f"cr_{task_id[:12]}")
        try:
            await session.put_diff(opts.diff_text)
            scripts = [("parse_diff.py", "parse")] + list(opts.checkers)
            for script, category in scripts:
                decision = engine.check_script(script, [DIFF_WS_PATH])
                filter_events.append(decision)
                await store.add_filter_event(task_id, decision.target, decision.decision,
                                             decision.rule, decision.reason)
                if decision.decision != "allow":
                    continue
                outcome = await session.run_script(script)
                engine.record_run(outcome.duration_ms / 1000.0)
                sandbox_ms += outcome.duration_ms
                sandbox_outcomes.append(outcome)
                await store.add_sandbox_run(
                    task_id, script=script, category=category, status=outcome.status,
                    exit_code=outcome.exit_code, duration_ms=outcome.duration_ms,
                    timed_out=outcome.timed_out, stdout_summary=outcome.stdout[:4096],
                    stderr_summary=outcome.stderr[:4096], error_type=outcome.error_type)
                if outcome.status != "ok":
                    key = outcome.error_type or outcome.status
                    error_distribution[key] = error_distribution.get(key, 0) + 1
                    warnings.append(f"{script} did not complete ({outcome.status}); "
                                    "coverage degraded")
                    continue
                if script == "parse_diff.py":
                    try:
                        diff_summary = json.loads(outcome.stdout).get("summary", {})
                    except (json.JSONDecodeError, ValueError):
                        warnings.append("parse_diff.py produced invalid JSON")
                else:
                    raw_findings.extend(_parse_script_findings(outcome.stdout, warnings))
        finally:
            await session.close()

        llm_findings: list[Finding] = []
        llm_summary = ""
        llm_calls = 0
        if opts.enable_llm:
            repository = create_default_skill_repository(
                str(Path(opts.skill_root).parent), workspace_runtime=runtime)
            gov_filter = GovernanceToolFilter(engine, on_event=filter_events.append)
            agent = create_review_agent(repository, opts.dry_run, [gov_filter])
            llm_findings, llm_summary, llm_warnings = await run_llm_review(
                agent, opts.diff_text, raw_findings)
            warnings.extend(llm_warnings)
            llm_calls = 1

        kept, dropped = dedupe(raw_findings + llm_findings)
        reported, needs_review = gate(kept)
        await store.add_findings(task_id, reported, status="reported")
        await store.add_findings(task_id, needs_review, status="needs_human_review")
        await store.add_findings(task_id, dropped, status="deduped")

        intercepts = sum(1 for e in filter_events if e.decision != "allow")
        metrics = {
            "total_duration_ms": int((time.monotonic() - start) * 1000),
            "sandbox_duration_ms": sandbox_ms,
            "tool_calls": len(sandbox_outcomes) + llm_calls,
            "intercepts": intercepts,
            "findings_total": len(reported),
            "severity_distribution": severity_distribution(reported),
            "error_distribution": error_distribution,
        }
        await store.add_metrics(task_id, metrics)

        report = build_report(
            task_id=task_id, input_ref=opts.input_ref, runtime=opts.runtime,
            dry_run=opts.dry_run, diff_summary=diff_summary, reported=reported,
            needs_review=needs_review, deduped_count=len(dropped),
            filter_events=filter_events, sandbox_outcomes=sandbox_outcomes,
            metrics=metrics, llm_summary=llm_summary, warnings=warnings)
        json_path, md_path = write_reports(report, opts.output_dir)
        await store.add_report(task_id, report, Path(md_path).read_text(encoding="utf-8"))
        await store.update_task(task_id, status="completed",
                                diff_summary=diff_summary, finished=True)
        return ReviewResult(task_id=task_id, report=report,
                            json_path=json_path, md_path=md_path)
    except Exception:
        await store.update_task(task_id, status="failed", finished=True)
        raise
    finally:
        await store.close()
