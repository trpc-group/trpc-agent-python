# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
"""A2A agent executor that uses unprefixed metadata and artifact-first streaming."""

from __future__ import annotations

import inspect
import uuid
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional
from typing import Union
from typing_extensions import override

from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import Artifact
from a2a.types import TaskArtifactUpdateEvent
from a2a.types import TaskState
from pydantic import BaseModel
from trpc_agent_sdk.cancel import SessionKey
from trpc_agent_sdk.cancel import is_run_cancelled
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.runners import Runner

from .._utils import get_metadata
from ..converters import convert_a2a_request_to_trpc_agent_run_args
from ..converters import convert_event_to_a2a_events
from ..converters import create_cancellation_event
from ..converters import create_completed_status_event
from ..converters import create_exception_status_event
from ..converters import create_final_status_event
from ..converters import create_submitted_status_event
from ..converters import create_working_status_event
from ..converters import get_user_session_id
from ._task_result_aggregator import TaskResultAggregator

UserIdExtractor = Callable[[RequestContext], Union[str, Awaitable[str]]]

EventCallback = Callable[[Event, RequestContext], Union[Optional[Event], Awaitable[Optional[Event]]]]


class TrpcA2aAgentExecutorConfig(BaseModel):
    """Configuration for TrpcA2aAgentExecutor.

    Attributes:
        cancel_wait_timeout: Maximum seconds to wait for cancellation. Default 1.0.
        user_id_extractor: Optional callback to extract user_id from RequestContext.
        event_callback: Optional callback invoked for each TrpcAgent Event before
            conversion to A2A events. Can be sync or async. Receives (Event, RequestContext).
            Return the event to continue, a modified event to alter behavior, or None to
            skip this event entirely. Useful for filtering, logging, or augmenting events
            (e.g. detecting streaming tool calls via event.is_streaming_tool_call()).
    """

    model_config = {"arbitrary_types_allowed": True}

    cancel_wait_timeout: float = 1.0
    user_id_extractor: Optional[UserIdExtractor] = None
    event_callback: Optional[EventCallback] = None


