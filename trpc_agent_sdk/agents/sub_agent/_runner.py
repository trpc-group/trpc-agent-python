# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Sub-agent construction and execution via a nested Runner.

This mirrors the pattern used by ``trpc_agent_sdk.tools._agent_tool.AgentTool``
but applies stricter isolation: the parent's session/state/memory/callbacks are
**not** shared into the sub-agent. Artifacts are forwarded back to the parent
context so files produced by the sub-agent remain accessible to the orchestrator.

Sub-agent metadata (``_is_subagent`` / ``_subagent_type`` / ``_parent_invocation_id``)
is threaded into the spawned run via ``Runner.run_async(..., agent_context=...)``.
``AgentContext.with_metadata`` is the existing mechanism — no new fields added.
"""

from __future__ import annotations

from typing import Any
from typing import Optional
from typing import Union

from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.agents._llm_agent import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.exceptions import RunCancelledException
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._archetype import SubAgentArchetype
from ._constants import ISOLATION_DEFAULTS
from ._constants import SUBAGENT_APP_NAME_SUFFIX
from ._constants import SUBAGENT_USER_ID


class _BorrowedToolSet(BaseToolSet):
    """Wraps a parent-owned ToolSet for use in a sub-agent.

    Proxies get_tools() to the inner instance but makes close() a no-op,
    preventing sub_runner.close() from tearing down the parent's connections.
    """

    def __init__(self, inner: BaseToolSet) -> None:
        super().__init__()
        self._inner = inner

    async def get_tools(self, invocation_context=None):
        return await self._inner.get_tools(invocation_context)

    async def close(self) -> None:
        pass  # borrowed — lifecycle owned by parent runner


def _materialize_tools(tools: tuple) -> list:
    """Convert archetype tool items (instances or factories) to instances."""
    out: list = []
    for t in tools:
        if isinstance(t, (BaseTool, BaseToolSet)):
            out.append(t)
        elif callable(t):
            out.append(t())
        else:
            raise TypeError(f"archetype tool item {t!r} is neither a BaseTool/BaseToolSet "
                            f"instance nor a zero-arg factory")
    return out


def _resolve_model(agent_config, parent_ctx: InvocationContext) -> Any:
    if agent_config is not None and agent_config.model:
        return agent_config.model
    parent_model = getattr(parent_ctx.agent, "model", None)
    if parent_model:
        return parent_model
    raise ValueError("dynamic_agent: cannot resolve sub-agent model. Provide "
                     "SubAgentConfig.model, or set a model on the parent agent.")


def _resolve_skill_repository(tools: list) -> Any:
    """Find a SkillToolSet in *tools* and return its repository, else None.

    Couples skill_repository to the presence of SkillToolSet so that skill
    capability and skill metadata travel together. Looks through
    _BorrowedToolSet wrappers so inherited skill toolsets work too.
    """
    from trpc_agent_sdk.skills import SkillToolSet
    for t in tools:
        inner = t._inner if isinstance(t, _BorrowedToolSet) else t
        if isinstance(inner, SkillToolSet):
            return inner.repository
    return None


def _is_user_text_event(event: Any) -> bool:
    """Return True if *event* is a user message containing plain text."""
    if getattr(event, "author", None) != "user":
        return False
    parts = getattr(event.content, "parts", None) if getattr(event, "content", None) else None
    if not parts:
        return False
    return any(getattr(p, "text", None) for p in parts)


def _event_is_model_visible(event: Any) -> bool:
    """Return True if *event* is model-visible.

    ``Event.is_model_visible`` is a method, so it must be called.
    """
    vis = getattr(event, "is_model_visible", None)
    if callable(vis):
        return vis()
    return bool(vis) if vis is not None else True


def _collect_parent_events(parent_ctx: InvocationContext, max_parent_history_turns: Optional[int]) -> list:
    """Collect model-visible parent events, limited to the last *max_parent_history_turns* turns.

    A turn starts with a user text message.  If *max_parent_history_turns* is ``None``,
    all available events are returned.
    """
    events = getattr(parent_ctx.session, "events", None) or []
    if not events:
        return []

    # Keep only model-visible events that have content.
    visible = [e for e in events if _event_is_model_visible(e) and getattr(e, "content", None)]

    if max_parent_history_turns is None:
        return visible
    if not max_parent_history_turns:
        return []

    # Count turns backward from the end.
    turn_start_idx = 0
    turn_count = 0
    for i in range(len(visible) - 1, -1, -1):
        if _is_user_text_event(visible[i]):
            turn_count += 1
            if turn_count >= max_parent_history_turns:
                turn_start_idx = i
                break

    return visible[turn_start_idx:]


def _build_sub_agent(
    archetype: SubAgentArchetype,
    parent_ctx: InvocationContext,
    agent_config=None,
    tool_filter: Optional[list] = None,
) -> LlmAgent:
    if archetype.tools is None:
        # Inherit the full tool surface of the parent agent. BaseTool instances
        # are shared directly (stateless). BaseToolSet instances are wrapped in
        # _BorrowedToolSet so sub_runner.close() cannot tear down the parent's
        # connections (e.g. MCPToolset sessions).
        parent_tools = getattr(parent_ctx.agent, 'tools', []) or []
        tools = [_BorrowedToolSet(t) if isinstance(t, BaseToolSet) else t for t in parent_tools]
    else:
        tools = _materialize_tools(archetype.tools)

    # Always strip SpawnSubAgentTool and DynamicAgentTool from the sub-agent's
    # tool surface, preventing sub-agents from spawning further sub-agents
    # (1-level cap).
    tools = [t for t in tools if type(t).__name__ not in ("DynamicAgentTool", "SpawnSubAgentTool")]

    # Apply optional name-based tool filter from the LLM. BaseToolSet wrappers
    # are always kept (they are infrastructure, not selectable by name).
    if tool_filter is not None:
        name_map = {}
        base_sets: list = []
        for t in tools:
            if isinstance(t, _BorrowedToolSet):
                base_sets.append(t)
                continue
            name = getattr(t, 'name', None)
            if name:
                name_map[name] = t
        filtered = [name_map[n] for n in tool_filter if n in name_map]
        tools = filtered + base_sets

    # archetype.name may contain hyphens (e.g. "general-purpose"); LlmAgent.name
    # must be a Python identifier, so normalize hyphens to underscores.
    safe_name = archetype.name.replace("-", "_")

    parent = parent_ctx.agent

    llm_kwargs: dict = {}
    llm_kwargs["name"] = f"subagent_{safe_name}"
    llm_kwargs["description"] = archetype.description
    llm_kwargs["instruction"] = archetype.instruction
    llm_kwargs["model"] = _resolve_model(agent_config, parent_ctx)
    llm_kwargs["tools"] = tools

    if agent_config is not None and agent_config.generate_content_config is not None:
        llm_kwargs["generate_content_config"] = agent_config.generate_content_config
    else:
        llm_kwargs["generate_content_config"] = getattr(parent, "generate_content_config", None)

    if agent_config is not None and agent_config.parallel_tool_calls is not None:
        llm_kwargs["parallel_tool_calls"] = agent_config.parallel_tool_calls
    else:
        llm_kwargs["parallel_tool_calls"] = getattr(parent, "parallel_tool_calls", False)

    # Detect SkillToolSet in tools to populate skill_repository.
    llm_kwargs["skill_repository"] = _resolve_skill_repository(tools)

    llm_kwargs.update(ISOLATION_DEFAULTS)
    return LlmAgent(**llm_kwargs)


async def _forward_artifacts(sub_runner, sub_session, parent_ctx: InvocationContext) -> None:
    """Copy artifacts produced by the sub-agent into the parent context.

    Mirrors the artifact forwarding done by AgentTool — without it, files
    written by the sub-agent become unreachable once the sub-runner closes.
    """
    if not sub_runner.artifact_service:
        return
    artifact_id = ArtifactId(
        app_name=sub_session.app_name,
        user_id=sub_session.user_id,
        session_id=sub_session.id,
    )
    keys = await sub_runner.artifact_service.list_artifact_keys(artifact_id=artifact_id)
    for filename in keys:
        artifact = await sub_runner.artifact_service.load_artifact(artifact_id=ArtifactId(
            app_name=sub_session.app_name,
            user_id=sub_session.user_id,
            session_id=sub_session.id,
            filename=filename,
        ), )
        if artifact:
            await parent_ctx.save_artifact(filename=filename, artifact=artifact)


def _extract_final_text(last_event) -> str:
    if not last_event or not last_event.content or not last_event.content.parts:
        return ""
    return "\n".join(p.text for p in last_event.content.parts if getattr(p, "text", None))


async def run_subagent(
    *,
    parent_ctx: InvocationContext,
    archetype: SubAgentArchetype,
    prompt: str,
    agent_config=None,
    tool_filter: Optional[list] = None,
) -> Union[str, dict]:
    """Spawn an isolated sub-agent and return its final assistant text.

    Returns:
        Final assistant text on success, ``"[sub-agent cancelled]"`` if the
        run was cancelled, or ``{"status": "error", "message": ...}`` on
        unexpected exceptions. Errors are not raised back to the parent so
        the orchestrator can decide how to react.
    """
    # Imported lazily to mirror AgentTool and avoid a circular import at module load.
    from trpc_agent_sdk.runners import Runner

    try:
        sub_agent = _build_sub_agent(archetype, parent_ctx, agent_config=agent_config, tool_filter=tool_filter)
    except Exception as ex:  # noqa: BLE001
        logger.error("sub-agent build failed: %s", ex, exc_info=True)
        return {"status": "error", "message": str(ex)}

    parent_app_name = getattr(parent_ctx.session, "app_name", "trpc_app")
    sub_app_name = f"{parent_app_name}{SUBAGENT_APP_NAME_SUFFIX}{archetype.name}"

    sub_runner = Runner(
        app_name=sub_app_name,
        agent=sub_agent,
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        artifact_service=parent_ctx.artifact_service,
        enable_post_turn_processing=False,
    )

    last_event = None
    max_turns_reached = False
    try:
        sub_session = await sub_runner.session_service.create_session(
            app_name=sub_app_name,
            user_id=SUBAGENT_USER_ID,
            state={},
        )

        # Inject parent conversation history if configured.
        if agent_config is not None and agent_config.include_parent_history:
            parent_events = _collect_parent_events(parent_ctx, agent_config.max_parent_history_turns)
            for event in parent_events:
                await sub_runner.session_service.append_event(sub_session, event)

        max_turns = agent_config.max_turns if agent_config is not None else None
        turn_count = 0

        content = Content(role="user", parts=[Part.from_text(text=prompt)])
        async for event in sub_runner.run_async(
                user_id=sub_session.user_id,
                session_id=sub_session.id,
                new_message=content,
        ):
            last_event = event
            # Count LLM calls (one non-partial event per request, including
            # those with tool calls).  Aligns with claw-code-agent.
            if event.content and not event.partial and not event.is_error():
                if event.content.role == "model":
                    turn_count += 1
                    if max_turns is not None and turn_count >= max_turns:
                        max_turns_reached = True
                        break
            # Strict isolation: do NOT propagate event.actions.state_delta
            # to the parent context (this is the deliberate divergence from
            # AgentTool's behavior).

        await _forward_artifacts(sub_runner, sub_session, parent_ctx)
    except RunCancelledException:
        return "[sub-agent cancelled]"
    except Exception as ex:  # noqa: BLE001
        logger.error("sub-agent run failed: %s", ex, exc_info=True)
        return {"status": "error", "message": str(ex)}
    finally:
        try:
            await sub_runner.close()
        except Exception as close_ex:  # noqa: BLE001
            logger.warning("sub-agent runner close failed: %s", close_ex)

    result = _extract_final_text(last_event)
    if max_turns_reached:
        note = "[sub-agent stopped: max turns reached]"
        return f"{result}\n\n{note}" if result else note
    return result


__all__ = ["run_subagent"]
