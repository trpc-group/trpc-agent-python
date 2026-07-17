# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Plan state-machine transitions (in-memory; caller persists)."""

from __future__ import annotations

import uuid
from typing import Optional
from typing import Tuple

from ._models import PlanQuestion
from ._models import PlanRecord
from ._models import PlanStatus


def apply_enter(
    existing: Optional[PlanRecord],
    *,
    objective: str,
    now_unix: int,
) -> Tuple[Optional[PlanRecord], Optional[str]]:
    """Create a new plan in ``exploring`` state."""
    if existing is not None and existing.is_gate_active():
        return None, (f"a plan is already active (status={existing.status.value}); "
                      "wait for approval or finish the current plan before entering again")
    if existing is not None and existing.status == PlanStatus.PENDING_ENTER:
        return None, "enter plan mode request already pending user confirmation"
    record = PlanRecord(
        id=uuid.uuid4().hex,
        status=PlanStatus.EXPLORING,
        objective=objective,
        started_at_unix=now_unix,
    )
    return record, None


def apply_request_enter(
    existing: Optional[PlanRecord],
    *,
    objective: str,
    request_id: str,
    now_unix: int,
) -> Tuple[Optional[PlanRecord], Optional[str], Optional[dict]]:
    """Stage Plan Mode entry and build HITL payload for human confirmation."""
    if existing is not None and existing.is_gate_active():
        return None, (f"a plan is already active (status={existing.status.value}); "
                      "wait for approval or finish the current plan before entering again"), None
    if existing is not None and existing.status == PlanStatus.PENDING_ENTER:
        return None, "enter plan mode request already pending user confirmation", None

    record = PlanRecord(
        id=uuid.uuid4().hex,
        status=PlanStatus.PENDING_ENTER,
        objective=objective,
        started_at_unix=now_unix,
    )
    record.approval.request_id = request_id
    payload = {
        "status": "pending_enter",
        "message": f"Request to enter Plan Mode: {objective}",
        "objective": objective,
        "plan_id": record.id,
        "approval_id": request_id,
        "timestamp": now_unix,
    }
    return record, None, payload


def apply_enter_decision(
    existing: Optional[PlanRecord],
    *,
    decision: str,
    reviewer_note: str,
    now_unix: int,
) -> Tuple[Optional[PlanRecord], Optional[str], Optional[dict]]:
    """Apply human approve / reject after enter_plan_mode HITL."""
    if existing is None:
        return None, "no plan exists", None
    if existing.status != PlanStatus.PENDING_ENTER:
        return None, f"plan is not pending enter confirmation (status={existing.status.value})", None

    existing.approval.reviewer_note = reviewer_note or None
    existing.approval.decided_at_unix = now_unix

    if decision == "approved":
        existing.status = PlanStatus.EXPLORING
        return existing, None, {
            "status":
            "approved",
            "message": ("User confirmed Plan Mode. Explore read-only, draft the plan, "
                        "then call exit_plan_mode for implementation approval."),
            "plan":
            existing.model_dump(mode="json", by_alias=True),
        }

    if decision == "rejected":
        note = reviewer_note or "User declined to enter Plan Mode."
        return None, None, {
            "status": "rejected",
            "message": note,
        }

    return None, f"unknown decision={decision!r}; expected 'approved' or 'rejected'", None


def apply_update_content(
    existing: Optional[PlanRecord],
    *,
    content: str,
    mode: str,
    now_unix: int,
) -> Tuple[Optional[PlanRecord], Optional[str]]:
    """Append or replace plan content; moves exploring → drafting."""
    if existing is None:
        return None, "no active plan; call enter_plan_mode first"
    if existing.status not in (PlanStatus.EXPLORING, PlanStatus.DRAFTING):
        return None, f"cannot edit plan content in status={existing.status.value}"
    if mode == "replace":
        existing.content = content
    elif mode == "append":
        if existing.content and not existing.content.endswith("\n"):
            existing.content += "\n"
        existing.content += content
    else:
        return None, "mode must be 'append' or 'replace'"
    existing.content_revisions += 1
    if existing.status == PlanStatus.EXPLORING:
        existing.status = PlanStatus.DRAFTING
    return existing, None


