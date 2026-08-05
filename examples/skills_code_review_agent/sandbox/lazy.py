"""Lazy workspace runtime used to defer Docker startup until tool execution."""

import inspect
from threading import Lock
from typing import Callable
from typing import Optional

from trpc_agent_sdk.code_executors import BaseProgramRunner
from trpc_agent_sdk.code_executors import BaseWorkspaceFS
from trpc_agent_sdk.code_executors import BaseWorkspaceManager
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import WorkspaceCapabilities
from trpc_agent_sdk.context import InvocationContext

RuntimeFactory = Callable[[], BaseWorkspaceRuntime]


class LazySandboxRuntime(BaseWorkspaceRuntime):
    """Create the real sandbox runtime only when execution needs it."""

    def __init__(self, factory: RuntimeFactory) -> None:
        self._factory = factory
        self._runtime: BaseWorkspaceRuntime | None = None
        self._lock = Lock()

    @property
    def is_initialized(self) -> bool:
        """Return whether the backing sandbox has been created."""
        return self._runtime is not None

    def _get_runtime(self) -> BaseWorkspaceRuntime:
        if self._runtime is None:
            with self._lock:
                if self._runtime is None:
                    self._runtime = self._factory()
        return self._runtime

    def manager(
        self,
        ctx: Optional[InvocationContext] = None,
    ) -> BaseWorkspaceManager:
        return self._get_runtime().manager(ctx)

    def fs(
        self,
        ctx: Optional[InvocationContext] = None,
    ) -> BaseWorkspaceFS:
        return self._get_runtime().fs(ctx)

    def runner(
        self,
        ctx: Optional[InvocationContext] = None,
    ) -> BaseProgramRunner:
        return self._get_runtime().runner(ctx)

    def describe(
        self,
        ctx: Optional[InvocationContext] = None,
    ) -> WorkspaceCapabilities:
        del ctx
        return WorkspaceCapabilities(
            isolation="container",
            network_allowed=False,
            read_only_mount=True,
            streaming=True,
        )

    async def close(self) -> None:
        """Release an initialized provider without forcing lazy initialization."""
        if self._runtime is None:
            return
        close = getattr(self._runtime, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
