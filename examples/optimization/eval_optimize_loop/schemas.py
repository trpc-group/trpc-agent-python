# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Serializable data schemas owned by the pipeline example.

The SDK evaluation result types remain the source of truth for raw evaluation
data. These schemas capture run inputs, prompt provenance, fake candidates,
and the full stage-two evaluation outputs consumed by later pipeline phases.
"""

from __future__ import annotations

from typing import Any
from typing import Literal
from typing import Optional

from pydantic import Field
from pydantic import model_validator

from trpc_agent_sdk.evaluation import EvalBaseModel
from trpc_agent_sdk.evaluation import EvalCaseResult


FakeCandidateScenario = Literal["improve", "no_improvement", "overfit"]


class ObservableValue(EvalBaseModel):
    """A measurement whose absence is explicit rather than silently zero."""

    status: Literal["available", "unavailable"]
    value: Optional[float] = None
    unit: Optional[str] = None
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _validate_status(self) -> "ObservableValue":
        if self.status == "available" and self.value is None:
            raise ValueError("available observable values require value")
        if self.status == "unavailable" and self.value is not None:
            raise ValueError("unavailable observable values must not carry a value")
        return self


class PromptSnapshot(EvalBaseModel):
    """Content and provenance of one source prompt field at preparation time."""

    field_name: str
    source_path: str
    working_path: str
    content: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class InputSnapshot(EvalBaseModel):
    """Immutable file identities captured before a pipeline run starts."""

    pipeline_config_path: str
    pipeline_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    optimizer_config_path: str
    optimizer_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    train_evalset_path: str
    train_evalset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_evalset_path: str
    validation_evalset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_snapshots: list[PromptSnapshot]
    seed: int


class WorkspaceSnapshot(EvalBaseModel):
    """Directory layout created for one isolated pipeline run."""

    run_id: str
    run_dir: str
    workspace_dir: str
    prompts_dir: str


class FakeCandidateProposal(EvalBaseModel):
    """One deterministic prompt proposal produced without a real optimizer."""

    scenario: FakeCandidateScenario
    prompts: dict[str, str]
    changed_fields: list[str]
    rationale: str
    seed: int
    parent_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: str = Field(pattern=r"^fake-(improve|no_improvement|overfit)-[0-9a-f]{12}$")


class FakeEvaluationSnapshot(EvalBaseModel):
    """Complete SDK outputs from one fake-agent dataset evaluation."""

    phase: Literal["baseline", "candidate"]
    split: Literal["train", "validation"]
    eval_set_id: str
    failed_summary: Optional[dict[str, Any]] = None
    details_lines: list[str] = Field(
        description=(
            "SDK detailed-output lines; intentionally empty in stage two because "
            "print_detailed_results is disabled."
        )
    )
    result_lines: list[str]
    eval_results_by_eval_id: dict[str, list[EvalCaseResult]]
    passed_case_count: int = Field(ge=0)
    total_case_count: int = Field(ge=0)
    average_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Arithmetic mean of every available overall metric score across "
            "all cases, configured runs, and metrics."
        ),
    )


class FakeStageResult(EvalBaseModel):
    """The four full evaluations and candidate metadata produced in stage two."""

    scenario: FakeCandidateScenario
    candidate: FakeCandidateProposal
    baseline_train: FakeEvaluationSnapshot
    baseline_validation: FakeEvaluationSnapshot
    candidate_train: FakeEvaluationSnapshot
    candidate_validation: FakeEvaluationSnapshot
