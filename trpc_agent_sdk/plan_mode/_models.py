# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Data model for Plan Mode (session-scoped design + approval gate)."""

from __future__ import annotations

from enum import Enum
from typing import List
from typing import Literal
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class PlanStatus(str, Enum):
    """Lifecycle state of a session plan."""

    PENDING_ENTER = "pending_enter"
    EXPLORING = "exploring"
    DRAFTING = "drafting"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"


class PlanQuestion(BaseModel):
    """A structured clarification question and optional answer."""

    id: int
    question: str
    options: Optional[List[str]] = None
    answer: Optional[str] = None
    asked_at_unix: int = Field(alias="askedAtUnix")

    model_config = {"populate_by_name": True}


class PlanApproval(BaseModel):
    """Approval metadata for exit_plan_mode."""

    request_id: Optional[str] = Field(default=None, alias="requestId")
    reviewer_note: Optional[str] = Field(default=None, alias="reviewerNote")
    decided_at_unix: Optional[int] = Field(default=None, alias="decidedAtUnix")

    model_config = {"populate_by_name": True}


class PlanRecord(BaseModel):
    """A single session plan artifact (main agent session state)."""

    id: str
    status: PlanStatus
    objective: str
    content: str = ""
    content_revisions: int = Field(default=0, alias="contentRevisions")
    asked_questions: List[PlanQuestion] = Field(default_factory=list, alias="askedQuestions")
    approval: PlanApproval = Field(default_factory=PlanApproval)
    backend: Literal["state", "file"] = "state"
    file_path: Optional[str] = Field(default=None, alias="filePath")
    started_at_unix: int = Field(alias="startedAtUnix")
    branch: Optional[str] = None

    model_config = {"populate_by_name": True}

    def is_gate_active(self) -> bool:
        """True while read-only plan gate should block write tools."""
        return self.status in (
            PlanStatus.EXPLORING,
            PlanStatus.DRAFTING,
            PlanStatus.PENDING_APPROVAL,
        )
