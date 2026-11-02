"""Public benchmark API and target contracts."""

from __future__ import annotations

import asyncio

import pytest

from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import FunctionTool

import trpc_agent_bench.core as core
from trpc_agent_bench.core import AgentBenchConf
from trpc_agent_bench.core import AgentTarget
from trpc_agent_bench.core import BenchProfile
from trpc_agent_bench.core import FunctionBlock
from trpc_agent_bench.core import ModelConf
from trpc_agent_bench.core import Reply
from trpc_agent_bench.core import TextBlock
from trpc_agent_bench.core import Turn
from trpc_agent_bench.core import resolve_tools
from trpc_agent_bench.core import teardown
from trpc_agent_bench.core import _setup


def _add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def test_public_core_api_is_minimal() -> None:
    assert set(core.__all__) == {
        "AgentBenchConf",
        "AgentHandle",
        "AgentTarget",
        "BenchProfile",
        "BenchSetup",
        "FunctionBlock",
        "ModelConf",
        "PerformanceEval",
        "PerformanceResult",
        "Reply",
        "TextBlock",
        "ToolsFactory",
        "Turn",
        "drain",
        "ensure_turn_completed",
        "get_concurrency",
        "get_iterations",
        "resolve_tools",
        "run_benchmarks",
        "setup",
        "teardown",
    }
    assert not hasattr(core, "MockOpenAIServer")
    assert not hasattr(core, "load_target")
    assert not hasattr(core, "script_spec")
    assert not hasattr(core, "write_result")


def test_core_exports_script_types() -> None:
    assert Reply == TextBlock | list[FunctionBlock]
    assert Turn == list[Reply]


def test_resolve_tools_accepts_sync_and_async_factories() -> None:

    def sync_factory() -> list[BaseTool]:
        return [FunctionTool(_add)]

    async def async_factory() -> list[BaseTool]:
        return [FunctionTool(_add)]

    assert isinstance(asyncio.run(resolve_tools(sync_factory))[0], BaseTool)
    assert isinstance(asyncio.run(resolve_tools(async_factory))[0], BaseTool)


def test_agent_target_is_abstract() -> None:
    with pytest.raises(TypeError):
        AgentTarget()


def test_bench_profile_defaults() -> None:
    profile = BenchProfile()

    assert profile.usable is True
    assert profile.supports_concurrency is False
    assert profile.requires_iterations is True
    assert profile.skip_reason == ""


def test_setup_and_teardown_own_server_and_target_resources(monkeypatch: pytest.MonkeyPatch, ) -> None:
    captured = {}

    class FakeServer:

        def __init__(self, turns, conf):
            captured["turns"] = turns
            captured["model_conf"] = conf
            self.base_url = None
            self.started = False
            self.stopped = False

        def start(self) -> str:
            self.started = True
            self.base_url = "http://mock.test/v1"
            return self.base_url

        def stop(self) -> None:
            self.stopped = True

    class FakeTarget(AgentTarget):
        name = "fake"
        import_module = "fake"

        def __init__(self) -> None:
            self.seen_conf = None
            self.cleaned = None

        async def prepare(self, *, conf):
            self.seen_conf = conf
            return "prepared"

        async def instantiate(self, prepared, *, tools_factory):
            return None

        async def handle(self, prepared, *, tools_factory):
            return None

        async def cleanup(self, prepared):
            self.cleaned = prepared

    target = FakeTarget()
    monkeypatch.setattr(_setup, "MockOpenAIServer", FakeServer)
    monkeypatch.setattr(_setup, "load_target", lambda: target)

    conf = AgentBenchConf(model_conf=ModelConf(request_delay_ms=25, chunk_delay_ms=5), )
    box = asyncio.run(_setup.setup([[TextBlock()]], conf=conf))

    assert box.target is target
    assert box.conf is conf
    assert box.prepared == "prepared"
    assert conf.base_url == "http://mock.test/v1"
    assert isinstance(box._server, FakeServer)
    assert box._server.started is True
    assert captured["model_conf"] is conf.model_conf

    asyncio.run(teardown(box))

    assert target.cleaned == "prepared"
    assert box._server.stopped is True


def test_setup_failure_resets_base_url_and_stops_server(monkeypatch: pytest.MonkeyPatch, ) -> None:
    servers = []

    class FakeServer:

        def __init__(self, turns, conf):
            self.stopped = False
            servers.append(self)

        def start(self) -> str:
            return "http://mock.test/v1"

        def stop(self) -> None:
            self.stopped = True

    class BadTarget:

        async def prepare(self, *, conf):
            raise RuntimeError("prepare failed")

    monkeypatch.setattr(_setup, "MockOpenAIServer", FakeServer)
    monkeypatch.setattr(_setup, "load_target", lambda: BadTarget())

    conf = AgentBenchConf()
    with pytest.raises(RuntimeError, match="prepare failed"):
        asyncio.run(_setup.setup([[TextBlock()]], conf=conf))

    assert conf.base_url is None
    assert servers[0].stopped is True
