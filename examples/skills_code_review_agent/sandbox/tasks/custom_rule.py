"""Custom code-review rule task."""

import sys
import uuid

from ..models import ResourcePolicy, SandboxTask


def build_code_review_task(workspace_path: str, runtime_name: str) -> SandboxTask:
    root = workspace_path.rstrip("/")
    executable = sys.executable if runtime_name == "local" else "python3"
    script = f"{root}/code-review/runner.py"
    review_input = f"{root}/review.json"
    return SandboxTask(
        id=uuid.uuid4().hex,
        task_type="custom_rule",
        command=[executable, script, review_input],
        cwd=workspace_path,
        input_paths=[script, review_input],
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        resources=ResourcePolicy(timeout_seconds=20),
    )
