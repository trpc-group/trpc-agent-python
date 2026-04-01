# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """
import os
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import DEFAULT_INPUTS_CONTAINER
from trpc_agent_sdk.code_executors import DEFAULT_SKILLS_CONTAINER
from trpc_agent_sdk.code_executors import WorkspaceInputSpec
from trpc_agent_sdk.code_executors import create_container_workspace_runtime
from trpc_agent_sdk.skills import ENV_SKILLS_ROOT
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository


def _get_skill_paths() -> str:
    """Get the skill paths."""
    skills_root = os.getenv(ENV_SKILLS_ROOT)
    if skills_root:
        return skills_root
    current_path = Path(__file__).parent
    path = str(current_path.parent / "skills")
    # convert to file URL
    # path = "file://" + path
    # "http://{host}:{port}/{path}/{filename}.{extension}"
    # path = "http://localhost:8000/skills/skills.tar.gz"
    return path


def _create_workspace_runtime(**kwargs: Any) -> BaseWorkspaceRuntime:
    """Create a new workspace runtime."""
    inputs_host = kwargs.pop("inputs_host", None)
    skill_paths = _get_skill_paths()
    host_config = {}
    specs = []
    skill_spec = f"{skill_paths}:{DEFAULT_SKILLS_CONTAINER}:ro"
    specs.append(skill_spec)
    if inputs_host:
        inputs_spec = f"{inputs_host}:{DEFAULT_INPUTS_CONTAINER}:ro"
        specs.append(inputs_spec)
    host_config["Binds"] = specs
    kwargs["host_config"] = host_config
    kwargs["auto_inputs"] = True
    return create_container_workspace_runtime(**kwargs)


def create_skill_tool_set() -> SkillToolSet:
    """Create a new skill tool set."""
    tool_kwargs = {
        "save_as_artifacts": True,
        "omit_inline_content": False,
    }
    workspace_runtime_args = {}
    # For container runtime demos, provide a host inputs base so host:// inputs
    # can be resolved through bind mount (instead of fallback tar copy).
    workspace_runtime_args["inputs_host"] = os.getenv("SKILLS_INPUTS_HOST", "/tmp/skillrun-inputs")
    workspace_runtime = _create_workspace_runtime(**workspace_runtime_args)
    skill_paths = _get_skill_paths()
    repository = create_default_skill_repository(skill_paths, workspace_runtime=workspace_runtime)
    return SkillToolSet(repository=repository, run_tool_kwargs=tool_kwargs), repository


def build_container_stage_inputs_specs(inputs_host: str = "/tmp/skillrun-inputs") -> list[WorkspaceInputSpec]:
    """Build example input specs for container runtime.

    The returned specs demonstrate all four supported input schemes used by
    ``ContainerWorkspaceFS.stage_inputs``:

    - ``host://``     : load from host path (ideally under ``inputs_host_base``)
    - ``workspace://``: reuse a file already present in current workspace
    - ``skill://``    : reference a file under workspace ``skills/``
    """
    return [
        WorkspaceInputSpec(
            src=f"host://{inputs_host}/sales.csv",
            dst="work/inputs/sales.csv",
            mode="link",
        ),
        WorkspaceInputSpec(
            # This file exists after skill staging, so the workspace:// demo is stable.
            src="workspace://skills/python_math/SKILL.md",
            dst="work/staged_inputs/python_math_skill.md",
            mode="copy",
        ),
        WorkspaceInputSpec(
            src="skill://python_math/scripts/fib.py",
            dst="work/staged_inputs/fib.py",
            mode="copy",
        ),
    ]


def build_container_skill_run_payload(skill_name: str = "python_math",
                                      inputs_host: str = "/tmp/skillrun-inputs") -> dict[str, Any]:
    """Build a full ``skill_run`` payload for container mode demonstration.

    This payload can be used directly when invoking the ``skill_run`` tool:
    it stages all four input schemes into ``work/inputs`` and writes outputs
    under ``out/``.
    """
    return {
        "skill":
        skill_name,
        "cwd":
        f"$SKILLS_DIR/{skill_name}",
        "command": ("python scripts/fib.py --n 10 > out/fib.txt && "
                    "(ls -R work/inputs; echo '---'; ls -R work/staged_inputs) > out/staged_inputs_tree.txt"),
        "inputs": [spec.model_dump() for spec in build_container_stage_inputs_specs(inputs_host=inputs_host)],
        "output_files": [
            "out/fib.txt",
            "out/staged_inputs_tree.txt",
        ],
    }
