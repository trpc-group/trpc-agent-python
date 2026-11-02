"""Target base classes and target loading.

This module is part of the generic layer: it must not import specific agent
implementations. Targets are loaded from any importable module that exposes
``TARGET``.
"""

from __future__ import annotations

import importlib
import inspect
import os
from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import AsyncIterator
from typing import Awaitable
from typing import Callable
from typing import List
from typing import Union

from trpc_agent_sdk.tools import BaseTool

from ._config import AgentBenchConf

ToolsFactory = Callable[
    [],
    Union[List[BaseTool], Awaitable[List[BaseTool]]],
]


async def resolve_tools(factory: ToolsFactory) -> List[BaseTool]:
    """Resolve a sync or async tools factory into a concrete tool list."""

    tools = factory()
    if inspect.isawaitable(tools):
        tools = await tools
    return list(tools)


class AgentTarget(ABC):
    """Base contract every concrete target adapter must implement."""

    name: str
    import_module: str

    @abstractmethod
    async def prepare(self, *, conf: AgentBenchConf) -> Any:
        """Build the target runtime using ``conf.base_url``.

        The target owns its runtime, session service, and any temporary
        directories. It must clean them up in :meth:`cleanup`.
        """

    @abstractmethod
    async def instantiate(
        self,
        prepared: Any,
        *,
        tools_factory: ToolsFactory,
    ) -> Any:
        """Construct the agent.

        ``tools_factory`` is called as part of construction so benchmark timing
        includes tool creation. It may return tools directly or via an awaitable.
        """

    @abstractmethod
    async def handle(
        self,
        prepared: Any,
        *,
        tools_factory: ToolsFactory,
    ) -> "AgentHandle":
        """Wrap the constructed agent in the uniform turn interface."""

    @abstractmethod
    async def cleanup(self, prepared: Any) -> None:
        """Close session services and target runtime; remove target temp paths."""


class AgentHandle(ABC):
    """Base class for the per-turn handle returned by targets."""

    @abstractmethod
    async def run_turn(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message: str,
    ) -> AsyncIterator[Any]:
        """Run one turn and yield framework events."""


def load_target(name: str | None = None) -> AgentTarget:
    """Load a target from a Python module path.

    ``name`` defaults to ``BENCH_TARGET``. The module must expose ``TARGET``.
    """

    target_name = name or os.environ.get("BENCH_TARGET")
    if not target_name:
        raise RuntimeError("BENCH_TARGET is not set")
    try:
        module = importlib.import_module(target_name)
    except ImportError as exc:
        raise RuntimeError(f"failed to import target module {target_name!r}: {exc}") from exc
    target = getattr(module, "TARGET", None)
    if target is None:
        raise RuntimeError(f"target module {target_name!r} does not define TARGET")
    return target
