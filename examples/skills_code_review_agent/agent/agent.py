"""Optional LlmAgent wrapper for the code-review skill.

The tested CLI in ``run_agent.py`` is deterministic and does not require model
credentials. This module mirrors the repository's agent examples for users who
want a normal LlmAgent that can call the bundled SkillToolSet.

Importing this module deliberately does not create a workspace runtime. The
framework-compatible ``root_agent`` attribute is initialized lazily on first
access, using ``CODE_REVIEW_AGENT_RUNTIME`` (``container`` by default).
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Callable
from types import TracebackType
from typing import Any

from pydantic import PrivateAttr
from typing_extensions import Self

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel

from .config import get_model_config
from .filtering import ReviewExecutionFilter
from .models import FilterDecision
from .prompts import INSTRUCTION
from .tools import (
    _destroy_workspace_runtime_sync,
    create_review_skill_tool_set,
    create_workspace_runtime_async,
)

ROOT_AGENT_RUNTIME_ENV = "CODE_REVIEW_AGENT_RUNTIME"
_root_agent: CodeReviewAgent | None = None

# Keep the conventional name visible to type checkers without binding it at
# runtime. Module ``__getattr__`` below creates it only when a framework or
# caller actually asks for it.
root_agent: CodeReviewAgent


class CodeReviewAgent(LlmAgent):
    """LLM review agent that explicitly owns and releases its sandbox runtime.

    ``create_agent()`` creates a dedicated workspace runtime. Long-running
    services must therefore either use the agent as an async context manager or
    call :meth:`close` when the review scope ends.
    """

    _owned_workspace_runtime: Any = PrivateAttr(default=None)
    _closed: bool = PrivateAttr(default=False)

    def bind_owned_workspace_runtime(self, runtime: Any) -> None:
        """Attach the runtime created for this agent exactly once."""
        if self._owned_workspace_runtime is not None:
            raise RuntimeError("code-review agent already owns a workspace runtime")
        self._owned_workspace_runtime = runtime

    @property
    def closed(self) -> bool:
        """Whether the owned runtime has been successfully released."""
        return self._closed

    async def close(self) -> None:
        """Release the owned Container/Cube runtime; safe to call repeatedly."""
        if self._closed:
            return
        runtime = self._owned_workspace_runtime
        if runtime is not None:
            destroy = getattr(runtime, "destroy", None)
            if callable(destroy):
                released = destroy()
                if inspect.isawaitable(released):
                    await released
            else:
                # Bare Container runtimes currently expose only their client's
                # private cleanup hook. NetworkIsolatedWorkspaceRuntime offers
                # a public destroy facade, but keep this fallback for injected
                # runtimes used by embedders and tests.
                container_client = getattr(runtime, "container", None)
                cleanup = getattr(container_client, "_cleanup_container", None)
                if callable(cleanup):
                    released = cleanup()
                    if inspect.isawaitable(released):
                        await released
        self._closed = True

    async def destroy(self) -> None:
        """Alias for callers that use sandbox-style lifecycle terminology."""
        await self.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


def _create_model() -> LLMModel:
    """Create a model from the standard example environment variables."""
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def _build_agent(
    *,
    model: LLMModel,
    skill_tool_set: Any,
    skill_repository: Any,
) -> CodeReviewAgent:
    """Construct and bind an agent after its owned runtime is ready."""
    agent = CodeReviewAgent(
        name="skills_code_review_agent",
        description=(
            "Automatic code review agent using Skills, sandbox scripts and "
            "structured reports."
        ),
        model=model,
        instruction=INSTRUCTION,
        tools=[skill_tool_set],
        skill_repository=skill_repository,
    )
    agent.bind_owned_workspace_runtime(skill_repository.workspace_runtime)
    return agent


async def _destroy_workspace_runtime_async(workspace_runtime: Any) -> None:
    """Release an async-factory-owned runtime while preserving its error."""
    destroy = getattr(workspace_runtime, "destroy", None)
    if callable(destroy):
        released = destroy()
        if inspect.isawaitable(released):
            await released
        return
    container_client = getattr(workspace_runtime, "container", None)
    cleanup = getattr(container_client, "_cleanup_container", None)
    if callable(cleanup):
        released = cleanup()
        if inspect.isawaitable(released):
            await released


def create_agent(
    runtime: str = "container",
    *,
    intercept_sink: Callable[[FilterDecision], None] | None = None,
    execution_policy: ReviewExecutionFilter | None = None,
) -> CodeReviewAgent:
    """Create an LlmAgent wired to the bundled code-review Skill.

    Args:
        runtime: workspace runtime for skill execution. Defaults to a
            network-disabled container; ``local`` is a development fallback.
        intercept_sink: Optional task-scoped callback for denied tool calls.
        execution_policy: Optional policy instance shared by all skill tools.
    """
    # Resolve model configuration before creating a container, so invalid
    # credentials/configuration cannot leak a just-created runtime.
    model = _create_model()
    skill_tool_set, skill_repository = create_review_skill_tool_set(
        runtime,
        intercept_sink=intercept_sink,
        execution_policy=execution_policy,
    )
    try:
        return _build_agent(
            model=model,
            skill_tool_set=skill_tool_set,
            skill_repository=skill_repository,
        )
    except BaseException:
        _destroy_workspace_runtime_sync(skill_repository.workspace_runtime)
        raise


async def create_agent_async(
    runtime: str = "cube",
    *,
    intercept_sink: Callable[[FilterDecision], None] | None = None,
    execution_policy: ReviewExecutionFilter | None = None,
) -> CodeReviewAgent:
    """Create a managed review agent from an async application.

    This is the supported Cube/E2B entry point when an event loop is already
    running. The runtime remains owned by the returned agent and is released by
    ``await agent.close()``.
    """
    model = _create_model()
    workspace_runtime = await create_workspace_runtime_async(runtime)
    try:
        skill_tool_set, skill_repository = create_review_skill_tool_set(
            runtime,
            workspace_runtime=workspace_runtime,
            intercept_sink=intercept_sink,
            execution_policy=execution_policy,
        )
        return _build_agent(
            model=model,
            skill_tool_set=skill_tool_set,
            skill_repository=skill_repository,
        )
    except BaseException:
        try:
            await _destroy_workspace_runtime_async(workspace_runtime)
        except BaseException:  # noqa: BLE001,S110 - preserve construction error
            # Preserve the construction failure. Runtime cleanup remains
            # best-effort on this already-failing path.
            pass
        raise


def get_root_agent() -> CodeReviewAgent:
    """Return the lazily initialized framework root agent.

    Set ``CODE_REVIEW_AGENT_RUNTIME`` before the first access to select
    ``container``, ``cube`` or the explicit development fallback ``local``.
    """
    global _root_agent
    if _root_agent is None:
        runtime = os.getenv(ROOT_AGENT_RUNTIME_ENV, "container")
        if runtime == "cube":
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError(
                    "lazy root_agent cannot create Cube inside a running event "
                    "loop; await create_agent_async('cube') instead, or initialize "
                    "root_agent before starting the loop"
                )
        _root_agent = create_agent(runtime)
    return _root_agent


async def close_root_agent() -> None:
    """Release and clear the lazily created framework root agent."""
    global _root_agent
    agent = _root_agent
    if agent is None:
        return
    await agent.close()
    _root_agent = None


def __getattr__(name: str) -> CodeReviewAgent:
    """Provide the conventional ``root_agent`` without import-time I/O."""
    if name == "root_agent":
        return get_root_agent()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Advertise the lazy attribute to framework discovery and introspection."""
    return sorted({*globals(), "root_agent"})
