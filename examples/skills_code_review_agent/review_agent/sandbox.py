# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Workspace runtime factory: container by default, local only as explicit fallback.

Container isolation properties (verified against the SDK source, not the
metadata API — ``describe()`` wrongly hardcodes ``network_allowed=True``):

* no network: docker ``network_mode`` defaults to ``'none'``;
* env allowlist for free: only the five workspace variables plus the
  explicit per-call env reach the container — the host environment does not;
* skill directory staged read-only;
* one long-lived container per process, every execution is a docker exec
  (no per-run container start cost).

Local mode inherits the full host environment (``os.environ.copy()`` in the
SDK), so it is gated behind ``--unsafe-local`` and the run env is scrubbed via
the filter env allowlist; it exists for development and CI hosts without
Docker.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime, create_local_workspace_runtime
from trpc_agent_sdk.log import logger

_SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"


@dataclass
class SandboxHandle:
    """The chosen runtime plus everything the pipeline needs to know about it."""

    runtime: BaseWorkspaceRuntime
    kind: str  # "container" | "local"
    reason: str  # why this runtime was chosen (recorded in the task row)
    work_root: str = ""


def skills_root() -> str:
    return str(_SKILLS_ROOT)


def create_sandbox(prefer: str = "container",
                   work_root: str = "",
                   inputs_host_base: str = "",
                   docker_image: Optional[str] = None) -> SandboxHandle:
    """Create the workspace runtime.

    Args:
        prefer: "container" (default) or "local" (explicit --unsafe-local).
        work_root: host directory for local workspaces (temp dir of the run).
        inputs_host_base: host directory that host:// input specs resolve
            against; also the only directory the filter allows as input source.
        docker_image: optional custom image tag.

    Container startup failures (no docker daemon, missing image) degrade to
    local with a recorded reason instead of failing the review task.
    """
    if prefer == "container":
        try:
            # imported lazily: the docker SDK may be absent on dev machines
            from trpc_agent_sdk.code_executors import (DEFAULT_INPUTS_CONTAINER, DEFAULT_SKILLS_CONTAINER,
                                                       ContainerConfig, create_container_workspace_runtime)

            config = ContainerConfig(image=docker_image) if docker_image else None
            binds = [f"{_SKILLS_ROOT}:{DEFAULT_SKILLS_CONTAINER}:ro"]
            if inputs_host_base:
                # host:// input specs resolve through this read-only bind;
                # the runtime derives inputs_host_base from the bind target
                binds.append(f"{inputs_host_base}:{DEFAULT_INPUTS_CONTAINER}:ro")
            host_config = {
                "Binds": binds,
                # explicit even though it is the SDK default: no network
                "network_mode": "none",
            }
            runtime = create_container_workspace_runtime(
                container_config=config,
                host_config=host_config,
                auto_inputs=True,
            )
            return SandboxHandle(runtime=runtime, kind="container", reason="container runtime ready")
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("container runtime unavailable (%s); falling back to local", ex)
            fallback_reason = f"container unavailable: {type(ex).__name__}: {str(ex)[:120]}"
    else:
        fallback_reason = "local runtime requested via --unsafe-local"

    runtime = create_local_workspace_runtime(
        work_root=work_root,
        read_only_staged_skill=True,
        auto_inputs=True,
        inputs_host_base=inputs_host_base,
    )
    return SandboxHandle(runtime=runtime, kind="local", reason=fallback_reason, work_root=work_root)
