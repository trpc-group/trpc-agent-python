"""Static-analysis task builder."""

import uuid

from ..models import ResourcePolicy, SandboxTask


def build_static_check_task(workspace_path: str, paths: list[str]) -> SandboxTask:
    return SandboxTask(
        id=uuid.uuid4().hex,
        task_type="static_check",
        command=["ruff", "check", *paths],
        cwd=workspace_path,
        input_paths=[f"{workspace_path.rstrip('/')}/{path}" for path in paths],
        resources=ResourcePolicy(timeout_seconds=60),
    )
