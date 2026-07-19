"""Tool and runtime helpers for the skills code review example."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import create_container_workspace_runtime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import ENV_SKILLS_ROOT
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository

from ..src.filter_policy import SkillScriptInvocation
from ..src.review_types import SandboxRunRecord, SandboxRunStatus

SCRIPT_TIMEOUT_SECONDS = 20
OUTPUT_LIMIT_CHARS = 4000


def create_skill_tool_set(
    workspace_runtime_type: str = "local",
    *,
    use_cached_repository: bool = True,
) -> tuple[SkillToolSet, BaseSkillRepository]:
    """Create a SkillToolSet for the code-review skill example."""

    tool_kwargs = {
        "save_as_artifacts": True,
        "omit_inline_content": False,
    }
    workspace_runtime = _create_workspace_runtime(workspace_runtime_type=workspace_runtime_type)
    skill_paths = _get_skill_paths()
    repository = create_default_skill_repository(
        skill_paths,
        workspace_runtime=workspace_runtime,
        use_cached_repository=use_cached_repository,
    )
    return SkillToolSet(repository=repository, run_tool_kwargs=tool_kwargs), repository


def build_skill_script_plan(
    *,
    diff_file: Path,
    project_root: Path,
) -> list[SkillScriptInvocation]:
    """Build the list of planned skill-script executions for the review."""

    scripts_dir = project_root / "examples" / "skills_code_review_agent" / "skills" / "code-review" / "scripts"
    return [
        SkillScriptInvocation(
            name="parse_diff",
            script_path=scripts_dir / "parse_diff.py",
            command=[
                sys.executable,
                str(scripts_dir / "parse_diff.py"),
                "--diff-file",
                str(diff_file),
            ],
            target="skill:code-review/scripts/parse_diff.py",
        ),
        SkillScriptInvocation(
            name="run_linters",
            script_path=scripts_dir / "run_linters.py",
            command=[
                sys.executable,
                str(scripts_dir / "run_linters.py"),
                "--diff-file",
                str(diff_file),
            ],
            target="skill:code-review/scripts/run_linters.py",
        ),
        SkillScriptInvocation(
            name="run_tests",
            script_path=scripts_dir / "run_tests.py",
            command=[
                sys.executable,
                str(scripts_dir / "run_tests.py"),
                "--diff-file",
                str(diff_file),
            ],
            target="skill:code-review/scripts/run_tests.py",
        ),
    ]


def build_skill_run_payload(
    *,
    diff_file: Path,
    script_name: str,
    skill_name: str = "code-review",
) -> dict[str, Any]:
    """Build a `skill_run` payload for one code-review skill script."""

    output_file = _output_file_for_script(script_name)
    return {
        "skill": skill_name,
        "cwd": f"$SKILLS_DIR/{skill_name}",
        "command": f"python scripts/{script_name} --diff-file {diff_file} > {output_file}",
        "output_files": [output_file],
    }


def execute_skill_script(
    invocation: SkillScriptInvocation,
    *,
    runtime: str,
    timeout_seconds: int = SCRIPT_TIMEOUT_SECONDS,
    output_limit_chars: int = OUTPUT_LIMIT_CHARS,
) -> SandboxRunRecord:
    """Execute a skill script in a controlled subprocess."""

    started = perf_counter()
    try:
        completed = subprocess.run(
            invocation.command,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
        )
        stdout, stdout_truncated = _truncate_output(completed.stdout, output_limit_chars)
        stderr, stderr_truncated = _truncate_output(completed.stderr, output_limit_chars)
        status = (
            SandboxRunStatus.SUCCEEDED
            if completed.returncode == 0
            else SandboxRunStatus.FAILED
        )
        return SandboxRunRecord(
            name=invocation.name,
            command=invocation.command,
            status=status,
            runtime=runtime,
            duration_ms=int((perf_counter() - started) * 1000),
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            output_truncated=stdout_truncated or stderr_truncated,
            blocked_by_filter=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _truncate_output(exc.stdout or "", output_limit_chars)
        stderr, stderr_truncated = _truncate_output(exc.stderr or "", output_limit_chars)
        return SandboxRunRecord(
            name=invocation.name,
            command=invocation.command,
            status=SandboxRunStatus.TIMED_OUT,
            runtime=runtime,
            duration_ms=int((perf_counter() - started) * 1000),
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            output_truncated=stdout_truncated or stderr_truncated,
            blocked_by_filter=False,
        )


def build_blocked_run(
    invocation: SkillScriptInvocation,
    *,
    runtime: str,
    reason: str,
) -> SandboxRunRecord:
    """Create a synthetic sandbox record for blocked invocations."""

    return SandboxRunRecord(
        name=invocation.name,
        command=invocation.command,
        status=SandboxRunStatus.BLOCKED,
        runtime=runtime,
        duration_ms=0,
        exit_code=None,
        stdout="",
        stderr=reason,
        timed_out=False,
        output_truncated=False,
        blocked_by_filter=True,
    )


def _truncate_output(text: str, limit: int) -> tuple[str, bool]:
    """Truncate subprocess output to the configured size limit."""

    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _get_skill_paths() -> str:
    """Get the skill root path for this example."""

    skills_root = os.getenv(ENV_SKILLS_ROOT)
    if skills_root:
        return skills_root
    return str(Path(__file__).resolve().parent.parent / "skills")


def _create_workspace_runtime(
    *,
    workspace_runtime_type: str = "local",
    **kwargs: Any,
) -> BaseWorkspaceRuntime:
    """Create a workspace runtime for skill execution demos."""

    if workspace_runtime_type == "container":
        skill_paths = _get_skill_paths()
        container_path = "/opt/trpc-agent/skills"
        host_config = {"Binds": [f"{skill_paths}:{container_path}:ro"]}
        kwargs["host_config"] = host_config
        kwargs["auto_inputs"] = True
        return create_container_workspace_runtime(**kwargs)
    return create_local_workspace_runtime(**kwargs)


def _output_file_for_script(script_name: str) -> str:
    """Map a script name to its canonical skill_run output artifact."""

    stem = Path(script_name).stem
    return f"out/{stem}.json"
