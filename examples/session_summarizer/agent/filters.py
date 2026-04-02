# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Agent Session Summarizer Filter

This module implements session summarization using an Agent Filter.

Unlike summarization at the SessionService layer, agent-level summarization has these traits:
1. Fine-grained control: each agent summarizes only its own events
2. Multi-agent support: fits multi-agent collaboration without summarization conflicts
3. Flexible triggers: can trigger on conversation text length, agent completion, etc.

Usage:
    Enable the filter in agent/agent.py:

    ```python
    def create_agent() -> LlmAgent:
        agent = LlmAgent(
            name="python_tutor",
            model=_create_model(),
            instruction=INSTRUCTION,
            filters=[AgentSessionSummarizerFilter(_create_model())],
        )
        return agent
    ```

Workflow:
    1. _after_every_stream: collect events after each stream chunk
    2. Check whether conversation text exceeds the threshold (12KB)
    3. _after: run summarization after the agent finishes
    4. _do_summarize: remove agent events from the session, summarize, replace with compressed events
"""

from typing import Any

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import get_invocation_ctx
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.sessions import SessionSummarizer


# Each agent may bind multiple filters.
# Filters run in an onion (layered) model.
# @register_agent_filter("agent_session_summarizer_filter")  # Register with the framework; bind by name on the agent
class AgentSessionSummarizerFilter(BaseFilter):
    """Agent session summarizer filter.

    Session summarizer based on Agent Filter; compresses conversation history at the agent layer.

    Compared to SessionService-level summarization, this is better for:
    - Multi-agent collaboration: each agent summarizes its own dialogue
    - Fine-grained control: different summarization strategies per agent
    - Avoiding conflicts: does not clash with SessionService-level summarization

    Triggers:
    - After each stream chunk, check conversation text length (default threshold: 12KB)
    - After the agent completes, always run summarization once

    Example:
        ```python
        # In agent/agent.py
        agent = LlmAgent(
            name="python_tutor",
            model=model,
            filters=[AgentSessionSummarizerFilter(model)],
        )
        ```
    """

    def __init__(self, model: OpenAIModel):
        """Initialize Agent Session Summarizer Filter.

        Args:
            model: LLM model instance used for summarization
        """
        super().__init__()
        # Create summarizer
        # Note: check_summarizer_functions is not set here; trigger logic is controlled in the filter
        self.summarizer = SessionSummarizer(
            model=model,
            max_summary_length=600,  # Max summary text length kept; default 1000; beyond shows ...
        )

    async def _after_every_stream(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Logic after each streaming chunk.

        Called for each streamed event from the agent to:
        1. Collect all agent-produced events in ctx.metadata["events"]
        2. Check conversation text length and trigger summarization when over threshold

        Args:
            ctx: Agent context, including metadata
            req: Request object
            rsp: FilterResult; rsp.rsp is an Event
        """
        # Each stream yields one event; rsp is FilterResult; rsp.rsp is Event
        # check if need to summarize
        if not rsp.rsp.partial:
            # Events collected so far
            events = ctx.metadata.get("events", [])
            # Extract conversation text
            conversation_text = self.summarizer._extract_conversation_text(events)
            # If conversation text exceeds 12KB, trigger summarization
            # Threshold can be tuned for your use case
            if len(conversation_text) > 12 * 1024:
                await self._do_summarize(ctx)

        # Cache the executed event on the context
        # These events are processed in _do_summarize
        if "events" not in ctx.metadata:
            ctx.metadata["events"] = []
        ctx.metadata["events"].append(rsp.rsp)

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """Logic after the agent run completes.

        Ensures all events are handled. Runs summarization once here regardless of
        whether it was triggered during streaming, so all agent-produced events are compressed.

        Args:
            ctx: Agent context
            req: Request object
            rsp: FilterResult
        """
        # Final summarization pass so nothing is left unprocessed
        await self._do_summarize(ctx)

    async def _do_summarize(self, ctx: AgentContext):
        """Core summarization steps.

        Steps:
        1. Remove this agent's events from the global session (avoid double summarization)
        2. Extract conversation text
        3. Call create_session_summary_by_events to summarize
        4. Append compressed events back to the session

        Notes:
        - If multiple agents run concurrently, use an asyncio lock for ordering
        - Async I/O may yield; ordering can get mixed without synchronization

        Args:
            ctx: Agent context with metadata and events
        """
        # Current invocation context
        invocation_ctx: InvocationContext = get_invocation_ctx()

        # Events produced by this agent run
        # pop ensures one-time processing
        events = ctx.metadata.pop("events", [])

        # If multiple agents run concurrently, add a coroutine lock for ordering
        # Async network calls may yield and reorder work
        # Example: asyncio.Lock()
        # async with self._lock:
        #     ... summarization ...

        print(
            f"\n\n {invocation_ctx.agent.name} agent: before summary agent events length: {len(invocation_ctx.session.events)}\n\n"
        )

        # Remove this agent's cached events from the global session
        # Important: avoids SessionService-level summarization processing them again
        for event in events:
            if event in invocation_ctx.session.events:
                invocation_ctx.session.events.remove(event)

        print(
            f"\n\n {invocation_ctx.agent.name}: after summary agent events length: {len(invocation_ctx.session.events)}\n\n"
        )

        session_id = invocation_ctx.session.id

        # Conversation text (debug / logging)
        conversation_text = self.summarizer._extract_conversation_text(events)
        print(
            f"\n\n {invocation_ctx.agent.name} agent: conversation_text: {conversation_text}\n--------------------------------\n"
        )

        # Summarize events produced by this agent
        # create_session_summary_by_events is for agent-level summarization:
        # takes event list and session_id; returns summary text and compressed events
        summary_text, compressed_events = await self.summarizer.create_session_summary_by_events(events,
                                                                                                 session_id,
                                                                                                 ctx=invocation_ctx)

        # Append compressed events to the session
        # Usually far fewer events than raw, reducing token usage
        if compressed_events:
            invocation_ctx.session.events.extend(compressed_events)

        print(
            f"\n\n {invocation_ctx.agent.name} agent: after {len(invocation_ctx.session.events)} summary_text: {summary_text}\n--------------------------------\n"
        )
