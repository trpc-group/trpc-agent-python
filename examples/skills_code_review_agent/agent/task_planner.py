"""Build a bounded, reproducible sandbox task plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..sandbox.models import SandboxTask
from ..sandbox.tasks import build_code_review_task, build_static_check_task, build_test_task
from .models import ReviewInput


@dataclass
class ReviewPlan:
    tasks: list[SandboxTask] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


class ReviewTaskPlanner:
    """Select checks from the staged project and changed paths."""

    def build_plan(
        self,
        review_input: ReviewInput,
        workspace_path: str,
        runtime_name: str,
        *,
        project_staged: bool,
    ) -> ReviewPlan:
        plan = ReviewPlan(
            tasks=[build_code_review_task(workspace_path, runtime_name)]
        )
        if not project_staged:
            plan.skipped.extend(
                [
                    {"check": "ruff", "reason": "full project source is unavailable"},
                    {"check": "pytest", "reason": "full project source is unavailable"},
                ]
            )
            return plan

        python_paths = sorted(
            item.path for item in review_input.files
            if item.path.endswith(".py") and not item.is_deleted
        )
        project_root = f"{workspace_path.rstrip('/')}/project"
        if python_paths:
            plan.tasks.append(build_static_check_task(project_root, python_paths))
        else:
            plan.skipped.append({"check": "ruff", "reason": "no changed Python files"})

        changed_tests = [
            path for path in python_paths
            if Path(path).name.startswith("test_") or "/tests/" in f"/{path}"
        ]
        if changed_tests:
            plan.tasks.append(build_test_task(project_root, changed_tests))
        else:
            plan.skipped.append(
                {"check": "pytest", "reason": "no changed test files were identified"}
            )
        return plan
