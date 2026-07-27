"""Skill repository helpers for integrating the review skill with LlmAgent.

The workspace runtime defaults to a network-disabled container, matching the
deterministic CLI: issue #92 requires the local runtime to be a development
fallback rather than the production path, so choosing it takes an explicit
argument.

Every skill tool carries the review execution policy as a tool filter, so a
model-driven ``skill_run`` is subject to the same governance the CLI applies
before it touches the sandbox.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bounded_runtime import ReviewBoundedWorkspaceRuntime
from .filtering import ReviewExecutionFilter
from .workspace_sandbox import (
    NetworkIsolatedWorkspaceRuntime,
    assert_runtime_network_isolated,
    create_network_isolated_cube_runtime,
)

if TYPE_CHECKING:
    from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
    from trpc_agent_sdk.skills import SkillToolSet

    from .models import FilterDecision


DEFAULT_REVIEW_IMAGE = os.getenv("CODE_REVIEW_IMAGE", "python:3.12-slim")
SKILLS_CONTAINER_PATH = "/opt/trpc-agent/skills"
MAX_REVIEW_TOOL_TIMEOUT_SECONDS = 30.0
_INTERACTIVE_TOOL_NAMES = {
    "skill_exec",
    "skill_write_stdin",
    "skill_poll_session",
    "skill_kill_session",
    "workspace_write_stdin",
    "workspace_kill_session",
}


def get_skill_root() -> Path:
    """Return the example skill root."""
    return Path(__file__).resolve().parents[1] / "skills"


def create_workspace_runtime(runtime: str = "container") -> BaseWorkspaceRuntime:
    """Create the workspace runtime backing the code-review skill.

    Args:
        runtime: ``container`` (default), ``cube`` or ``local``. ``local`` runs
            skill scripts on the host and is only intended for development.
    """
    if runtime == "container":
        from trpc_agent_sdk.code_executors import (
            ContainerConfig,
            create_container_workspace_runtime,
        )

        workspace_runtime = create_container_workspace_runtime(
            container_config=ContainerConfig(image=DEFAULT_REVIEW_IMAGE),
            # network_mode defaults to "none"; set it explicitly so the intent
            # survives anyone editing this dict later.
            host_config={
                "network_mode": "none",
                "Binds": [f"{get_skill_root()}:{SKILLS_CONTAINER_PATH}:ro"],
            },
            auto_inputs=True,
        )
        return NetworkIsolatedWorkspaceRuntime(workspace_runtime)

    if runtime == "cube":
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(create_workspace_runtime_async("cube"))
        raise RuntimeError(
            "Cube runtime creation is asynchronous inside a running event loop; "
            "await create_workspace_runtime_async('cube') and pass the result "
            "to create_review_skill_tool_set(workspace_runtime=...)"
        )

    if runtime == "local":
        from trpc_agent_sdk.code_executors import create_local_workspace_runtime

        return create_local_workspace_runtime()

    raise ValueError(
        f"unsupported workspace runtime: {runtime!r} (expected container, cube or local)"
    )


async def create_workspace_runtime_async(runtime: str) -> BaseWorkspaceRuntime:
    """Create a runtime from async application setup, including Cube/E2B."""
    if runtime != "cube":
        return create_workspace_runtime(runtime)

    return await create_network_isolated_cube_runtime(MAX_REVIEW_TOOL_TIMEOUT_SECONDS)


def _await_cleanup_blocking(awaitable: Any) -> None:
    """Finish one cleanup awaitable even when the caller already has a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(awaitable)
        return

    # A synchronous factory cannot await the caller's running loop.  Cleanup
    # is exceptional and short, so use a private thread/loop and join it before
    # re-raising the construction failure.
    failures: list[BaseException] = []

    def run_cleanup() -> None:
        try:
            asyncio.run(awaitable)
        except BaseException as ex:  # noqa: BLE001 - best-effort cleanup only
            failures.append(ex)

    thread = threading.Thread(target=run_cleanup, daemon=True)
    thread.start()
    thread.join()


def _destroy_workspace_runtime_sync(workspace_runtime: Any) -> None:
    """Best-effort release used only for factory-owned construction failures."""
    try:
        destroy = getattr(workspace_runtime, "destroy", None)
        if callable(destroy):
            result = destroy()
            if inspect.isawaitable(result):
                _await_cleanup_blocking(result)
            return

        container_client = getattr(workspace_runtime, "container", None)
        cleanup = getattr(container_client, "_cleanup_container", None)
        if callable(cleanup):
            result = cleanup()
            if inspect.isawaitable(result):
                _await_cleanup_blocking(result)
    except BaseException:  # noqa: BLE001 - preserve the original factory error
        return


