# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""GraphAgent orchestration for the review pipeline.

Splits the review pipeline into a directed graph with nodes for each step:
  parse → filter → sandbox → classify → report → store

This enables conditional routing, error recovery, and observability
at each individual step.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import State
from trpc_agent_sdk.dsl.graph import StateGraph

from config import ReviewAgentConfig
from review_agent import (
    _build_static_check_script,
    _build_secret_detection_script,
    _mask_finding_secrets,
    classify_findings,
    detect_findings_by_pattern,
    generate_json_report,
    generate_markdown_report,
    mask_secrets,
    parse_diff,
    run_filter_governance,
    run_sandbox_script,
)
from storage.models import (
    FilterLog,
    Finding,
    FindingSeverity,
    MonitorSummary,
    ReportType,
    ReviewReport,
    ReviewResult,
    ReviewTask,
    SandboxRun,
    SandboxStatus,
    TaskStatus,
)
from storage.sqlite_repository import SqliteCrRepository


class ReviewState(State):
    """Shared state for the review pipeline graph.

    Each node reads from and writes to this state, allowing the
    graph to orchestrate the full pipeline.
    """

    # Configuration (set once at the start)
    config: Optional[ReviewAgentConfig] = None
    repo: Optional[SqliteCrRepository] = None
    start_time: float = 0.0

    # Pipeline data (populated by nodes)
    diff_content: str = ""
    task: Optional[ReviewTask] = None
    task_id: str = ""
    parsed_diff: Optional[dict[str, Any]] = None
    raw_findings: list[Finding] = []
    sandbox_runs: list[SandboxRun] = []
    filter_intercepts: list[FilterLog] = []
    classified: Optional[dict[str, list[Finding]]] = None
    all_findings: list[Finding] = []
    severity_distribution: dict[str, int] = {}
    total_duration: float = 0.0
    sandbox_duration: float = 0.0
    monitor: Optional[MonitorSummary] = None
    masked_findings: list[Finding] = []
    masked_warnings: list[Finding] = []
    masked_needs_review: list[Finding] = []
    json_path: str = ""
    md_path: str = ""
    result: Optional[ReviewResult] = None

    # Error tracking
    error_message: str = ""
    node_errors: list[str] = []


async def _create_task(state: ReviewState) -> dict[str, Any]:
    """Node 1: Create the review task in the database."""
    config = state.get("config")
    repo = SqliteCrRepository(config.db_path)

    task = ReviewTask(
        input_type=config.input_source,
        input_summary="{}",
        status=TaskStatus.RUNNING,
    )
    repo.create_task(task)
    repo.close()
    return {
        "task": task,
        "task_id": task.id,
        "start_time": time.time(),
    }


async def _read_input(state: ReviewState) -> dict[str, Any]:
    """Node 2: Read and parse the input diff."""
    config = state.get("config")
    task = state.get("task")
    repo = SqliteCrRepository(config.db_path)

    diff_content = ""
    if config.input_source == "fixture":
        fixture_path = Path(__file__).parent.parent / "evals" / "fixtures" / f"{config.input_value}.diff"
        if fixture_path.exists():
            diff_content = fixture_path.read_text(encoding="utf-8")
    elif config.input_source == "diff_file":
        diff_path = Path(config.input_value)
        if diff_path.exists():
            diff_content = diff_path.read_text(encoding="utf-8")
    else:
        diff_content = config.input_value

    if not diff_content:
        task.status = TaskStatus.FAILED
        task.error_message = "No diff content found"
        repo.update_task(task)
        repo.close()
        return {"error_message": "No diff content found"}

    parsed = parse_diff(diff_content)
    task.input_summary = json.dumps(parsed, ensure_ascii=False)
    repo.update_task(task)
    repo.close()
    return {
        "diff_content": diff_content,
        "parsed_diff": parsed,
        "task": task,
    }


async def _detect_patterns(state: ReviewState) -> dict[str, Any]:
    """Node 3: Run pattern-based detection on the diff."""
    diff_content = state.get("diff_content", "")
    task_id = state.get("task_id", "")
    raw_findings = detect_findings_by_pattern(diff_content, task_id)
    return {"raw_findings": raw_findings}


