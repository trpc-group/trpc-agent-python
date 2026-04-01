# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Agent Tool Adapter Implementation.

This module implements the AgentTool class which serves as an adapter between the
TRPC Agent framework and the tooling system. Key responsibilities include:

1. Agent Integration:
   - Wrapping agent instances as callable tools
   - Handling input/output schema conversions
   - Managing execution lifecycle

2. Context Management:
   - State synchronization between agent and parent sessions
   - Artifact forwarding between contexts
   - Event processing pipeline

3. Schema Handling:
   - Automatic function declaration generation
   - Input validation for schema-based agents
   - Output formatting for structured responses

Key Features:
- Seamless integration of agents into tool workflows
- Support for both structured and unstructured I/O
- Thread-safe context operations
- Built-in artifact persistence

Example Usage:
    agent = MyAgent()
    tool = AgentTool(agent=agent)
    result = await tool.run_async(
        args={"input": "value"},
        tool_context=InvocationContext(...)
    )
"""

from __future__ import annotations

from typing import Any
from typing import Optional
from typing_extensions import override

from pydantic import model_validator

from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base_tool import BaseTool
from .utils import build_function_declaration

# Constant suffix appended to app_name when AgentTool creates a runner
# This is used to identify AgentTool spans in Langfuse tracing
AGENT_TOOL_APP_NAME_SUFFIX = "_trpc_agent_tool_"


class AgentTool(BaseTool):
    """A tool that wraps an agent.

  This tool allows an agent to be called as a tool within a larger application.
  The agent's input schema is used to define the tool's input parameters, and
  the agent's output is returned as the tool's result.

  Attributes:
    agent: The agent to wrap.
    skip_summarization: Whether to skip summarization of the agent output.
  """

    def __init__(self,
                 agent: AgentABC,
                 skip_summarization: bool = False,
                 filters_name: Optional[list[str]] = None,
                 filters: Optional[list[BaseFilter]] = None):
        self.agent = agent
        self.skip_summarization: bool = skip_summarization
        super().__init__(name=agent.name, description=agent.description, filters_name=filters_name, filters=filters)

    @model_validator(mode='before')
    @classmethod
    def populate_name(cls, data: dict) -> Any:
        data['name'] = data['agent'].name
        return data

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        from trpc_agent_sdk.agents import LlmAgent
        if isinstance(self.agent, LlmAgent) and self.agent.input_schema:
            result = build_function_declaration(func=self.agent.input_schema, variant=self.api_variant)
        else:
            result = FunctionDeclaration(
                parameters=Schema(
                    type=Type.OBJECT,
                    properties={
                        'request': Schema(type=Type.STRING, ),
                    },
                    required=['request'],
                ),
                description=self.agent.description,
                name=self.name,
            )

        # Set response schema based on agent's output schema
        if isinstance(self.agent, LlmAgent) and self.agent.output_schema:
            # Agent has structured output schema - response is an object
            result.response = Schema(type=Type.OBJECT)
        else:
            # Agent returns text - response is a string
            result.response = Schema(type=Type.STRING)

        result.name = self.name
        return result

    @override
    async def _run_async_impl(
        self,
        *,
        args: dict[str, Any],
        tool_context: InvocationContext,
    ) -> Any:
        try:
            from trpc_agent_sdk.agents import LlmAgent
            if self.skip_summarization:
                tool_context.event_actions.skip_summarization = True
            if isinstance(self.agent, LlmAgent) and self.agent.input_schema:
                input_value = self.agent.input_schema.model_validate(args)
            else:
                input_value = args['request']

            if isinstance(self.agent, LlmAgent) and self.agent.input_schema:
                if isinstance(input_value, dict):
                    input_value = self.agent.input_schema.model_validate(input_value)
                if not isinstance(input_value, self.agent.input_schema):
                    raise ValueError(f'Input value {input_value} is not of type'
                                     f' `{self.agent.input_schema}`.')
                content = Content(
                    role='user',
                    parts=[Part.from_text(text=input_value.model_dump_json(exclude_none=True))],
                )
            else:
                content = Content(
                    role='user',
                    parts=[Part.from_text(text=str(input_value))],
                )
            # Import Runner here to avoid circular import
            from trpc_agent_sdk.runners import Runner

            runner = Runner(
                app_name=f"{self.agent.name}{AGENT_TOOL_APP_NAME_SUFFIX}",
                agent=self.agent,
                # It seems we don't need re-use artifact_service if we forward below.
                artifact_service=tool_context.artifact_service,
                session_service=InMemorySessionService(),
                memory_service=InMemoryMemoryService(),
            )
            session: Session = await runner.session_service.create_session(
                app_name=f"{self.agent.name}{AGENT_TOOL_APP_NAME_SUFFIX}",
                user_id='tmp_user',
                state=tool_context.state.to_dict(),
            )

            last_event = None
            async for event in runner.run_async(user_id=session.user_id, session_id=session.id, new_message=content):
                # Forward state delta to parent session.
                assert isinstance(event, Event)
                if event.actions.state_delta:
                    tool_context.state.update(event.actions.state_delta)
                last_event = event

            if runner.artifact_service:
                # Forward all artifacts to parent session.
                artifact_names = await runner.artifact_service.list_artifact_keys(artifact_id=ArtifactId(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=session.id,
                ), )
                for artifact_name in artifact_names:
                    if artifact := await runner.artifact_service.load_artifact(artifact_id=ArtifactId(
                            app_name=session.app_name,
                            user_id=session.user_id,
                            session_id=session.id,
                            filename=artifact_name), ):
                        await tool_context.save_artifact(filename=artifact_name, artifact=artifact)

            await runner.close()

            if not last_event or not last_event.content or not last_event.content.parts:
                return ''
            if isinstance(self.agent, LlmAgent) and self.agent.output_schema:
                merged_text = '\n'.join([p.text for p in last_event.content.parts if p.text])
                tool_result = self.agent.output_schema.model_validate_json(merged_text).model_dump(exclude_none=True)
            else:
                tool_result = '\n'.join([p.text for p in last_event.content.parts if p.text])
            return tool_result
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error running agent tool: %s", ex, exc_info=True)
            raise ex
