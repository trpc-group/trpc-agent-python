# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tools for the OpenClaw agent."""

from pathlib import Path

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import ContainerConfig
from trpc_agent_sdk.code_executors import DEFAULT_INPUTS_CONTAINER
from trpc_agent_sdk.code_executors import DEFAULT_SKILLS_CONTAINER
from trpc_agent_sdk.code_executors import create_container_workspace_runtime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.skills import SkillToolSet

from ..config import ClawConfig
from ..config import ContainerCodeExecutorConfig
from ..config import LocalCodeExecutorConfig
from ..config import TRPC_CLAW_SKILLS_INSTALL_ROOT_ENV_NAME
from ._skill_loader import ClawSkillLoader

# "/opt/trpc-agent/skills"

_SKILLS_DIRS = ["local", "local_file", "network", "builtin"]
_CONTAINER_SKILLS_INSTALL_ROOT = f"{DEFAULT_SKILLS_CONTAINER}/downloaded"


def _get_skill_paths(repository: ClawSkillLoader) -> set[Path]:
    """Return existing skill root paths from config."""
    roots: set[Path] = set()
    workspace_skills_root = repository.workspace_skills_root
    for skill_dir in _SKILLS_DIRS:
        path = workspace_skills_root / skill_dir
        if path.exists():
            roots.add(path)
    return roots


def _create_local_workspace_runtime(config: LocalCodeExecutorConfig) -> BaseWorkspaceRuntime:
    """Create a new local workspace runtime."""
    return create_local_workspace_runtime(
        work_root=config.workspace,
        read_only_staged_skill=config.read_only_staged_skill,
        auto_inputs=config.auto_inputs,
        inputs_host_base=config.inputs_host_base,
    )


def _create_container_workspace_runtime(config: ContainerCodeExecutorConfig, skills_root: set[Path],
                                        downloaded_skills_root: Path) -> BaseWorkspaceRuntime:
    """Create a new container workspace runtime.
    Args:
        config: The config.
        skills_root: The skills root.
    Returns:
        BaseWorkspaceRuntime: The workspace runtime.
    """
    container_cfg = ContainerConfig(
        base_url=config.base_url,
        image=config.image,
        docker_path=config.docker_path,
    )
    inputs_host = config.inputs_host_base
    specs = []
    for idx, skill_path in enumerate(list(skills_root)):
        specs.append(f"{skill_path}:{DEFAULT_SKILLS_CONTAINER}/{idx}:ro")
    # Writable shared path so runtime-downloaded skills can persist on host.
    specs.append(f"{downloaded_skills_root}:{_CONTAINER_SKILLS_INSTALL_ROOT}:rw")
    if inputs_host:
        inputs_spec = f"{inputs_host}:{DEFAULT_INPUTS_CONTAINER}:ro"
        specs.append(inputs_spec)
    host_config = {"Binds": specs}
    return create_container_workspace_runtime(
        container_config=container_cfg,
        host_config=host_config,
        auto_inputs=config.auto_inputs,
    )


def _create_workspace_runtime(config: ClawConfig, repository: ClawSkillLoader) -> BaseWorkspaceRuntime:
    """Create a new workspace runtime.
    Args:
        config: The config.
        repository: The skill repository.
    Returns:
        BaseWorkspaceRuntime: The workspace runtime.
    """
    workspace_runtime_type = config.skills.sandbox_type
    if workspace_runtime_type == "container":
        skills_root = _get_skill_paths(repository)
        return _create_container_workspace_runtime(
            config.skills.container_config,
            skills_root,
            repository.downloaded_skills_root,
        )
    elif workspace_runtime_type == "local":
        return _create_local_workspace_runtime(config.skills.local_config)
    else:
        raise ValueError(f"Invalid workspace runtime type: {workspace_runtime_type}")


def create_skill_tool_set(config: ClawConfig) -> SkillToolSet:
    """Create a new skill tool set.
    Args:
        config: The config.

    Returns:
        SkillToolSet: The skill tool set.
    """
    repository = ClawSkillLoader(config=config)
    # there are some skills from network, so we need to create a workspace runtime after the repository is created
    workspace_runtime = _create_workspace_runtime(config=config, repository=repository)
    repository.set_workspace_runtime(workspace_runtime)
    run_tool_kwargs = dict(config.skills.run_tool_kwargs or {})
    run_env = dict(run_tool_kwargs.get("env", {}) or {})
    if config.skills.sandbox_type == "container":
        run_env.setdefault(TRPC_CLAW_SKILLS_INSTALL_ROOT_ENV_NAME, _CONTAINER_SKILLS_INSTALL_ROOT)
    else:
        run_env.setdefault(TRPC_CLAW_SKILLS_INSTALL_ROOT_ENV_NAME, str(repository.downloaded_skills_root))
    run_tool_kwargs["env"] = run_env
    tool_set = SkillToolSet(repository=repository, run_tool_kwargs=run_tool_kwargs)
    return tool_set
