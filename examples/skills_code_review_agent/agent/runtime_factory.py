"""Workspace runtime factories for production sandbox backends."""

from __future__ import annotations

from typing import Any

from .pipeline import build_workspace_sandbox_runner


def create_container_sandbox_runner(*,
                                    image: str = "python:3-slim",
                                    docker_path: str | None = None,
                                    base_url: str | None = None):
    """Create a Docker Container workspace sandbox runner.

    Docker must be installed and reachable. The import is lazy so offline
    dry-run tests do not require Docker daemon access.
    """
    from trpc_agent_sdk.code_executors.container import ContainerConfig
    from trpc_agent_sdk.code_executors.container import create_container_workspace_runtime

    host_config = {"network_mode": "none"}
    config = ContainerConfig(
        image=image,
        docker_path=docker_path or "",
        base_url=base_url or "",
        host_config=host_config,
    )
    runtime = create_container_workspace_runtime(container_config=config, host_config=host_config)
    return build_workspace_sandbox_runner(runtime, "container")


def create_cube_sandbox_runner(*,
                               executor: Any | None = None,
                               sandbox_client: Any | None = None,
                               workspace_cfg: Any | None = None):
    """Create a Cube/E2B workspace sandbox runner.

    The optional cube dependency is imported lazily.
    """
    if executor is None and sandbox_client is None:
        raise ValueError("Cube sandbox requires an executor or sandbox_client")

    from trpc_agent_sdk.code_executors.cube import create_cube_workspace_runtime

    runtime = create_cube_workspace_runtime(executor=executor,
                                            sandbox_client=sandbox_client,
                                            workspace_cfg=workspace_cfg)
    return build_workspace_sandbox_runner(runtime, "cube")


async def create_cube_sandbox_runner_from_config(
    *,
    template: str | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
    sandbox_id: str | None = None,
):
    """Create or attach to a Cube/E2B sandbox and wrap it as a runner."""
    from trpc_agent_sdk.code_executors.cube import CubeClientConfig
    from trpc_agent_sdk.code_executors.cube import CubeSandboxClient

    cfg = CubeClientConfig(template=template, api_url=api_url, api_key=api_key, sandbox_id=sandbox_id)
    client = await CubeSandboxClient.open_existing(cfg) if sandbox_id else await CubeSandboxClient.open_new(cfg)
    return create_cube_sandbox_runner(sandbox_client=client)
