"""Unit-test task builder."""

import uuid

from ..models import ResourcePolicy, SandboxTask


def build_test_task(workspace_path: str, test_paths: list[str]) -> SandboxTask:
    return SandboxTask(
        id=uuid.uuid4().hex,
        task_type="test",
        command=["pytest", "-q", *test_paths],
        cwd=workspace_path,
        input_paths=[f"{workspace_path.rstrip('/')}/{path}" for path in test_paths],
        resources=ResourcePolicy(timeout_seconds=120, memory_mb=1024),
    )