async def _run_sandbox(state: ReviewState) -> dict[str, Any]:
    """Node 4: Run sandbox scripts with filter governance."""
    config = state.get("config")
    task_id = state.get("task_id", "")
    diff_content = state.get("diff_content", "")
    repo = SqliteCrRepository(config.db_path)

    sandbox_runs: list[SandboxRun] = []
    all_filter_intercepts: list[FilterLog] = []

    if not config.dry_run:
        for script_name, builder in [
            ("scripts/run_static_check.py", _build_static_check_script),
            ("scripts/detect_secrets.py", _build_secret_detection_script),
        ]:
            script_content = builder(diff_content)
            flogs, allowed = run_filter_governance(script_content, script_name, task_id)
            for fl in flogs:
                repo.create_filter_log(fl)
            all_filter_intercepts.extend(flogs)

            if allowed:
                sb_run = run_sandbox_script(
                    script_name, script_content, task_id,
                    timeout=config.sandbox_timeout,
                    max_output=config.sandbox_max_output,
                    sandbox_type=config.sandbox_type,
                )
                sandbox_runs.append(sb_run)
            else:
                sandbox_runs.append(SandboxRun(
                    task_id=task_id,
                    script_name=script_name,
                    status=SandboxStatus.INTERCEPTED,
                    intercept_reason=flogs[-1].reason if flogs else "Filter denied",
                ))

    for sb in sandbox_runs:
        repo.create_sandbox_run(sb)
    repo.close()

    return {
        "sandbox_runs": sandbox_runs,
        "filter_intercepts": all_filter_intercepts,
    }


async def _classify_findings(state: ReviewState) -> dict[str, Any]:
    """Node 5: Classify, deduplicate, and store findings."""
    config = state.get("config")
    task_id = state.get("task_id", "")
    raw_findings = state.get("raw_findings", [])
    repo = SqliteCrRepository(config.db_path)

    classified = classify_findings(raw_findings)

    all_fs = classified["findings"] + classified["warnings"] + classified["needs_human_review"]
    for finding in all_fs:
        repo.create_finding(finding)

    severity_dist = {
        "critical": sum(1 for f in all_fs if f.severity == FindingSeverity.CRITICAL),
        "warning": sum(1 for f in all_fs if f.severity == FindingSeverity.WARNING),
        "suggestion": sum(1 for f in all_fs if f.severity == FindingSeverity.SUGGESTION),
    }
    repo.close()

    return {
        "classified": classified,
        "all_findings": all_fs,
        "severity_distribution": severity_dist,
    }


async def _generate_reports(state: ReviewState) -> dict[str, Any]:
    """Node 6: Generate and store reports."""
    config = state.get("config")
    task = state.get("task")
    repo = SqliteCrRepository(config.db_path)
    sandbox_runs = state.get("sandbox_runs", [])
    filter_intercepts = state.get("filter_intercepts", [])
    classified = state.get("classified", {})
    all_fs = state.get("all_findings", [])
    severity_dist = state.get("severity_distribution", {})
    severity_dist_json = json.dumps(severity_dist, ensure_ascii=False)
    start_time = state.get("start_time", time.time())
    total_duration = (time.time() - start_time) * 1000
    sandbox_duration = sum(s.duration_ms for s in sandbox_runs)

    # Update task as completed
    task.status = TaskStatus.COMPLETED
    task.total_duration_ms = total_duration
    task.finding_count = len(all_fs)
    task.severity_distribution = severity_dist_json
    repo.update_task(task)

    # Mask secrets
    masked_findings = _mask_finding_secrets(classified.get("findings", []))
    masked_warnings = _mask_finding_secrets(classified.get("warnings", []))
    masked_needs_review = _mask_finding_secrets(classified.get("needs_human_review", []))

    # Build monitor
    monitor = MonitorSummary(
        task_id=task.id,
        total_duration_ms=total_duration,
        sandbox_duration_ms=sandbox_duration,
        tool_call_count=1,
        intercept_count=len(filter_intercepts),
        finding_count=len(all_fs),
        severity_distribution=severity_dist_json,
        exception_types=json.dumps([]),
    )

    # Generate reports
    os.makedirs(config.output_dir, exist_ok=True)
    json_path = os.path.join(config.output_dir, "review_report.json")
    md_path = os.path.join(config.output_dir, "review_report.md")

    json_content = generate_json_report(
        task, masked_findings, masked_warnings, masked_needs_review,
        sandbox_runs, filter_intercepts, monitor,
    )
    md_content = generate_markdown_report(
        task, masked_findings, masked_warnings, masked_needs_review,
        sandbox_runs, filter_intercepts, monitor,
    )

    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json_content)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # Store reports and monitor
    repo.create_report(ReviewReport(
        task_id=task.id, report_type=ReportType.JSON, content=json_content,
        summary=json.dumps({"finding_count": len(all_fs), "severity_distribution": severity_dist}),
        monitoring_metrics=monitor.model_dump_json(),
    ))
    repo.create_report(ReviewReport(
        task_id=task.id, report_type=ReportType.MARKDOWN, content=md_content,
    ))
    repo.create_monitor_summary(monitor)
    repo.close()

    return {
        "task": task,
        "monitor": monitor,
        "total_duration": total_duration,
        "sandbox_duration": sandbox_duration,
        "masked_findings": masked_findings,
        "masked_warnings": masked_warnings,
        "masked_needs_review": masked_needs_review,
        "json_path": json_path,
        "md_path": md_path,
    }


