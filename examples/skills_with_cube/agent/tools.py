# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tools for the Cube-backed skill agent."""
import os
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import WorkspaceInputSpec
from trpc_agent_sdk.code_executors.cube import CubeClientConfig
from trpc_agent_sdk.code_executors.cube import CubeWorkspaceRuntime
from trpc_agent_sdk.code_executors.cube import CubeWorkspaceRuntimeConfig
from trpc_agent_sdk.code_executors.cube import create_cube_sandbox_client
from trpc_agent_sdk.code_executors.cube import create_cube_workspace_runtime
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


def _cube_client_config() -> CubeClientConfig:
    """Build Cube executor config from environment variables."""
    return CubeClientConfig(
        execute_timeout=float(os.getenv("CUBE_EXECUTE_TIMEOUT", "30")),
        idle_timeout=int(os.getenv("CUBE_IDLE_TIMEOUT", "600")),
        auto_recover=True,
    )


async def create_skill_tool_set() -> tuple[SkillToolSet, Any, CubeWorkspaceRuntime]:
    """Create a Cube-backed skill tool set and its Cube runtime."""
    tool_kwargs = {
        "save_as_artifacts": True,
        "omit_inline_content": False,
    }

    cfg = _cube_client_config()
    sandbox_client = await create_cube_sandbox_client(cfg)
    workspace_runtime = create_cube_workspace_runtime(
        sandbox_client=sandbox_client,
        execute_timeout=cfg.execute_timeout,
        workspace_cfg=CubeWorkspaceRuntimeConfig(),
    )
    print(f"[skills_with_cube] using Cube sandbox: {workspace_runtime.sandbox_id}", flush=True)
    skill_paths = _get_skill_paths()
    repository = create_default_skill_repository(skill_paths, workspace_runtime=workspace_runtime)
    toolset = SkillToolSet(repository=repository, run_tool_kwargs=tool_kwargs)
    return toolset, repository, workspace_runtime


def build_cube_stage_inputs_specs(inputs_host: str = "/tmp/skillrun-inputs") -> list[WorkspaceInputSpec]:
    """Build example input specs for Cube runtime.

    The returned specs demonstrate the supported input schemes used by
    ``CubeWorkspaceFS.stage_inputs``:

    - ``host://``     : upload from a host path into the remote Cube sandbox
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
            src="workspace://skills/python-math/SKILL.md",
            dst="work/staged_inputs/python-math_skill.md",
            mode="copy",
        ),
        WorkspaceInputSpec(
            src="skill://python-math/scripts/fib.py",
            dst="work/staged_inputs/fib.py",
            mode="copy",
        ),
    ]


def build_cube_skill_run_payload(skill_name: str = "python-math",
                                 inputs_host: str = "/tmp/skillrun-inputs") -> dict[str, Any]:
    """Build a full ``skill_run`` payload for Cube mode demonstration.

    This payload can be used directly when invoking the ``skill_run`` tool:
    it stages input schemes into the remote Cube workspace and writes outputs
    under ``out/``.
    """
    return {
        "skill":
        skill_name,
        "cwd":
        f"$SKILLS_DIR/{skill_name}",
        "command": ("python scripts/fib.py 10 > out/fib.txt && "
                    "(ls -R work/inputs; echo '---'; ls -R work/staged_inputs) > out/staged_inputs_tree.txt"),
        "inputs": [spec.model_dump() for spec in build_cube_stage_inputs_specs(inputs_host=inputs_host)],
        "output_files": [
            "out/fib.txt",
            "out/staged_inputs_tree.txt",
        ],
    }
