# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent node action executor."""

import json
from typing import Any
from typing import Callable
from typing import Optional

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import Part

from .._callbacks import NodeCallbackContext
from .._callbacks import NodeCallbacks
from .._constants import STATE_KEY_LAST_RESPONSE
from .._constants import STATE_KEY_MESSAGES
from .._constants import STATE_KEY_NODE_RESPONSES
from .._constants import STATE_KEY_USER_INPUT
from .._event_writer import AsyncEventWriter
from .._event_writer import EventWriter
from .._node_config import NodeConfig
from .._state import State
from .._state_mapper import SubgraphResult
from ._base import BaseNodeAction


class AgentNodeAction(BaseNodeAction):
    """Executes sub-agent invocation with isolated state.

    This class invokes the sub-agent's run_async method with an isolated
    child state to prevent side effects on the parent state.

    Attributes:
        agent: Sub-agent instance
        node_config: Common node configuration
    """

    def __init__(
        self,
        node_id: str,
        agent: BaseAgent,
        node_config: NodeConfig,
        writer: EventWriter,
        async_writer: AsyncEventWriter,
        ctx: Optional[InvocationContext] = None,
        callback_ctx: Optional[NodeCallbackContext] = None,
        callbacks: Optional[NodeCallbacks] = None,
        isolated_messages: bool = False,
        input_from_last_response: bool = False,
        event_scope: Optional[str] = None,
        input_mapper: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        output_mapper: Optional[Callable[[dict[str, Any], SubgraphResult], Optional[dict[str, Any]]]] = None,
    ):
        """Initialize the agent node action.

        Args:
            node_id: Graph node ID
            agent: Sub-agent instance
            node_config: Common node configuration
            writer: EventWriter for high-frequency streaming text
            async_writer: AsyncEventWriter for lifecycle events
            ctx: Optional invocation context
            callback_ctx: Optional callback context from wrapper
            callbacks: Optional merged callbacks from wrapper
            isolated_messages: If True, child execution does not inherit parent message history.
            input_from_last_response: If True, map parent last_response to child user_input.
            event_scope: Optional branch scope segment for child agent events.
            input_mapper: Optional mapper from parent state to child state.
            output_mapper: Optional mapper from SubgraphResult to parent state update.
        """
        super().__init__(node_id, writer, async_writer, ctx)
        self.agent = agent
        self.node_id = node_id
        self.node_config = node_config
        self.callback_ctx = callback_ctx
        self.callbacks = callbacks
        self.isolated_messages = isolated_messages
        self.input_from_last_response = input_from_last_response
        self.event_scope = event_scope
        self.input_mapper = input_mapper
        self.output_mapper = output_mapper

    async def execute(self, state: State) -> dict[str, Any]:
        """Execute the sub-agent invocation.

        Args:
            state: Current state

        Returns:
            State update dictionary (delta pattern)
        """
        if self.agent is None:
            raise RuntimeError(f"Agent for node '{self.node_id}' is None.")

        # Build child state via input mapper.
        if self.input_mapper:
            child_state = self.input_mapper(dict(state))
        else:
            child_state = dict(state)

        # Optionally map parent last response to child user input.
        if self.input_from_last_response:
            last_response = state.get(STATE_KEY_LAST_RESPONSE, "")
            if last_response:
                child_state[STATE_KEY_USER_INPUT] = last_response

        parent_ctx: Optional[InvocationContext] = self.ctx

        if parent_ctx is None:
            raise RuntimeError(
                f"Agent node '{self.name}' requires InvocationContext but none was set. "
                "Pass context via config['configurable']['invocation_context'] when executing the graph.")

        child_scope = self.event_scope or self.agent.name
        child_branch = f"{parent_ctx.branch}.{child_scope}" if parent_ctx.branch else child_scope
        child_user_input = child_state.get(STATE_KEY_USER_INPUT, "")

        child_session = parent_ctx.session.model_copy(deep=True)
        child_session.state = dict(child_state)
        child_events = self._build_child_events(parent_ctx, child_user_input, child_branch)
        if hasattr(child_session, "events"):
            child_session.events = child_events
        if self.isolated_messages:
            child_session.state[STATE_KEY_MESSAGES] = []

        child_user_content = None
        if isinstance(child_user_input, str) and child_user_input:
            child_user_content = Content(
                role="user",
                parts=[Part.from_text(text=child_user_input)],
            )

        # Create an isolated invocation context for child execution.
        child_ctx = parent_ctx.model_copy(
            update={
                "agent": self.agent,
                "session": child_session,
                "branch": child_branch,
                "user_content": child_user_content,
                "event_actions": EventActions(),
                "callback_state": None,
                "override_messages": None,
            },
            deep=False,
        )

        # Execute agent/sub-agent chain (supports transfer_to_agent).
        last_response = ""
        final_state = dict(child_session.state)
        raw_state_delta: dict[str, Any] = {}
        try:
            root_agent = self._resolve_root_agent(self.agent)
            current_agent = self.agent
            while True:
                transfer_target: Optional[str] = None
                transfer_requested = False
                child_ctx.agent = current_agent

                async for event in current_agent.run_async(child_ctx):
                    await self._run_agent_event_callbacks(state, event)

                    if (not event.partial) and hasattr(child_session, "events"):
                        child_session.events.append(event.model_copy(deep=True))

                    if event.actions and event.actions.state_delta:
                        delta = dict(event.actions.state_delta)
                        raw_state_delta.update(delta)

                        if not self._is_graph_event(event):
                            final_state.update(delta)

                        candidate = delta.get(STATE_KEY_LAST_RESPONSE, "")
                        if isinstance(candidate, str) and candidate:
                            last_response = candidate

                    if (not self._is_graph_event(event)) and (
                            not event.partial) and event.content and event.content.parts:
                        text_parts = [part.text for part in event.content.parts if part.text]
                        if text_parts:
                            last_response = text_parts[-1]

                    if not event.visible or not event.is_model_visible():
                        if event.actions and event.actions.transfer_to_agent:
                            raise ValueError("Agent transfer requested but invisible is not allowed.")
                        continue

                    event_to_emit = event
                    if event.actions and event.actions.transfer_to_agent:
                        # Transfer is handled inside AgentNodeAction. Do not leak it to
                        # Runner, otherwise Runner may perform the transfer again.
                        event_to_emit = event.model_copy(deep=True)
                        if event_to_emit.actions:
                            event_to_emit.actions.transfer_to_agent = None
                        transfer_requested = True
                        transfer_target = event.actions.transfer_to_agent
                    self.writer.write_event(event_to_emit)

                    if transfer_requested:
                        break

                if not transfer_requested:
                    break

                target_agent = self._resolve_transfer_target(root_agent, transfer_target)
                if target_agent is None:
                    error_event = Event(
                        invocation_id=child_ctx.invocation_id,
                        author=current_agent.name,
                        error_message=f"Transfer target agent '{transfer_target}' not found",
                        error_code="transfer_target_not_found",
                        branch=child_ctx.branch,
                    )
                    await self._run_agent_event_callbacks(state, error_event)
                    if hasattr(child_session, "events"):
                        child_session.events.append(error_event.model_copy(deep=True))
                    if error_event.visible or error_event.is_model_visible():
                        self.writer.write_event(error_event)
                    break

                child_ctx.branch = self._build_transferred_branch(
                    current_branch=child_ctx.branch,
                    current_agent=current_agent,
                    target_agent=target_agent,
                    root_agent=root_agent,
                )
                current_agent = target_agent

            if not last_response:
                candidate = final_state.get(STATE_KEY_LAST_RESPONSE, "")
                if isinstance(candidate, str) and candidate:
                    last_response = candidate

        except Exception as e:
            raise RuntimeError(f"Agent node '{self.name}' execution failed: {e}") from e

        if last_response:
            final_state[STATE_KEY_LAST_RESPONSE] = last_response

        node_response: Any = last_response
        structured_output: Any = None
        if isinstance(self.agent, LlmAgent) and self.agent.output_schema is not None:
            node_response = json.loads(last_response)
            structured_output = node_response

        subgraph_result = SubgraphResult(
            last_response=last_response,
            final_state=final_state,
            raw_state_delta=raw_state_delta,
            structured_output=structured_output,
        )

        default_result = {
            STATE_KEY_LAST_RESPONSE: last_response,
            STATE_KEY_NODE_RESPONSES: {
                self.node_id: node_response
            },
            STATE_KEY_USER_INPUT: "",
        }

        if self.output_mapper:
            mapped = self.output_mapper(state, subgraph_result)
            if mapped is None:
                return {}
            if not isinstance(mapped, dict):
                raise TypeError(f"Output mapper for agent node '{self.node_id}' must return dict, "
                                f"got {type(mapped).__name__}.")
            if STATE_KEY_USER_INPUT not in mapped:
                mapped = dict(mapped)
                mapped[STATE_KEY_USER_INPUT] = ""
            return mapped

        return default_result

    def _build_child_events(
        self,
        parent_ctx: InvocationContext,
        child_user_input: Any,
        child_branch: str,
    ) -> list[Event]:
        parent_events = getattr(parent_ctx.session, "events", [])
        if self.isolated_messages:
            child_events: list[Event] = []
        else:
            child_events = [event.model_copy(deep=True) for event in parent_events]

        if isinstance(child_user_input, str) and child_user_input:
            child_events.append(
                Event(
                    invocation_id=parent_ctx.invocation_id,
                    author="user",
                    branch=child_branch,
                    content=Content(
                        role="user",
                        parts=[Part.from_text(text=child_user_input)],
                    ),
                ))
        return child_events

    async def _run_agent_event_callbacks(self, state: State, event: Event) -> None:
        if not self.callbacks or not self.callbacks.agent_event:
            return

        callback_ctx = self.callback_ctx or NodeCallbackContext(
            node_id=self.node_id,
            node_name=self.node_id,
            node_type="agent",
        )
        for callback in self.callbacks.agent_event:
            await callback(callback_ctx, state, event)

    @staticmethod
    def _resolve_root_agent(agent: BaseAgent) -> BaseAgent:
        root = getattr(agent, "root_agent", None)
        if root is not None:
            return root
        return agent

    @staticmethod
    def _resolve_transfer_target(root_agent: BaseAgent, target_name: Optional[str]) -> Optional[BaseAgent]:
        if not target_name:
            return None
        find_agent = getattr(root_agent, "find_agent", None)
        if callable(find_agent):
            return find_agent(target_name)
        if getattr(root_agent, "name", None) == target_name:
            return root_agent
        return None

    @staticmethod
    def _build_transferred_branch(
        *,
        current_branch: Optional[str],
        current_agent: BaseAgent,
        target_agent: BaseAgent,
        root_agent: BaseAgent,
    ) -> str:
        if current_agent.name == root_agent.name:
            return f"{current_agent.name}.{target_agent.name}"

        if current_branch:
            branch_parts = [root_agent.name]
            agent = target_agent
            agent_path: list[str] = []
            while agent is not None and agent != root_agent:
                agent_path.insert(0, agent.name)
                agent = agent.parent_agent
            if agent == root_agent:
                return ".".join(branch_parts + agent_path)

        return target_agent.name

    @staticmethod
    def _is_graph_event(event: Event) -> bool:
        """Check whether an event is a graph lifecycle/metadata event."""
        object_type = getattr(event, "object", None)
        return isinstance(object_type, str) and object_type.startswith("graph.")