class TrpcA2aAgentExecutor(AgentExecutor):
    """Executor that converts TrpcAgent events to A2A events using unprefixed
    metadata and artifact-first streaming.
    """

    def __init__(
        self,
        *,
        runner: Runner | Callable[..., Runner | Awaitable[Runner]],
        config: Optional[TrpcA2aAgentExecutorConfig] = None,
    ):
        super().__init__()
        self._runner = runner
        self._config = config or TrpcA2aAgentExecutorConfig()

    @property
    def _user_id_extractor(self) -> Optional[UserIdExtractor]:
        return self._config.user_id_extractor if self._config else None

    async def _resolve_runner(self) -> Runner:
        if isinstance(self._runner, Runner):
            return self._runner
        if callable(self._runner):
            result = self._runner()
            if inspect.iscoroutine(result):
                resolved = await result
            else:
                resolved = result
            self._runner = resolved
            return resolved
        raise TypeError(f"Runner must be a Runner instance or callable, got {type(self._runner)}")

    def _get_user_session_from_task_metadata(
        self,
        context: RequestContext,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Extract (app_name, user_id, session_id) from task metadata written by execute()."""
        if not context.current_task or not context.current_task.metadata:
            return None, None, None
        metadata = context.current_task.metadata
        return (
            get_metadata(metadata, "app_name"),
            get_metadata(metadata, "user_id"),
            get_metadata(metadata, "session_id"),
        )

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        runner = await self._resolve_runner()

        app_name_meta, user_id, session_id = self._get_user_session_from_task_metadata(context)
        if user_id and session_id:
            logger.info(
                "Canceling task %s using metadata: app_name=%s, user_id=%s, session_id=%s",
                context.task_id,
                app_name_meta,
                user_id,
                session_id,
            )
        else:
            user_id, session_id = await get_user_session_id(context, self._user_id_extractor)
            logger.info(
                "Canceling task %s using fallback: user_id=%s, session_id=%s",
                context.task_id,
                user_id,
                session_id,
            )

        timeout = self._config.cancel_wait_timeout if self._config else 1.0
        success = await runner.cancel_run_async(user_id, session_id, timeout=timeout)

        if success:
            logger.info("Cancel requested for user_id=%s, session_id=%s", user_id, session_id)
        else:
            logger.warning(
                "No active run found for task %s, user_id=%s, session_id=%s",
                context.task_id,
                user_id,
                session_id,
            )
            await event_queue.enqueue_event(
                create_cancellation_event(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    message_text="No active task found to cancel",
                ))

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        token = None
        try:
            call_context = getattr(context, "call_context", None)
            state = getattr(call_context, "state", None) if call_context else None
            headers = state.get("headers") if isinstance(state, dict) else {}
            if isinstance(headers, dict) and headers:
                from opentelemetry.propagate import extract
                from opentelemetry.context import attach
                token = attach(extract(headers))
        except Exception:  # pylint: disable=broad-except
            pass

        try:
            if not context.message:
                raise ValueError("A2A request must have a message")

            if not context.current_task:
                await event_queue.enqueue_event(
                    create_submitted_status_event(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        message=context.message,
                    ))

            try:
                user_id, session_id = await get_user_session_id(context, self._user_id_extractor)
                logger.info("Execute request for user_id: %s, session_id: %s", user_id, session_id)

                runner = await self._resolve_runner()

                session_key = SessionKey(runner.app_name, user_id, session_id)
                if await is_run_cancelled(session_key):
                    logger.warning("Session %s is cancelled, rejecting new execution", session_id)
                    await event_queue.enqueue_event(
                        create_cancellation_event(
                            task_id=context.task_id,
                            context_id=context.context_id,
                            message_text="Session was cancelled",
                        ))
                    return

                await self._handle_request(context, event_queue)
            except Exception as ex:  # pylint: disable=broad-except
                logger.error("Error handling A2A request: %s", ex, exc_info=True)
                try:
                    except_event = create_exception_status_event(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        message_text=str(ex),
                    )
                    if except_event.status and except_event.status.message:
                        await event_queue.enqueue_event(except_event.status.message)
                except Exception as enqueue_error:  # pylint: disable=broad-except
                    logger.error("Failed to publish failure event: %s", enqueue_error, exc_info=True)
        finally:
            if token is not None:
                try:
                    from opentelemetry.context import detach
                    detach(token)
                except Exception:  # pylint: disable=broad-except
                    pass

    async def _handle_request(self, context: RequestContext, event_queue: EventQueue):
        runner = await self._resolve_runner()
        run_args = await convert_a2a_request_to_trpc_agent_run_args(context, self._user_id_extractor)

        session = await self._prepare_session(run_args, runner)
        agent_context = new_agent_context()

        invocation_context = runner._new_invocation_context(
            session=session,
            new_message=run_args["new_message"],
            run_config=run_args["run_config"],
            agent_context=agent_context,
        )

        working_meta: dict[str, Any] = {
            "app_name": runner.app_name,
            "user_id": run_args["user_id"],
            "session_id": run_args["session_id"],
        }
        await event_queue.enqueue_event(
            create_working_status_event(
                task_id=context.task_id,
                context_id=context.context_id,
                metadata=working_meta,
            ))

        aggregator = TaskResultAggregator()
        event_callback = self._config.event_callback if self._config else None
        async for trpc_event in runner.run_async(**run_args):
            from trpc_agent_sdk.events import AgentCancelledEvent
            if isinstance(trpc_event, AgentCancelledEvent):
                await event_queue.enqueue_event(
                    create_cancellation_event(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        message_text="Task was cancelled",
                    ))
                return

            if event_callback is not None:
                result = event_callback(trpc_event, context)
                if inspect.isawaitable(result):
                    result = await result
                if result is None:
                    continue
                trpc_event = result

            for a2a_event in convert_event_to_a2a_events(
                    trpc_event,
                    invocation_context,
                    context.task_id,
                    context.context_id,
                    on_event=aggregator.process_event,
            ):
                await event_queue.enqueue_event(a2a_event)

        if (aggregator.task_state == TaskState.working and aggregator.task_status_message is not None
                and aggregator.task_status_message.parts):
            final_meta: dict[str, Any] = {"partial": False}
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    task_id=context.task_id,
                    last_chunk=True,
                    context_id=context.context_id,
                    artifact=Artifact(
                        artifact_id=str(uuid.uuid4()),
                        parts=aggregator.task_status_message.parts,
                    ),
                    metadata=final_meta,
                ))
            await event_queue.enqueue_event(
                create_completed_status_event(
                    task_id=context.task_id,
                    context_id=context.context_id,
                ))
        else:
            await event_queue.enqueue_event(
                create_final_status_event(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    state=aggregator.task_state,
                    message=aggregator.task_status_message,
                ))

    async def _prepare_session(self, run_args: dict[str, Any], runner: Runner):
        session_id = run_args["session_id"]
        user_id = run_args["user_id"]
        session = await runner.session_service.get_session(
            app_name=runner.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if session is None:
            session = await runner.session_service.create_session(
                app_name=runner.app_name,
                user_id=user_id,
                state={},
                session_id=session_id,
            )
            run_args["session_id"] = session.id
        return session
