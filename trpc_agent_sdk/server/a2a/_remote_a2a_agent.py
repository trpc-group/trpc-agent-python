# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Remote A2A agent that uses unprefixed metadata and artifact-first streaming.

This module provides the TrpcRemoteA2aAgent class which extends BaseAgent
to communicate with remote A2A agents via the standard A2A SDK client (a2a-sdk).
It supports agent card discovery and message exchange with remote A2A services
over HTTP, using unprefixed metadata keys and artifact-first streaming.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from typing import AsyncGenerator
from typing import List
from typing import Optional

import httpx

from a2a.client import A2ACardResolver
from a2a.client import A2AClient
from a2a.client.middleware import ClientCallContext
from a2a.types import AgentCard
from a2a.types import CancelTaskRequest
from a2a.types import Message
from a2a.types import MessageSendParams
from a2a.types import Role
from a2a.types import SendStreamingMessageRequest
from a2a.types import SendStreamingMessageResponse
from a2a.types import Task
from a2a.types import TaskArtifactUpdateEvent
from a2a.types import TaskIdParams
from a2a.types import TaskState
from a2a.types import TaskStatusUpdateEvent

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.telemetry import CustomTraceReporter
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._utils import get_metadata
from .converters import build_request_message_metadata
from .converters import convert_a2a_message_to_event
from .converters import convert_a2a_task_to_event
from .converters import convert_content_to_a2a_message
from .converters import convert_event_to_a2a_message


