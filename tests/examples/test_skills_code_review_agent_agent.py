"""Tests for the optional model-driven code-review agent wrapper."""

from __future__ import annotations

import asyncio
import importlib
import sys
from typing import Any

import pytest

AGENT_MODULE = "examples.skills_code_review_agent.agent.agent"


def test_import_is_safe_when_container_runtime_is_unavailable(monkeypatch):
    """Import must not contact Docker; callers can still choose local explicitly."""
    from examples.skills_code_review_agent import agent as agent_package
    from examples.skills_code_review_agent.agent import tools as review_tools

    runtime_attempts: list[str] = []

    def unavailable_runtime(runtime: str = "container") -> Any:
        runtime_attempts.append(runtime)
        raise RuntimeError("Docker daemon is unavailable")

    # With the old eager ``root_agent = create_agent()`` this makes the import
    # fail exactly as an unavailable Docker daemon would.
    monkeypatch.setattr(review_tools, "create_workspace_runtime", unavailable_runtime)
    monkeypatch.delattr(agent_package, "agent", raising=False)
    monkeypatch.delitem(sys.modules, AGENT_MODULE, raising=False)

    agent_module = importlib.import_module(AGENT_MODULE)

    assert runtime_attempts == []
    assert "root_agent" not in agent_module.__dict__
    assert "root_agent" in dir(agent_module)

    tool_set = object()
    owned_runtime = object()

    class FakeRepository:
        workspace_runtime = owned_runtime

    repository = FakeRepository()
    selected_runtimes: list[str] = []

    tool_set_options: list[dict[str, Any]] = []

    def fake_tool_set_factory(runtime: str, **kwargs: Any):
        selected_runtimes.append(runtime)
        tool_set_options.append(kwargs)
        return tool_set, repository

    class FakeAgent:
        def __init__(self, **kwargs: Any):
            self.kwargs = kwargs
            self.bound_runtime = None

        def bind_owned_workspace_runtime(self, runtime: Any) -> None:
            self.bound_runtime = runtime

    model = object()
    monkeypatch.setattr(
        agent_module, "create_review_skill_tool_set", fake_tool_set_factory
    )
    monkeypatch.setattr(agent_module, "_create_model", lambda: model)
    monkeypatch.setattr(agent_module, "CodeReviewAgent", FakeAgent)

    intercepts = []
    policy = object()
    explicit_agent = agent_module.create_agent(
        "local",
        intercept_sink=intercepts.append,
        execution_policy=policy,
    )

    assert selected_runtimes == ["local"]
    assert explicit_agent.kwargs["model"] is model
    assert explicit_agent.kwargs["tools"] == [tool_set]
    assert explicit_agent.kwargs["skill_repository"] is repository
    assert explicit_agent.bound_runtime is owned_runtime
    assert tool_set_options == [
        {"intercept_sink": intercepts.append, "execution_policy": policy}
    ]

    # Frameworks can keep discovering ``root_agent`` by name. The environment
    # is read only at first access, so importing the module remains side-effect
    # free and local fallback remains explicit/configurable.
    monkeypatch.setenv(agent_module.ROOT_AGENT_RUNTIME_ENV, "local")
    monkeypatch.setattr(agent_module, "_root_agent", None)
    lazy_root = agent_module.root_agent

    assert selected_runtimes == ["local", "local"]
    assert agent_module.root_agent is lazy_root


def test_managed_agent_close_is_idempotent_and_root_can_be_recreated(monkeypatch):
    agent_module = importlib.import_module(AGENT_MODULE)

    class FakeRuntime:
        def __init__(self) -> None:
            self.destroy_calls = 0

        async def destroy(self) -> None:
            self.destroy_calls += 1

    runtime = FakeRuntime()
    managed = agent_module.CodeReviewAgent(
        name="managed_review",
        model=lambda _request: None,
    )
    managed.bind_owned_workspace_runtime(runtime)
    monkeypatch.setattr(agent_module, "_root_agent", managed)

    asyncio.run(agent_module.close_root_agent())
    asyncio.run(managed.close())

    assert runtime.destroy_calls == 1
    assert managed.closed is True
    assert agent_module._root_agent is None


def test_create_agent_releases_owned_runtime_when_agent_construction_fails(
    monkeypatch,
):
    agent_module = importlib.import_module(AGENT_MODULE)

    class FakeRuntime:
        def __init__(self) -> None:
            self.destroy_calls = 0

        async def destroy(self) -> None:
            self.destroy_calls += 1

    runtime = FakeRuntime()

    class FakeRepository:
        workspace_runtime = runtime

    monkeypatch.setattr(agent_module, "_create_model", lambda: object())
    monkeypatch.setattr(
        agent_module,
        "create_review_skill_tool_set",
        lambda *_args, **_kwargs: (object(), FakeRepository()),
    )

    class FailingAgent:
        def __init__(self, **_kwargs: Any) -> None:
            raise ValueError("constructor failed")

    monkeypatch.setattr(agent_module, "CodeReviewAgent", FailingAgent)

    with pytest.raises(ValueError, match="constructor failed"):
        agent_module.create_agent("container")

    assert runtime.destroy_calls == 1


def test_async_agent_factory_releases_owned_runtime_on_toolset_failure(
    monkeypatch,
):
    agent_module = importlib.import_module(AGENT_MODULE)

    class FakeRuntime:
        def __init__(self) -> None:
            self.destroy_calls = 0

        async def destroy(self) -> None:
            self.destroy_calls += 1

    runtime = FakeRuntime()

    async def fake_runtime_factory(_runtime: str):
        return runtime

    def fail_toolset(*_args: Any, **_kwargs: Any):
        raise ValueError("toolset failed")

    monkeypatch.setattr(agent_module, "_create_model", lambda: object())
    monkeypatch.setattr(
        agent_module,
        "create_workspace_runtime_async",
        fake_runtime_factory,
    )
    monkeypatch.setattr(agent_module, "create_review_skill_tool_set", fail_toolset)

    async def create() -> None:
        with pytest.raises(ValueError, match="toolset failed"):
            await agent_module.create_agent_async("cube")

    asyncio.run(create())

    assert runtime.destroy_calls == 1


def test_lazy_cube_root_in_running_loop_points_to_async_factory(monkeypatch):
    agent_module = importlib.import_module(AGENT_MODULE)
    monkeypatch.setattr(agent_module, "_root_agent", None)
    monkeypatch.setenv(agent_module.ROOT_AGENT_RUNTIME_ENV, "cube")

    async def get_root() -> None:
        with pytest.raises(RuntimeError, match="create_agent_async"):
            agent_module.get_root_agent()

    asyncio.run(get_root())
