# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Function tools for the code review agent.

Wraps the existing review pipeline as a FunctionTool so the LlmAgent
can invoke it during A2A/AG-UI service interactions.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from trpc_agent_sdk.tools import FunctionTool

# Ensure the parent package is importable (supports both direct and AgentEvaluator usage)
_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from config import ReviewAgentConfig
from progress import ProgressEvent, ProgressReporter, ReviewStage, print_progress_callback
from review_agent import run_review


async def run_code_review(
    diff_content: str,
    input_type: str = "diff_file",
    sandbox_type: str = "local",
    dry_run: bool = False,
    output_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Run a full code review on the given diff content.

    Parses the diff, runs filter governance, executes sandbox checks,
    deduplicates findings, and generates a structured review report.

    Args:
        diff_content: Unified diff content (the raw diff text) or a file path
                      to a diff file. When input_type is "fixture", this is
                      the fixture name (e.g. "01_clean").
        input_type: Type of input source. One of:
            - "diff_file": diff_content is raw diff text
            - "fixture": diff_content is a fixture name
        sandbox_type: Sandbox executor type. One of "local", "container", "cube".
        dry_run: If True, skip sandbox execution and LLM calls.
        output_dir: Directory for output reports. Defaults to a temp dir.

    Returns:
        A dictionary containing the review report with keys:
        - task_id: The review task ID
        - status: Task status ("completed", "failed")
        - finding_count: Total number of findings
        - severity_distribution: Dict of severity -> count
        - findings: List of finding dicts
        - warnings: List of warning dicts
        - needs_human_review: List of low-confidence findings
        - sandbox_runs: List of sandbox execution records
        - filter_intercepts: List of filter interception records
        - report_json_path: Path to the JSON report file
        - report_md_path: Path to the Markdown report file
        - error: Error message if the review failed
    """
    # Set up progress reporter
    reporter = ProgressReporter()
    reporter.on_progress(print_progress_callback)
    reporter.start()
    reporter.report(ReviewStage.INIT, "Initializing review pipeline...", 0.0)

    # Create a temporary directory for output if not specified
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="reviewmind_")

    # Write diff content to a temp file if it's raw text
    diff_path: Optional[str] = None
    fixture_name: Optional[str] = None

    if input_type == "fixture":
        fixture_name = diff_content
        reporter.report(ReviewStage.PARSE, f"Loading fixture '{diff_content}'...", 10.0)
    else:
        diff_path = os.path.join(output_dir, "input.diff")
        os.makedirs(os.path.dirname(diff_path), exist_ok=True)
        with open(diff_path, "w", encoding="utf-8") as f:
            f.write(diff_content)
        reporter.report(ReviewStage.PARSE, f"Parsing diff ({len(diff_content)} bytes)...", 15.0)

    # Build config
    reporter.report(ReviewStage.PARSE, "Building review configuration...", 20.0)
    config = ReviewAgentConfig(
        input_source="fixture" if fixture_name else "diff_file",
        input_value=fixture_name or diff_path or "",
        output_dir=output_dir,
        sandbox_type=sandbox_type,
        dry_run=dry_run,
        fake_model=dry_run,
        db_path=os.path.join(output_dir, "review.db"),
    )

    # Run the review pipeline
    reporter.report(ReviewStage.FILTER, "Running filter governance...", 30.0)
    report = run_review(config)
    if report is None:
        reporter.report(ReviewStage.FAILED, "Review pipeline failed", 100.0)
        return {
            "task_id": "",
            "status": "failed",
            "error": "Review pipeline returned no report",
        }

    reporter.report(ReviewStage.DEDUP, "Deduplicating findings...", 70.0)
    reporter.report(ReviewStage.COMPLETE, "Review complete!", 100.0)

    # Serialize the report to a dict
    report_dict = {
        "task_id": report.task.id,
        "status": report.task.status.value,
        "finding_count": len(report.findings),
        "warning_count": len(report.warnings),
        "needs_review_count": len(report.needs_human_review),
        "severity_distribution": (
            json.loads(report.monitor.severity_distribution)
            if report.monitor and report.monitor.severity_distribution
            else {}
        ),
        "findings": [f.model_dump() for f in report.findings],
        "warnings": [f.model_dump() for f in report.warnings],
        "needs_human_review": [f.model_dump() for f in report.needs_human_review],
        "sandbox_runs": [s.model_dump() for s in report.sandbox_runs],
        "filter_intercepts": [i.model_dump() for i in report.filter_intercepts],
        "report_json_path": report.report_path_json or "",
        "report_md_path": report.report_path_md or "",
    }

    return report_dict


# Create the FunctionTool instance
run_code_review_tool = FunctionTool(run_code_review)