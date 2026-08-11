"""Provide the one-shot entry point for the context pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import TYPE_CHECKING

from .autocompact import AutoCompact
from .autocompact import LegacySummaryGenerator
from .autocompact import setup_autocompact
from .history_snip import HistorySnip
from .history_snip import setup_history_snip
from .memory_context import LongTermMemoryContext
from .memory_context import setup_long_term_memory_context
from .microcompact import Microcompact
from .microcompact import setup_microcompact
from .runtime import AdvancedMemoryRuntime
from .session_memory import SessionMemoryExtractor
from .session_memory import SessionMemoryGenerator
from .session_service import TranscriptSessionService
from .tool_result_budget import setup_tool_result_budget
from .tool_result_budget import ToolResultBudget

if TYPE_CHECKING:
    from trpc_agent_sdk.agents import LlmAgent
    from trpc_agent_sdk.sessions import SessionServiceABC
    from trpc_agent_sdk.tools.advanced_memory_tool import AdvancedMemoryTools


@dataclass(frozen=True)
class AdvancedContextManagement:
    """Aggregate the five components installed by one setup call."""

    long_term_memory: LongTermMemoryContext
    tool_result_budget: ToolResultBudget
    history_snip: HistorySnip
    microcompact: Microcompact
    autocompact: AutoCompact


@dataclass(frozen=True)
class AdvancedMemoryIntegration:
    """Aggregate Agent callbacks, the session memory extractor, and service."""

    context_management: AdvancedContextManagement
    session_memory_extractor: SessionMemoryExtractor
    session_service: TranscriptSessionService
    long_term_memory_tools: "AdvancedMemoryTools | None"


def _setup_long_term_memory_tools(
    agent: "LlmAgent",
    memory_runtime: AdvancedMemoryRuntime,
) -> "AdvancedMemoryTools":
    """Install the three official memory tools idempotently."""
    from trpc_agent_sdk.tools.advanced_memory_tool import (
        ADVANCED_MEMORY_TOOL_NAMES, )
    from trpc_agent_sdk.tools.advanced_memory_tool import AdvancedMemoryTools

    matching_tools = [tool for tool in agent.tools if getattr(tool, "name", None) in ADVANCED_MEMORY_TOOL_NAMES]
    if matching_tools:
        owners = {getattr(getattr(tool, "func", None), "__self__", None) for tool in matching_tools}
        if len(owners) != 1:
            raise ValueError("Advanced Memory tool names are already used by different tools")
        owner = owners.pop()
        if not isinstance(owner, AdvancedMemoryTools):
            raise ValueError("Advanced Memory tool names are already used by non-SDK tools")
        if owner.runtime is not memory_runtime:
            raise ValueError("Advanced Memory tools use another runtime")
        installed_names = {getattr(tool, "name", None) for tool in matching_tools}
        if installed_names != ADVANCED_MEMORY_TOOL_NAMES:
            raise ValueError("Advanced Memory tools are only partially installed")
        return owner
    tools = AdvancedMemoryTools(memory_runtime)
    agent.tools.extend(tools.as_tools())
    return tools


def _setup_preload_memory_tool(
    agent: "LlmAgent",
    memory_runtime: AdvancedMemoryRuntime,
    model: Any | None = None,
) -> None:
    """Install the automatic topic-memory preprocessor when enabled."""
    if not memory_runtime.config.enabled or not memory_runtime.config.preload_memory_enabled:
        return
    from trpc_agent_sdk.advanced_memory.preload_memory import MemoryPreloader
    from trpc_agent_sdk.advanced_memory.preload_memory import (
        ModelMemoryRelevanceSelector, )
    from trpc_agent_sdk.tools import PreloadMemoryTool

    existing = [tool for tool in agent.tools if getattr(tool, "name", None) == "preload_memory"]
    use_legacy_memory = False
    if existing:
        if len(existing) != 1 or not isinstance(existing[0], PreloadMemoryTool):
            raise ValueError("Advanced Memory preload tool name is already used by another tool")
        use_legacy_memory = existing[0].uses_legacy_memory
        agent.tools.remove(existing[0])
    preloader = MemoryPreloader(memory_runtime, ModelMemoryRelevanceSelector(model))
    agent.tools.append(PreloadMemoryTool(
        memory_preloader=preloader.preload,
        use_legacy_memory=use_legacy_memory,
    ))


def setup_context_management(
    agent: "LlmAgent",
    memory_runtime: AdvancedMemoryRuntime,
    summary_generator: LegacySummaryGenerator | None = None,
    *,
    compact_model: Any | None = None,
) -> AdvancedContextManagement:
    """Install the complete Advanced Memory pipeline in fixed stages."""
    return AdvancedContextManagement(
        long_term_memory=setup_long_term_memory_context(agent, memory_runtime),
        tool_result_budget=setup_tool_result_budget(agent, memory_runtime),
        history_snip=setup_history_snip(agent, memory_runtime),
        microcompact=setup_microcompact(agent, memory_runtime),
        autocompact=setup_autocompact(
            agent,
            memory_runtime,
            summary_generator,
            model=compact_model,
        ),
    )


def setup_advanced_memory(
    agent: "LlmAgent",
    session_service: "SessionServiceABC",
    memory_runtime: AdvancedMemoryRuntime,
    summary_generator: LegacySummaryGenerator | None = None,
    session_memory_generator: SessionMemoryGenerator | None = None,
    *,
    compact_model: Any | None = None,
    session_memory_model: Any | None = None,
    preload_memory_model: Any | None = None,
    install_long_term_memory_tools: bool = True,
) -> AdvancedMemoryIntegration:
    """Assemble callbacks, the transcript decorator, and session memory."""
    context_management = setup_context_management(
        agent,
        memory_runtime,
        summary_generator,
        compact_model=compact_model,
    )
    long_term_memory_tools = (_setup_long_term_memory_tools(agent, memory_runtime)
                              if install_long_term_memory_tools and memory_runtime.config.enabled else None)
    _setup_preload_memory_tool(agent, memory_runtime, model=preload_memory_model)
    if isinstance(session_service, TranscriptSessionService):
        if session_service.memory_runtime is not memory_runtime:
            raise ValueError("Transcript session service uses another runtime")
        extractor = session_service.session_memory_extractor
        if extractor is not None:
            if session_memory_generator is not None or session_memory_model is not None:
                raise ValueError("Session memory extractor is already configured; "
                                 "do not provide another generator or model")
        else:
            extractor = SessionMemoryExtractor(
                memory_runtime,
                session_memory_generator,
                model=session_memory_model,
            )
            session_service.attach_session_memory_extractor(extractor)
        wrapped_service = session_service
    else:
        extractor = SessionMemoryExtractor(
            memory_runtime,
            session_memory_generator,
            model=session_memory_model,
        )
        wrapped_service = TranscriptSessionService(
            session_service,
            memory_runtime,
            extractor,
        )
    return AdvancedMemoryIntegration(
        context_management=context_management,
        session_memory_extractor=extractor,
        session_service=wrapped_service,
        long_term_memory_tools=long_term_memory_tools,
    )
