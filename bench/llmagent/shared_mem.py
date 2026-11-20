"""LlmAgent benchmark target with shared-memory control plane enabled."""

from __future__ import annotations

from trpc_agent_bench.bench import AgentBenchConf

from trpc_agent_sdk.cancel import SharedMemoryConfig
from trpc_agent_sdk.cancel import destroy_shared_mem
from trpc_agent_sdk.cancel import init_shared_mem

from .base import LlmAgentTarget
from .base import PreparedLlmAgent


class SharedMemoryLlmAgentTarget(LlmAgentTarget):
    """Run LlmAgent benchmarks with the process-global shared control plane."""

    name = "llm_agent_shared_mem"

    async def prepare(self, *, conf: AgentBenchConf) -> PreparedLlmAgent:
        init_shared_mem(SharedMemoryConfig())
        try:
            return await super().prepare(conf=conf)
        except Exception:
            destroy_shared_mem()
            raise

    async def cleanup(self, prepared: PreparedLlmAgent) -> None:
        try:
            await super().cleanup(prepared)
        finally:
            destroy_shared_mem()


TARGET = SharedMemoryLlmAgentTarget()