class TrpcRemoteA2aAgent(BaseAgent):
    """Agent that communicates with a remote A2A agent via the standard A2A SDK client.

    Supports agent-card discovery via HTTP, A2A message conversion, session state
    management, and streaming with artifact-first event ordering and unprefixed
    metadata keys.

    The agent requires:
    - agent_base_url: HTTP base URL for agent card discovery (if agent_card not provided)
    - name: Agent name (must be unique identifier)
    - description: Agent description (auto-populated from card if empty)
    - agent_card: Optional AgentCard object (if not provided, will be discovered)
    - a2a_client: Optional A2AClient object (if not provided, will be created)
    """

    agent_base_url: Optional[str] = None

    def __init__(
        self,
        name: str,
        description: str = "",
        agent_card: Optional[AgentCard] = None,
        a2a_client: Optional[A2AClient] = None,
        agent_base_url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, description=description, **kwargs)
        if not name or not name.strip():
            raise ValueError("name cannot be empty")
        if agent_card is None and a2a_client is None and (not agent_base_url or not agent_base_url.strip()):
            raise ValueError("Either agent_card, a2a_client, or agent_base_url must be provided")

        self.agent_base_url = agent_base_url.strip() if agent_base_url else None
        self._agent_card: Optional[AgentCard] = agent_card
        self._a2a_client: Optional[A2AClient] = a2a_client
        self._httpx_client: Optional[httpx.AsyncClient] = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize the client with agent card discovery (if needed).

        Returns:
            bool: True if initialization successful, False otherwise
        """
        if self._initialized:
            return True

        logger.debug("Initializing Remote A2A agent...")
        try:
            if self._httpx_client is None:
                self._httpx_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout=None))

                self._httpx_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout=None))

                self._httpx_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout=None))
            # add close method to class( needed define in class definition)

            if self._agent_card is None:
                if not self.agent_base_url:
                    raise ValueError("agent_base_url is required when agent_card is not provided")

                card_resolver = A2ACardResolver(
                    httpx_client=self._httpx_client,
                    base_url=self.agent_base_url,
                )
                self._agent_card = await card_resolver.get_agent_card()

            logger.debug("Agent Name: %s", self._agent_card.name)
            logger.debug("Description: %s", self._agent_card.description)
            logger.debug("Agent Card URL: %s", self._agent_card.url)
            logger.debug("Capabilities: %s", self._agent_card.capabilities.model_dump_json())

            if self._a2a_client is None:
                self._a2a_client = A2AClient(
                    httpx_client=self._httpx_client,
                    agent_card=self._agent_card,
                    url=self._agent_card.url or self.agent_base_url,
                )

            if not self.description and self._agent_card and self._agent_card.description:
                self.description = self._agent_card.description

            self._initialized = True
            logger.debug("Successfully initialized remote A2A agent: %s", self.name)
            return True

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to initialize remote A2A agent %s: %s", self.name, ex)
            return False

    async def _stream_with_cancel_check(
        self,
        ctx: InvocationContext,
        streaming_generator: AsyncGenerator[SendStreamingMessageResponse, None],
    ) -> AsyncGenerator[SendStreamingMessageResponse, None]:
        """Wrap a streaming generator with concurrent cancel checking."""
        cancel_event = await ctx.get_cancel_event()
        stream_iter = streaming_generator.__aiter__()

        while True:
            next_response_task = asyncio.create_task(stream_iter.__anext__())
            try:
                cancel_wait_task = asyncio.create_task(cancel_event.wait())
                done, pending = await asyncio.wait(
                    [next_response_task, cancel_wait_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, StopAsyncIteration):
                        pass

                if cancel_wait_task in done:
                    logger.info("Cancel event triggered during streaming wait")
                    raise RunCancelledException("Run cancelled while waiting for stream response")

                if next_response_task in done:
                    try:
                        yield next_response_task.result()
                    except StopAsyncIteration:
                        return
            except asyncio.CancelledError:
                next_response_task.cancel()
                try:
                    await next_response_task
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
                raise

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        await ctx.raise_if_cancelled()

        if not self._initialized:
            yield Event(
                author=self.name,
                error_message="Remote A2A agent is not initialized",
                invocation_id=ctx.invocation_id,
                branch=ctx.branch,
            )
            return

        a2a_message = self._build_outgoing_message(ctx)
        if not a2a_message:
            logger.warning("Failed to convert content to A2A message. Emitting empty event.")
            yield Event(author=self.name, content=Content(), invocation_id=ctx.invocation_id, branch=ctx.branch)
            return

        a2a_message.context_id = ctx.session.id
        request_meta = build_request_message_metadata(ctx)
        existing = getattr(a2a_message, "metadata", None) or {}
        if isinstance(existing, dict):
            request_meta.update(existing)
        a2a_message.metadata = request_meta

        metadata = None
        configuration = None
        if ctx.run_config:
            metadata = ctx.run_config.agent_run_config.get("metadata", None)
            configuration = ctx.run_config.agent_run_config.get("configuration", None)

        streaming_request = SendStreamingMessageRequest(
            id=str(uuid.uuid4()),
            params=MessageSendParams(message=a2a_message, metadata=metadata, configuration=configuration),
        )

        logger.debug("Sending A2A streaming request: %s", streaming_request)
        await ctx.raise_if_cancelled()

        trace_reporter = CustomTraceReporter(
            agent_name=self.name,
            model_prefix="a2a",
            tool_description_prefix="Remote A2A tool",
        )
        task_id = None

        out_headers: dict[str, str] = {}
        try:
            from opentelemetry.propagate import inject
            inject(out_headers)
        except Exception:  # pylint: disable=broad-except
            pass
        if ctx.user_id:
            out_headers["X-User-ID"] = ctx.user_id
        http_kwargs = {"headers": out_headers} if out_headers else {}
        call_context = ClientCallContext(state={"http_kwargs": http_kwargs})

        try:
            event_count = 0
            streaming_gen = self._a2a_client.send_message_streaming(
                streaming_request,
                context=call_context,
            )

            async for response in self._stream_with_cancel_check(ctx, streaming_gen):
                await ctx.raise_if_cancelled()
                event_count += 1
                result = response.root.result

                if task_id is None and hasattr(result, "task_id"):
                    task_id = result.task_id
                    logger.debug("Captured task_id for cancellation: %s", task_id)

                for event in self._events_from_response(result, event_count, ctx):
                    trace_reporter.trace_event(ctx, event)
                    yield event

            logger.debug("Streaming completed with %s events", event_count)

        except RunCancelledException:
            logger.info(
                "Remote A2A agent '%s' execution cancelled, sending cancel request to remote service",
                self.name,
            )
            if task_id:
                try:
                    cancel_request = CancelTaskRequest(
                        id=str(uuid.uuid4()),
                        params=TaskIdParams(id=task_id),
                    )
                    cancel_response = await self._a2a_client.cancel_task(cancel_request, context=call_context)
                    logger.info("Successfully sent cancel request for session_id: %s", ctx.session.id)
                    logger.debug("Cancel response: %s", cancel_response)
                except Exception as cancel_error:  # pylint: disable=broad-except
                    logger.warning("Failed to send cancel request to remote service: %s", cancel_error)
            else:
                logger.warning("No task_id captured, cannot send cancel request to remote service")
            raise

        except Exception as ex:  # pylint: disable=broad-except
            error_message = f"A2A streaming request failed: {ex}"
            logger.error(error_message, exc_info=True)
            yield Event(author=self.name,
                        error_message=error_message,
                        invocation_id=ctx.invocation_id,
                        branch=ctx.branch)

    def _build_outgoing_message(self, ctx: InvocationContext) -> Optional[Message]:
        """Build the outgoing A2A message from ctx.override_messages or session events."""
        if ctx.override_messages is not None:
            logger.debug("Using override_messages for remote A2A agent: %s", self.name)
            return convert_content_to_a2a_message(ctx.override_messages, role=Role.user)

        user_event = None
        for event in reversed(ctx.session.events):
            if event.author == "user" and event.content:
                user_event = event
                break
        if not user_event or not user_event.content or not user_event.content.parts:
            logger.warning("No content to send to remote A2A agent. Emitting empty event.")
            return None

        return convert_event_to_a2a_message(user_event, ctx, role=Role.user)

    def _build_message_from_artifact_event(self, event: TaskArtifactUpdateEvent) -> Message:
        artifact = event.artifact if hasattr(event, "artifact") else None
        if not artifact:
            return Message(role=Role.agent, parts=[])
        msg = Message(
            role=Role.agent,
            parts=artifact.parts or [],
            message_id=getattr(artifact, "artifact_id", "") or "",
        )
        msg.metadata = getattr(event, "metadata", None)
        return msg

    def _ensure_non_streaming_for_discrete_events(self, event: Event) -> None:
        if event.get_function_calls() or event.get_function_responses():
            event.partial = False
            return
        obj = getattr(event, "object", None)
        if obj in ("tool.response", "postprocessing.code_execution"):
            event.partial = False
            return
        if event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "code_execution_result", None) or getattr(part, "executable_code", None):
                    event.partial = False
                    return

    def _resolve_partial(self, metadata: Any) -> bool:
        """Resolve the 'partial' flag from event metadata, defaulting to True."""
        if not metadata:
            return True
        partial = get_metadata(metadata, "partial")
        if partial is None:
            return True
        if isinstance(partial, bool):
            return partial
        if isinstance(partial, str):
            return partial.strip().lower() == "true"
        return True

    def _events_from_response(self, result: Any, event_count: int, ctx: InvocationContext) -> List[Event]:
        """Produce TrpcAgent events from one streaming response."""
        events: List[Event] = []

        if isinstance(result, TaskArtifactUpdateEvent):
            artifact = result.artifact if hasattr(result, "artifact") else None
            last_chunk = result.last_chunk
            if artifact is not None and (artifact.parts or not last_chunk):
                if not artifact.parts and last_chunk:
                    logger.debug("[Event %s] Artifact last_chunk (empty), skip", event_count)
                else:
                    msg = self._build_message_from_artifact_event(result)
                    partial = self._resolve_partial(result.metadata)
                    event = convert_a2a_message_to_event(msg, author=self.name, invocation_context=ctx, partial=partial)
                    self._ensure_non_streaming_for_discrete_events(event)

                    if result.metadata:
                        streaming_tool_call = get_metadata(result.metadata, "streaming_tool_call")
                        if streaming_tool_call == "true" or streaming_tool_call is True:
                            if event.custom_metadata is None:
                                event.custom_metadata = {}
                            event.custom_metadata["streaming_tool_call"] = True
                            event.partial = True

                    events.append(event)
                    logger.debug("[Event %s] Artifact (yielded), last_chunk=%s", event_count, last_chunk)
            else:
                logger.debug("[Event %s] Artifact (no parts / closing), skip", event_count)

        elif isinstance(result, TaskStatusUpdateEvent):
            logger.debug("[Event %s] Status: %s", event_count, result.status.state)
            if not result.status.message:
                return events
            msg = result.status.message
            if msg.role == Role.user:
                return events

            state = result.status.state
            if state not in (TaskState.submitted, TaskState.working, TaskState.completed):
                partial = self._resolve_partial(result.metadata)
                ev = convert_a2a_message_to_event(msg, author=self.name, invocation_context=ctx, partial=partial)
                events.append(ev)
            return events

        elif isinstance(result, Task):
            logger.debug("[Event %s] Task: %s", event_count, result.id)
            events.append(convert_a2a_task_to_event(result, author=self.name, invocation_context=ctx))

        elif isinstance(result, Message):
            logger.debug("[Event %s] Message", event_count)
            events.append(convert_a2a_message_to_event(result, author=self.name, invocation_context=ctx))

        else:
            logger.warning("[Event %s] Unknown response type: %s", event_count, type(result))
            events.append(
                Event(
                    author=self.name,
                    content=Content(parts=[Part(text=f"Received unknown response type: {type(result).__name__}")]),
                    invocation_id=ctx.invocation_id,
                    branch=ctx.branch,
                ))

        return events
