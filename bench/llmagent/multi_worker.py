"""LlmAgent benchmark target with the Unix socket control plane enabled."""

from __future__ import annotations

import os

from trpc_agent_bench.bench import AgentBenchConf

from trpc_agent_sdk.cancel import UnixSocketConfig
from trpc_agent_sdk.cancel import init_unix_socket
from trpc_agent_sdk.cancel import release_unix_socket

from .base import LlmAgentTarget
from .base import PreparedLlmAgent

SOCKET_DIR = "/tmp/trpc-agent-bench"


class UnixSocketLlmAgentTarget(LlmAgentTarget):
    """Run LlmAgent benchmarks with the Unix socket control plane enabled."""

    name = "llm_agent_unix_socket"

    async def prepare(self, *, conf: AgentBenchConf) -> PreparedLlmAgent:
        socket_path = os.path.join(SOCKET_DIR, f"cancel-{os.getpid()}.sock")
        init_unix_socket(UnixSocketConfig(socket_path=socket_path))
        try:
            return await super().prepare(conf=conf)
        except Exception:
            release_unix_socket()
            raise

    async def cleanup(self, prepared: PreparedLlmAgent) -> None:
        try:
            await super().cleanup(prepared)
        finally:
            release_unix_socket()


TARGET = UnixSocketLlmAgentTarget()
