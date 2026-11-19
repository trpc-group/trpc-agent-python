# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""DSH agent example: a tRPC agent backed by a DeepSeek Harness runtime."""

from pathlib import Path

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.agents.dsh import DSHAgent
from trpc_agent_sdk.server.agents.dsh import DSHRunInput

from .config import get_dsh_conf

_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "dsh-workspace"


def _user_segment(value: str) -> str:
    """Keep a user id usable as one directory segment."""

    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)
    return cleaned or "anonymous"


def workspace_for(user_id: str) -> Path:
    """Return the per-user DSH working directory."""

    return _WORKSPACE_ROOT / _user_segment(user_id)


async def custom_dsh_input(
    ctx: InvocationContext,
    run_input: DSHRunInput,
) -> DSHRunInput:
    """Give every tRPC user their own DSH working directory."""

    cwd = workspace_for(ctx.user_id)
    cwd.mkdir(parents=True, exist_ok=True)
    run_input.cwd = str(cwd)
    return run_input


def create_agent() -> DSHAgent:
    """Create a DSHAgent that runs the custom profile under dsh-home/profiles/custom."""

    return DSHAgent(
        name="dsh_agent",
        description="A DeepSeek Harness agent exposed through the tRPC-Agent runtime.",
        dsh_conf=get_dsh_conf(),
        custom_dsh_input=custom_dsh_input,
    )


root_agent = create_agent()
