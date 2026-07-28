"""Translate application resource policy to SDK limits."""

from trpc_agent_sdk.code_executors import WorkspaceResourceLimits

from ..models import ResourcePolicy


def to_workspace_limits(policy: ResourcePolicy) -> WorkspaceResourceLimits:
    return WorkspaceResourceLimits(
        cpu_percent=policy.cpu_percent,
        memory_mb=policy.memory_mb,
        max_pids=policy.max_pids,
    )
