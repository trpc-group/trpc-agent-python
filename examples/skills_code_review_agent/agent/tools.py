"""Connect Agent Skills to the configured sandbox runtime."""

from pathlib import Path

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.tools import FunctionTool

from filters.sdk_filter import SandboxToolFilter
from filters.policy import ReviewPolicyContext
from sandbox.base import SandboxProvider
from sandbox.lazy import LazySandboxRuntime

SAFE_SKILL_TOOLS = frozenset(
    {
        "skill_list",
        "skill_list_docs",
        "skill_load",
        "skill_run",
        "skill_select_docs",
        "sandbox_policy_info",
    }
)


def sandbox_policy_info() -> dict[str, object]:
    """Describe the enforced execution boundary without running a command."""
    return {
        "runtime": "docker",
        "network_allowed": False,
        "repository_mount": "read-only",
        "root_filesystem": "read-only",
        "container_user": "non-root host UID/GID",
        "resource_limits": ["memory", "cpu", "pids", "tmpfs"],
        "execution_requires_filter_allow": True,
    }


class GovernedSkillToolSet(SkillToolSet):
    """Expose only filtered Skill execution and non-executing metadata tools."""

    def __init__(self, *args, managed_runtime=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._managed_runtime = managed_runtime

    async def get_tools(self, invocation_context=None):
        tools = await super().get_tools(invocation_context)
        # Do not expose generic workspace execution outside the governed Skill path.
        return [tool for tool in tools if tool.name in SAFE_SKILL_TOOLS]

    async def close(self) -> None:
        if self._managed_runtime is not None:
            await self._managed_runtime.close()


def create_skill_tools(
    sandbox: SandboxProvider,
    repository_path: Path,
    skills_path: Path,
    policy_context: ReviewPolicyContext | None = None,
) -> tuple[SkillToolSet, BaseSkillRepository, BaseWorkspaceRuntime]:
    """Create a SkillToolSet whose commands run only in the sandbox."""
    # Defer Docker startup so Filter rejection can happen without creating a container.
    runtime = LazySandboxRuntime(
        lambda: sandbox.create_runtime(repository_path, skills_path),
    )
    repository = create_default_skill_repository(
        str(skills_path),
        workspace_runtime=runtime,
    )
    toolset = GovernedSkillToolSet(
        repository=repository,
        runtime_tools=[FunctionTool(sandbox_policy_info)],
        filters=[SandboxToolFilter(context=policy_context)],
        managed_runtime=runtime,
        require_skill_loaded=True,
        run_tool_kwargs={
            "save_as_artifacts": False,
            "omit_inline_content": False,
            "timeout": 30,
        },
    )
    return toolset, repository, runtime
