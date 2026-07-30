"""No-LLM and fake-reviewer integration tests."""

from pathlib import Path

import pytest

from examples.code_review_agent.code_review.models import (
    AnalyzerExecution,
    AnalyzerStatus,
    Finding,
    ReviewOutput,
    ReviewStatus,
    Severity,
)
from examples.code_review_agent.code_review.orchestrator import run_review
from examples.code_review_agent.code_review.reporter import render_markdown
from examples.code_review_agent.code_review.static_analysis import StaticAnalysisResult


@pytest.mark.asyncio
async def test_no_llm_pipeline_collects_diff(sample_repository: tuple[Path, str, str], ) -> None:
    repository, base, head = sample_repository

    review_run = await run_review(
        repository=repository,
        base_revision=base,
        head_revision=head,
    )

    assert review_run.status == ReviewStatus.COMPLETED
    assert len(review_run.changed_files) == 2
    assert review_run.output.findings == []
    assert "LLM review was disabled" in review_run.output.summary
    assert "# Code Review Report" in render_markdown(review_run)


@pytest.mark.asyncio
async def test_fake_reviewer_output_is_line_validated(sample_repository: tuple[Path, str, str], ) -> None:
    repository, base, head = sample_repository

    async def reviewer(_context):
        assert "L3:         return None" in _context.text
        return ReviewOutput(
            summary="Found one issue.",
            findings=[
                Finding(
                    rule_id="python.correctness.none-sentinel",
                    severity=Severity.MEDIUM,
                    confidence=0.88,
                    category="correctness",
                    file_path="app.py",
                    start_line=3,
                    title="Ambiguous zero-division result",
                    description="Returning None changes the function contract.",
                    suggestion="Raise a documented exception or return a typed result.",
                )
            ],
        )

    review_run = await run_review(
        repository=repository,
        base_revision=base,
        head_revision=head,
        reviewer=reviewer,
        model_name="fake-model",
    )

    assert review_run.status == ReviewStatus.COMPLETED
    assert review_run.output.findings[0].publishable is True


@pytest.mark.asyncio
async def test_static_and_llm_findings_share_the_same_policy(sample_repository: tuple[Path, str, str], ) -> None:
    repository, base, head = sample_repository

    async def static_analyzer(_repository, _changed_files):
        return StaticAnalysisResult(
            findings=[
                Finding(
                    rule_id="ruff.f821",
                    severity=Severity.MEDIUM,
                    confidence=0.99,
                    category="correctness",
                    file_path="app.py",
                    start_line=3,
                    title="Undefined name",
                    description="The new line references an undefined name.",
                    source="static:ruff",
                )
            ],
            executions=[
                AnalyzerExecution(
                    tool="ruff",
                    runtime="fake",
                    command=["ruff"],
                    status=AnalyzerStatus.FINDINGS,
                    exit_code=1,
                    findings_count=1,
                )
            ],
        )

    review_run = await run_review(
        repository=repository,
        base_revision=base,
        head_revision=head,
        static_analyzer=static_analyzer,
    )

    assert review_run.status == ReviewStatus.COMPLETED
    assert review_run.output.findings[0].source == "static:ruff"
    assert review_run.output.findings[0].publishable is True
    assert review_run.analyzer_executions[0].tool == "ruff"