async def _build_result(state: ReviewState) -> dict[str, Any]:
    """Node 7: Build the final ReviewResult."""
    return {
        "result": ReviewResult(
            task=state.get("task"),
            findings=state.get("masked_findings", []),
            warnings=state.get("masked_warnings", []),
            needs_human_review=state.get("masked_needs_review", []),
            sandbox_runs=state.get("sandbox_runs", []),
            filter_intercepts=state.get("filter_intercepts", []),
            monitor=state.get("monitor"),
            report_path_json=state.get("json_path", ""),
            report_path_md=state.get("md_path", ""),
        ),
        "status": "completed",
    }


async def _handle_error(state: ReviewState) -> dict[str, Any]:
    """Error handler node: record failure and close the repo."""
    error_msg = state.get("error_message", "Unknown error")
    task = state.get("task")
    repo = state.get("repo")

    if task and repo:
        try:
            task.status = TaskStatus.FAILED
            task.error_message = error_msg
            repo.update_task(task)
        except Exception:
            pass

    if repo:
        try:
            repo.close()
        except Exception:
            pass

    state["result"] = ReviewResult(
        task=task or ReviewTask(status=TaskStatus.FAILED, error_message=error_msg),
        findings=[], warnings=[], needs_human_review=[],
        sandbox_runs=[], filter_intercepts=[],
    )
    return {"status": "failed", "error": error_msg}


def create_review_graph(config: ReviewAgentConfig) -> GraphAgent:
    """Create a GraphAgent that orchestrates the full review pipeline.

    The graph executes nodes in sequence:
      create_task → read_input → detect_patterns → run_sandbox
      → classify_findings → generate_reports → build_result

    Each node writes to the shared ReviewState, enabling full observability
    and error recovery at each step.

    Args:
        config: ReviewAgentConfig with all settings for this run.

    Returns:
        A compiled GraphAgent ready to execute.
    """
    graph = StateGraph(ReviewState)

    # Add all nodes
    graph.add_node(
        "create_task", _create_task,
        config=NodeConfig(name="create_task", description="Create review task in DB"),
    )
    graph.add_node(
        "read_input", _read_input,
        config=NodeConfig(name="read_input", description="Read and parse input diff"),
    )
    graph.add_node(
        "detect_patterns", _detect_patterns,
        config=NodeConfig(name="detect_patterns", description="Run pattern-based detection"),
    )
    graph.add_node(
        "run_sandbox", _run_sandbox,
        config=NodeConfig(name="run_sandbox", description="Run sandbox scripts with filter governance"),
    )
    graph.add_node(
        "classify_findings", _classify_findings,
        config=NodeConfig(name="classify_findings", description="Deduplicate and classify findings"),
    )
    graph.add_node(
        "generate_reports", _generate_reports,
        config=NodeConfig(name="generate_reports", description="Generate and store reports"),
    )
    graph.add_node(
        "build_result", _build_result,
        config=NodeConfig(name="build_result", description="Build final ReviewResult"),
    )
    graph.add_node(
        "handle_error", _handle_error,
        config=NodeConfig(name="handle_error", description="Handle pipeline errors"),
    )

    # Wire edges: sequential execution
    graph.set_entry_point("create_task")
    graph.set_finish_point("build_result")

    graph.add_edge("create_task", "read_input")
    graph.add_edge("read_input", "detect_patterns")
    graph.add_edge("detect_patterns", "run_sandbox")
    graph.add_edge("run_sandbox", "classify_findings")
    graph.add_edge("classify_findings", "generate_reports")
    graph.add_edge("generate_reports", "build_result")

    # Compile and return
    compiled = graph.compile()
    return GraphAgent(
        name="review_pipeline",
        description="Orchestrates the code review pipeline as a directed graph",
        graph=compiled,
    )


async def run_review_via_graph(config: ReviewAgentConfig) -> Optional[ReviewResult]:
    """Run the review pipeline using graph-like node orchestration.

    Executes the 7 pipeline nodes in sequence, passing state between them.
    This demonstrates the task decomposition pattern without requiring the
    langgraph StateGraph runtime (which requires a checkpointer config).

    Args:
        config: ReviewAgentConfig with all settings.

    Returns:
        ReviewResult or None on failure.
    """
    state: ReviewState = {"config": config}

    try:
        # Execute nodes in sequence
        state.update(await _create_task(state))
        state.update(await _read_input(state))
        state.update(await _detect_patterns(state))
        state.update(await _run_sandbox(state))
        state.update(await _classify_findings(state))
        state.update(await _generate_reports(state))
        state.update(await _build_result(state))

        result = state.get("result")
        return result

    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)[:500]}"
        return ReviewResult(
            task=ReviewTask(status=TaskStatus.FAILED, error_message=error_msg),
            findings=[], warnings=[], needs_human_review=[],
            sandbox_runs=[], filter_intercepts=[],
        )
    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)[:500]}"
        return ReviewResult(
            task=ReviewTask(status=TaskStatus.FAILED, error_message=error_msg),
            findings=[], warnings=[], needs_human_review=[],
            sandbox_runs=[], filter_intercepts=[],
        )