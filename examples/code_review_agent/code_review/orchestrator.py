"""End-to-end local review orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .context_builder import ContextBudget, ReviewContext, build_review_context
from .git_diff import GitDiffCollector
from .models import (
    ChangedFile,
    ReviewOutput,
    ReviewRun,
    ReviewStatus,
    stable_config_hash,
)
from .policy import apply_finding_policy
from .static_analysis import StaticAnalysisResult

ReviewCallable = Callable[[ReviewContext], Awaitable[ReviewOutput]]
StaticAnalysisCallable = Callable[[Path, list[ChangedFile]], Awaitable[StaticAnalysisResult]]


@dataclass(frozen=True)
class ReviewConfig:
    """Configuration that affects deterministic review results."""

    context_lines: int = 3
    use_merge_base: bool = True
    max_files: int = 40
    max_patch_chars_per_file: int = 24_000
    max_total_chars: int = 120_000
    minimum_confidence: float = 0.0

    def context_budget(self) -> ContextBudget:
        return ContextBudget(
            max_files=self.max_files,
            max_patch_chars_per_file=self.max_patch_chars_per_file,
            max_total_chars=self.max_total_chars,
        )


async def run_review(
    *,
    repository: str | Path,
    base_revision: str,
    head_revision: str,
    config: ReviewConfig | None = None,
    reviewer: ReviewCallable | None = None,
    static_analyzer: StaticAnalysisCallable | None = None,
    model_name: str = "",
    execution_config: dict[str, Any] | None = None,
    repository_identity: str | None = None,
) -> ReviewRun:
    """Run a local review; omit reviewer for deterministic no-LLM mode."""
    config = config or ReviewConfig()
    repository_path = Path(repository).expanduser().resolve()
    identity = repository_identity or str(repository_path)
    config_hash = stable_config_hash({
        "review": asdict(config),
        "execution": execution_config or {},
    })
    provisional_idempotency_key = stable_config_hash({
        "repository_identity": identity,
        "base_revision": base_revision,
        "head_revision": head_revision,
        "config_hash": config_hash,
        "model_name": model_name,
    })
    review_run = ReviewRun(
        id=str(uuid4()),
        repository_path=identity,
        base_revision=base_revision,
        head_revision=head_revision,
        effective_base_revision="",
        status=ReviewStatus.RUNNING,
        model_name=model_name,
        config_hash=config_hash,
        idempotency_key=provisional_idempotency_key,
    )
    try:
        collector = GitDiffCollector(repository_path, context_lines=config.context_lines)
        effective_base, changed_files = collector.collect(
            base_revision,
            head_revision,
            use_merge_base=config.use_merge_base,
        )
        resolved_head = collector.resolve_revision(head_revision)
        review_run.effective_base_revision = effective_base
        review_run.resolved_head_revision = resolved_head
        review_run.idempotency_key = stable_config_hash({
            "repository_identity": identity,
            "effective_base_revision": effective_base,
            "resolved_head_revision": resolved_head,
            "config_hash": config_hash,
            "model_name": model_name,
        })
        review_run.changed_files = changed_files
        context = build_review_context(changed_files, config.context_budget())
        review_run.diagnostics.extend(context.diagnostics)

        static_result = StaticAnalysisResult()
        if static_analyzer is not None:
            review_run.static_analysis_requested = True
            static_result = await static_analyzer(repository_path, changed_files)
            review_run.analyzer_executions = static_result.executions
            review_run.diagnostics.extend(static_result.diagnostics)
            context = ReviewContext(
                text=context.text,
                included_files=context.included_files,
                skipped_files=context.skipped_files,
                truncated_files=context.truncated_files,
                diagnostics=context.diagnostics,
                static_analysis=static_result.prompt_text(),
            )

        if reviewer is None:
            llm_output = ReviewOutput(summary=(f"Collected {len(changed_files)} changed file(s); "
                                               "LLM review was disabled."))
        elif not context.included_files:
            llm_output = ReviewOutput(summary="No textual changed files were eligible for LLM review.")
        else:
            llm_output = await reviewer(context)

        summary_parts = [llm_output.summary.strip()]
        if static_analyzer is not None:
            summary_parts.append(f"Static analysis produced {len(static_result.findings)} finding(s) "
                                 f"from {len(static_result.executions)} tool run(s).")
        raw_output = ReviewOutput(
            summary=" ".join(part for part in summary_parts if part),
            findings=[*static_result.findings, *llm_output.findings],
        )

        normalized_output, diagnostics = apply_finding_policy(
            raw_output,
            changed_files,
            minimum_confidence=config.minimum_confidence,
        )
        review_run.output = normalized_output
        review_run.diagnostics.extend(diagnostics)
        review_run.status = ReviewStatus.COMPLETED
    except Exception as exc:  # noqa: BLE001 - report failures as review artifacts
        review_run.status = ReviewStatus.FAILED
        review_run.error_message = str(exc)
        review_run.diagnostics.append(f"Review failed: {exc}")
    finally:
        review_run.finished_at = datetime.now(timezone.utc)
    return review_run