def create_review_skill_tool_set(
    runtime: str = "container",
    *,
    workspace_runtime: BaseWorkspaceRuntime | None = None,
    intercept_sink: Callable[[FilterDecision], None] | None = None,
    execution_policy: ReviewExecutionFilter | None = None,
) -> tuple[SkillToolSet, Any]:
    """Create a SkillToolSet for the bundled code-review skill.

    The deterministic CLI drives the same skill files directly so it can run
    without model credentials. This helper is for mounting the skill into a
    regular LlmAgent and letting the model call the skill tools itself.
    """
    from trpc_agent_sdk.skills import SkillToolSet, create_default_skill_repository
    from trpc_agent_sdk.skills.tools import WorkspaceExecTool

    from .sdk_filter import CodeReviewSandboxPolicyFilter

    policy = execution_policy or ReviewExecutionFilter()
    tool_timeout_seconds = min(
        MAX_REVIEW_TOOL_TIMEOUT_SECONDS,
        float(policy.max_timeout_seconds),
    )
    if tool_timeout_seconds < 1:
        raise ValueError("execution policy timeout must be at least one second")
    workspace_exec_timeout = int(tool_timeout_seconds)

    owns_runtime = workspace_runtime is None
    base_workspace_runtime = workspace_runtime
    if base_workspace_runtime is None:
        base_workspace_runtime = create_workspace_runtime(runtime)

    try:
        if runtime != "local":
            assert_runtime_network_isolated(base_workspace_runtime, runtime)

        bounded_runtime = ReviewBoundedWorkspaceRuntime(
            base_workspace_runtime,
            policy,
            wrapper_python=sys.executable if runtime == "local" else "python",
        )
        repository = create_default_skill_repository(
            str(get_skill_root()),
            workspace_runtime=bounded_runtime,
            use_cached_repository=True,
        )
        policy_filter = CodeReviewSandboxPolicyFilter(policy, intercept_sink)
        filters = [policy_filter]

        class _ReviewWorkspaceExecTool(WorkspaceExecTool):
            """Bind the SDK's zero/omitted timeout to the review policy budget."""

            async def _run_async_impl(self, *, tool_context, args):
                bounded_args = dict(args)
                raw_timeout = bounded_args.get("timeout_sec", 0)
                try:
                    parsed_timeout = float(raw_timeout)
                except (TypeError, ValueError):
                    # Preserve the SDK's normal Pydantic validation error for
                    # malformed values rather than silently changing the request.
                    pass
                else:
                    if parsed_timeout <= 0:
                        bounded_args["timeout_sec"] = workspace_exec_timeout
                    elif parsed_timeout > tool_timeout_seconds:
                        raise ValueError(
                            f"workspace_exec timeout {parsed_timeout:g}s exceeds "
                            f"review budget {tool_timeout_seconds:g}s"
                        )
                return await super()._run_async_impl(
                    tool_context=tool_context,
                    args=bounded_args,
                )

        class _ReviewSkillToolSet(SkillToolSet):
            """Expose only one-shot execution paths governed by this facade."""

            async def get_tools(self, invocation_context=None):
                tools = await super().get_tools(invocation_context)
                return [
                    tool
                    for tool in tools
                    if getattr(tool, "name", "") not in _INTERACTIVE_TOOL_NAMES
                ]

        # SkillToolSet normally creates unfiltered workspace_exec tools alongside
        # skill_run. Supply the one-shot runtime tool explicitly so a model cannot
        # bypass the review policy by choosing the lower-level shell entry point.
        exec_tool = _ReviewWorkspaceExecTool(
            workspace_runtime=bounded_runtime,
            filters=filters,
        )
        tool_set = _ReviewSkillToolSet(
            repository=repository,
            runtime_tools=[exec_tool],
            # Forwarded to SkillRunTool, so the policy runs before any skill command
            # reaches the workspace.
            filters=filters,
            run_tool_kwargs={"timeout": tool_timeout_seconds},
            denied_cmds=["curl", "wget", "ssh", "scp", "nc", "sudo", "pip", "npm"],
        )
        return tool_set, repository
    except BaseException:
        if owns_runtime and base_workspace_runtime is not None:
            _destroy_workspace_runtime_sync(base_workspace_runtime)
        raise
