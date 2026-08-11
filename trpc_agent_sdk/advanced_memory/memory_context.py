"""Inject the long-term memory index into model system instructions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .callbacks import install_staged_callback
from .runtime import AdvancedMemoryRuntime

if TYPE_CHECKING:
    from trpc_agent_sdk.agents import LlmAgent
    from trpc_agent_sdk.context import InvocationContext
    from trpc_agent_sdk.models import LlmRequest

LONG_TERM_MEMORY_MARKER = "<advanced-memory-index>"


class LongTermMemoryContext:
    """Load a bounded MEMORY.md index for each model request."""

    def __init__(self, memory_runtime: AdvancedMemoryRuntime) -> None:
        """Store the runtime bound to this long-term memory context."""
        self._runtime = memory_runtime

    @property
    def runtime(self) -> AdvancedMemoryRuntime:
        """Return the runtime bound to this long-term memory context."""
        return self._runtime

    async def apply(self, request: "LlmRequest") -> bool:
        """Append the MEMORY.md index and on-demand read guidance."""
        config = self._runtime.config
        if not config.enabled or not config.long_term_memory_injection_enabled:
            return False
        await self._runtime.initialize()
        existing_instruction = (str(request.config.system_instruction)
                                if request.config is not None and request.config.system_instruction else "")
        if LONG_TERM_MEMORY_MARKER in existing_instruction:
            return False
        index = await self._runtime.long_term_memory.read_index()
        instruction = (
            f"{LONG_TERM_MEMORY_MARKER}\n"
            "The following is a bounded index of this project's long-term memory. It is a trusted cross-session "
            "lead, not a complete fact. Use it only when relevant to the current task. For exact details, prefer "
            "read_memory on the referenced file; do not infer details from one index line.\n\n"
            "Memory records are point-in-time observations and may become stale. Before relying on a memory for "
            "current code, configuration, or external state, verify it against the current source or resource. "
            "If a memory is incorrect or outdated, update the existing memory instead of creating a duplicate.\n\n"
            "## Proactively maintain long-term memory\n"
            "If the save_memory tool is available, proactively save information that is sufficiently certain and "
            "useful across sessions; do not wait for the user to say \"remember this\". Prefer saving:\n"
            "- user: stable identity, role, preferences, skill level, work habits, or explicit personal constraints;\n"
            "- feedback: corrections, confirmations, or preferences about collaboration, format, and quality;\n"
            "- project: goals, confirmed technical decisions, architecture/process conventions, important state, or "
            "deadlines that cannot be reliably inferred from code alone;\n"
            "- reference: locations, purposes, and usage constraints for external systems, docs, APIs, repositories, "
            "or resources.\n"
            "For corrections, replacements, or important additions, update the existing memory with the same "
            "filename instead of creating a duplicate. Keep each memory focused on one stable, concrete, actionable "
            "topic; inspect the index first and reuse an existing topic when possible.\n\n"
            "Do not save temporary task details, information reconstructable from current code, unverified guesses, "
            "duplicates, the model's own reasoning, or secrets, credentials, tokens, and other sensitive data. "
            "Do not write information that is uncertain, useful only in the current conversation, or not clearly "
            "worth preserving.\n\n"
            "save_memory writes both the detail file and the index. Pass a stable filename and concise "
            "name/description/summary, and use one of user, feedback, project, or reference for memory_type. "
            "Keep the description short and general; put detailed information in content. "
            "If save_memory is unavailable, do not claim that the information was saved.\n"
            f"Memory directory: {self._runtime.paths.memory_dir}\n"
            f"Index file: {self._runtime.paths.memory_index_path}\n"
            f"<index>\n{index.rstrip()}\n</index>\n"
            f"</advanced-memory-index>")
        request.append_instructions([instruction])
        return True


class LongTermMemoryContextCallback:
    """Adapt the long-term memory index injector to before_model_callback."""

    advanced_memory_stage = 5

    def __init__(self, memory_context: LongTermMemoryContext) -> None:
        """Store the injector executed before each model request."""
        self._memory_context = memory_context

    @property
    def memory_context(self) -> LongTermMemoryContext:
        """Return the memory context used by this callback."""
        return self._memory_context

    async def __call__(self, ctx: "InvocationContext", request: "LlmRequest") -> None:
        """Inject the long-term memory index before a model request."""
        del ctx
        await self._memory_context.apply(request)
        return None


def setup_long_term_memory_context(
    agent: "LlmAgent",
    memory_runtime: AdvancedMemoryRuntime,
) -> LongTermMemoryContext:
    """Install the index callback while preserving pipeline stage order."""
    memory_context = LongTermMemoryContext(memory_runtime)
    callback = LongTermMemoryContextCallback(memory_context)
    existing_context = install_staged_callback(
        agent,
        callback,
        callback_type=LongTermMemoryContextCallback,
        component_attribute="memory_context",
        memory_runtime=memory_runtime,
        conflict_message="Long-term memory context is already configured with another runtime",
    )
    return existing_context or memory_context
