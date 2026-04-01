# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""HTTP/SSE runner bridge for generated agent service."""

from typing import Awaitable, Callable, Union

from trpc.log import logger
from trpc.plugin import PluginInitPos
from trpc.plugin import PluginType
from trpc.plugin import register_plugin
from trpc.utils import get_current_process_var
from trpc.utils import set_current_process_var

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.configs import RunConfig
{% if has_memory_search_tools %}
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import MemoryServiceConfig
{% endif %}
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part
from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph._events import (
    EventUtils,
    ExecutionPhase,
    ModelExecutionMetadata,
    NodeExecutionMetadata,
    ToolExecutionMetadata,
)


class AgentRunner:
    """Conversation runner reused by HTTP handlers."""

    def __init__(self, agent: BaseAgent):
        self.agent = agent
        self.app_name = "generated_agent_service"
        self.session_service = InMemorySessionService()
{% if has_memory_search_tools %}
        self.memory_service = InMemoryMemoryService(memory_service_config=MemoryServiceConfig(enabled=True))
{% endif %}
        self.send_func: Callable[[Union[str, bytes]], Awaitable[None]] | None = None

    def _create_runner(self) -> Runner:
        return Runner(
            app_name=self.app_name,
            agent=self.agent,
            session_service=self.session_service,
{% if has_memory_search_tools %}
            memory_service=self.memory_service,
{% endif %}
        )

    async def _get_last_response(self, user_id: str, session_id: str) -> str:
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if session is None or session.state is None:
            return ""
        value = session.state.get(STATE_KEY_LAST_RESPONSE, "")
        return value if isinstance(value, str) else ""

    async def _send_message(self, message_text: str, user_id: str, session_id: str) -> None:
        if self.send_func is None:
            raise RuntimeError("send_func is not initialized")

        logger.info("User: %s", message_text)
        user_content = Content(parts=[Part(text=message_text)])
        runner = self._create_runner()
        streaming = False
        stream_author = ""

        async def flush_stream_line() -> None:
            nonlocal streaming, stream_author
            if not streaming:
                return
            await self.send_func("\n")
            streaming = False
            stream_author = ""

        try:
            async for event in runner.run_async(
                    user_id=user_id,
                    session_id=session_id,
                    new_message=user_content,
                    run_config=RunConfig(),
            ):
                if event is None:
                    continue

                error_message = getattr(event, "error_message", "")
                if isinstance(error_message, str) and error_message:
                    await flush_stream_line()
                    await self.send_func(f"  Error: {error_message}\n")
                    continue

                node_meta = NodeExecutionMetadata.from_event(event)
                if node_meta:
                    await flush_stream_line()
                    if node_meta.phase == ExecutionPhase.START:
                        await self.send_func(
                            f"  [Node start] node_type={node_meta.node_type}, node_name={node_meta.node_id}\n")
                    elif node_meta.phase == ExecutionPhase.COMPLETE:
                        await self.send_func(
                            f"  [Node done ] node_type={node_meta.node_type}, node_name={node_meta.node_id}\n")
                    elif node_meta.phase == ExecutionPhase.ERROR:
                        await self.send_func(
                            f"  [Node error] node_type={node_meta.node_type}, node_name={node_meta.node_id}\n")
                        if node_meta.error:
                            await self.send_func(f"    Error: {node_meta.error}\n")

                tool_meta = ToolExecutionMetadata.from_event(event)
                if tool_meta:
                    await flush_stream_line()
                    if tool_meta.phase == ExecutionPhase.START:
                        await self.send_func(f"  [Tool start] {tool_meta.tool_name} (id={tool_meta.tool_id})\n")
                        if tool_meta.input_args:
                            await self.send_func(f"    Args   : {tool_meta.input_args}\n")
                    elif tool_meta.phase == ExecutionPhase.COMPLETE:
                        await self.send_func(f"  [Tool done ] {tool_meta.tool_name} (id={tool_meta.tool_id})\n")
                        if tool_meta.output_result:
                            await self.send_func(f"    Result : {tool_meta.output_result}\n")
                        if tool_meta.error:
                            await self.send_func(f"    Error  : {tool_meta.error}\n")

                model_meta = ModelExecutionMetadata.from_event(event)
                if model_meta:
                    await flush_stream_line()
                    if model_meta.phase == ExecutionPhase.START:
                        await self.send_func(f"  [Model start] {model_meta.model_name} ({model_meta.node_id})\n")
                    elif model_meta.phase == ExecutionPhase.COMPLETE:
                        await self.send_func(f"  [Model done ] {model_meta.model_name} ({model_meta.node_id})\n")

                if not EventUtils.is_graph_event(event) and event.content and event.content.parts:
                    current_author = event.author if event.author else "unknown"
                    if event.partial:
                        for part in event.content.parts:
                            if not part.text:
                                continue
                            if (not streaming) or (stream_author != current_author):
                                await flush_stream_line()
                                streaming = True
                                stream_author = current_author
                                await self.send_func(f"  [{current_author}] ")
                            await self.send_func(part.text)
                        continue

                    await flush_stream_line()
                    for part in event.content.parts:
                        if part.thought:
                            continue
                        if part.function_call:
                            await self.send_func(
                                f"  [{current_author}] [Function call] {part.function_call.name}({part.function_call.args})\n")
                        elif part.function_response:
                            await self.send_func(
                                f"  [{current_author}] [Function result] {part.function_response.response}\n")
                elif not event.content or event.author == "user":
                    continue

            await flush_stream_line()
            # Uncomment this block if you want to print the final full text once
            # (in addition to streamed partial output above).
            # final_output = await self._get_last_response(user_id=user_id, session_id=session_id)
            # if final_output:
            #     await self.send_func(f"  {final_output}\n")
        finally:
            await runner.close()

    async def conversation(self, message_text: str, user_id: str, session_id: str) -> None:
        if message_text.strip().lower() == "end":
            logger.info("Conversation ended by client input.")
            return
        await self._send_message(message_text, user_id, session_id)


_AGENT_RUNNER_KEY = "generated_http_agent_runner"


@register_plugin(PluginType.USER_DEFINED, "generated_http_agent_runner", init_pos=PluginInitPos.WORKER)
def setup_agent_runner_to_worker():
    """Setup worker-local agent runner plugin."""
    from agent.agent import root_agent
    set_current_process_var(_AGENT_RUNNER_KEY, AgentRunner(root_agent))


def get_agent_runner() -> AgentRunner:
    """Fetch worker-local agent runner plugin."""
    runner = get_current_process_var(_AGENT_RUNNER_KEY)
    if not isinstance(runner, AgentRunner):
        raise RuntimeError("AgentRunner plugin is not initialized")
    return runner