def apply_request_exit(
    existing: Optional[PlanRecord],
    *,
    summary: str,
    request_id: str,
    now_unix: int,
) -> Tuple[Optional[PlanRecord], Optional[str], Optional[dict]]:
    """Move to pending_approval and build HITL payload."""
    if existing is None:
        return None, "no active plan; call enter_plan_mode first", None
    if existing.status not in (PlanStatus.EXPLORING, PlanStatus.DRAFTING):
        return None, f"cannot exit plan mode from status={existing.status.value}", None
    if not existing.content.strip():
        return None, "plan content is empty; write the plan before calling exit_plan_mode", None

    existing.status = PlanStatus.PENDING_APPROVAL
    existing.approval.request_id = request_id
    payload = {
        "status": "pending_approval",
        "message": summary or "Plan ready for human review.",
        "plan_id": existing.id,
        "objective": existing.objective,
        "content": existing.content,
        "preview": existing.content[:2000],
        "approval_id": request_id,
        "timestamp": now_unix,
    }
    return existing, None, payload


def apply_approval_decision(
    existing: Optional[PlanRecord],
    *,
    decision: str,
    reviewer_note: str,
    edited_content: Optional[str],
    now_unix: int,
) -> Tuple[Optional[PlanRecord], Optional[str], Optional[dict]]:
    """Apply human approve / reject after exit_plan_mode HITL."""
    if existing is None:
        return None, "no plan exists", None
    if existing.status != PlanStatus.PENDING_APPROVAL:
        return None, f"plan is not pending approval (status={existing.status.value})", None

    existing.approval.reviewer_note = reviewer_note or None
    existing.approval.decided_at_unix = now_unix

    if decision == "approved":
        if edited_content is not None:
            existing.content = edited_content
            existing.content_revisions += 1
        existing.status = PlanStatus.APPROVED
        return existing, None, {
            "status":
            "approved",
            "message": ("User has approved your plan. You can now start implementation. "
                        "Consider breaking the plan into tasks with task_create or todo_write."),
            "plan":
            existing.model_dump(mode="json", by_alias=True),
        }

    if decision == "rejected":
        existing.status = PlanStatus.DRAFTING
        note = reviewer_note or "Plan rejected; revise and call exit_plan_mode again."
        return existing, None, {
            "status": "rejected",
            "message": note,
            "plan": existing.model_dump(mode="json", by_alias=True),
        }

    return None, f"unknown decision={decision!r}; expected 'approved' or 'rejected'", None


def apply_register_question(
    existing: Optional[PlanRecord],
    *,
    question: str,
    options: Optional[list],
    request_id: str,
    now_unix: int,
) -> Tuple[Optional[PlanRecord], Optional[str], Optional[dict]]:
    if existing is None or not existing.is_gate_active():
        return None, "ask_user_question is only available during an active plan", None
    qid = len(existing.asked_questions) + 1
    existing.asked_questions.append(PlanQuestion(
        id=qid,
        question=question,
        options=options,
        asked_at_unix=now_unix,
    ))
    payload = {
        "status": "pending_question",
        "message": question,
        "question_id": qid,
        "options": options,
        "approval_id": request_id,
        "timestamp": now_unix,
    }
    return existing, None, payload


def apply_question_answer(
    existing: Optional[PlanRecord],
    *,
    question_id: int,
    answer: str,
) -> Tuple[Optional[PlanRecord], Optional[str], Optional[dict]]:
    if existing is None:
        return None, "no active plan", None
    for q in existing.asked_questions:
        if q.id == question_id and q.answer is None:
            q.answer = answer
            return existing, None, {
                "status": "answered",
                "question_id": question_id,
                "answer": answer,
            }
    return None, f"no pending question with id={question_id}", None
