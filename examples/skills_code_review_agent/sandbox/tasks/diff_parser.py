"""Sandboxed unified-diff parser task builder."""

import sys
import uuid

from ..models import ResourcePolicy, SandboxTask


def build_diff_parser_task(workspace_path: str, runtime_name: str) -> SandboxTask:
    root = workspace_path.rstrip("/")
    executable = sys.executable if runtime_name == "local" else "python3"
    script = f"{root}/code-review/scripts/parse_diff.py"
    diff_path = f"{root}/change.diff"
    return SandboxTask(
        id=uuid.uuid4().hex,
        task_type="diff_parser",
        command=[executable, script, diff_path],
        cwd=workspace_path,
        input_paths=[script, diff_path],
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        resources=ResourcePolicy(timeout_seconds=20),
    )
