# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Parallel Agent Implementation.

This module provides the ParallelAgent class which executes sub-agents concurrently
in isolated contexts. This is beneficial for scenarios requiring multiple perspectives
or approaches on a single task.

Classes:
    ParallelAgent: A shell agent that runs its sub-agents in parallel
"""

from __future__ import annotations

import asyncio
import sys
from typing import AsyncGenerator
from typing import List
from typing_extensions import override

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.utils import AsyncClosingContextManager

from ._base_agent import BaseAgent


def _create_branch_ctx_for_sub_agent(
    agent: AgentABC,
    sub_agent: AgentABC,
    invocation_context: InvocationContext,
) -> InvocationContext:
    """Create isolated branch for every sub-agent.

    Args:
        agent: The parent agent
        sub_agent: The sub-agent to create context for
        invocation_context: The original invocation context

    Returns:
        InvocationContext: A new isolated context for the sub-agent
    """
    invocation_context = invocation_context.model_copy()
    branch_suffix = f"{agent.name}.{sub_agent.name}"
    invocation_context.branch = (f"{invocation_context.branch}.{branch_suffix}"
                                 if invocation_context.branch else branch_suffix)
    return invocation_context


async def _merge_agent_run_pre_3_11(agent_runs: list[AsyncGenerator[Event, None]]) -> AsyncGenerator[Event, None]:
    """Merges the agent run event generator.
    This version works in Python 3.9 and 3.10 and uses custom replacement for
    asyncio.TaskGroup for tasks cancellation and exception handling.

    This implementation guarantees for each agent, it won't move on until the
    generated event is processed by upstream runner.

    Args:
        agent_runs: A list of async generators that yield events from each agent.

    Yields:
        Event: The next event from the merged generator.
    """
    sentinel = object()
    queue = asyncio.Queue()

    def propagate_exceptions(tasks):
        # Propagate exceptions and errors from tasks.
        for task in tasks:
            if task.done():
                # Ignore the result (None) of correctly finished tasks and re-raise
                # exceptions and errors.
                task.result()

    # Agents are processed in parallel.
    # Events for each agent are put on queue sequentially.
    async def process_an_agent(events_for_one_agent: AsyncGenerator[Event, None]):
        try:
            async for event in events_for_one_agent:
                resume_signal = asyncio.Event()
                await queue.put((event, resume_signal))
                # Wait for upstream to consume event before generating new events.
                await resume_signal.wait()
        finally:
            # Mark agent as finished.
            await queue.put((sentinel, None))

    tasks: List[asyncio.Task] = []
    try:
        for events_for_one_agent in agent_runs:
            tasks.append(asyncio.create_task(process_an_agent(events_for_one_agent)))

        sentinel_count = 0
        # Run until all agents finished processing.
        while sentinel_count < len(agent_runs):
            propagate_exceptions(tasks)
            entry: tuple[Event, asyncio.Event] = await queue.get()
            # Agent finished processing.
            if entry[0] is sentinel:
                sentinel_count += 1
            else:
                yield entry[0]
                # Signal to agent that event has been processed by runner and it can
                # continue now.
                entry[1].set()
    finally:
        for task in tasks:
            task.cancel()


async def _merge_agent_run(agent_runs: list[AsyncGenerator[Event, None]]) -> AsyncGenerator[Event, None]:
    """Merges the agent run event generator.

      This implementation guarantees for each agent, it won't move on until the
      generated event is processed by upstream runner.

      Args:
          agent_runs: A list of async generators that yield events from each agent.

      Yields:
          Event: The next event from the merged generator.
      """
    sentinel = object()
    queue = asyncio.Queue()

    # Agents are processed in parallel.
    # Events for each agent are put on queue sequentially.
    async def process_an_agent(events_for_one_agent: AsyncGenerator[Event, None]):
        try:
            async for event in events_for_one_agent:
                resume_signal = asyncio.Event()
                await queue.put((event, resume_signal))
                # Wait for upstream to consume event before generating new events.
                await resume_signal.wait()
        finally:
            # Mark agent as finished.
            await queue.put((sentinel, None))

    async with asyncio.TaskGroup() as tg:
        for events_for_one_agent in agent_runs:
            tg.create_task(process_an_agent(events_for_one_agent))

        sentinel_count = 0
        # Run until all agents finished processing.
        while sentinel_count < len(agent_runs):
            entry: tuple[Event, asyncio.Event] = await queue.get()
            # Agent finished processing.
            if entry[0] is sentinel:
                sentinel_count += 1
            else:
                yield entry[0]
                # Signal to agent that it should generate next event.
                entry[1].set()


class ParallelAgent(BaseAgent):
    """A shell agent that runs its sub-agents in parallel in isolated manner.

    This approach is beneficial for scenarios requiring multiple perspectives or
    attempts on a single task, such as:

    - Running different algorithms simultaneously
    - Generating multiple responses for review by a subsequent evaluation agent
    - Parallel processing of independent subtasks

    Example:
        ```python
        parallel_agent = ParallelAgent(
            name="multi_approach_solver",
            description="Solve problems using multiple approaches in parallel",
            sub_agents=[
                algorithm_a_agent,
                algorithm_b_agent,
                heuristic_agent
            ]
        )
        ```
    """

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Execute sub-agents in parallel with isolated contexts.

        Args:
            ctx: The invocation context for this agent

        Yields:
            Event: Events from all sub-agents merged as they become available
        """
        agent_runs: List[AsyncGenerator[Event, None]] = []
        for sub_agent in self.sub_agents:
            if not sub_agent:
                continue
            sub_agent_run = sub_agent.run_async(_create_branch_ctx_for_sub_agent(self, sub_agent, ctx))
            agent_runs.append(sub_agent_run)  # type: ignore
        try:
            # TODO remove if once Python <3.11 is no longer supported.
            if sys.version_info >= (3, 11):
                async with AsyncClosingContextManager(_merge_agent_run(agent_runs)) as agen:
                    async for event in agen:
                        yield event
            else:
                async with AsyncClosingContextManager(_merge_agent_run_pre_3_11(agent_runs)) as agen:
                    async for event in agen:
                        yield event
        finally:
            for sub_agent_run in agent_runs:
                await sub_agent_run.aclose()
