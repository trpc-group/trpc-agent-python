"""Storage boundary for Advanced Memory tenant namespaces.

Backends expose logical records rather than filesystem paths so a future Redis
implementation can preserve the same tenant and session semantics.
"""

from __future__ import annotations

from typing import Protocol

from trpc_agent_sdk.sessions.compact._paths import MemoryScope
from trpc_agent_sdk.sessions.compact._runtime import ScopedAdvancedMemoryRuntime


class AdvancedMemoryStorageBackend(Protocol):
    """Create storage views isolated to an application user."""

    def for_scope(self, scope: MemoryScope) -> ScopedAdvancedMemoryRuntime:
        """Return the tenant-bound storage view."""


class LocalAdvancedMemoryStorageBackend:
    """Adapt the file-backed runtime to the storage backend boundary."""

    def __init__(self, runtime: object) -> None:
        self._runtime = runtime

    def for_scope(self, scope: MemoryScope) -> ScopedAdvancedMemoryRuntime:
        """Return a file-backed scope without exposing local path mechanics."""
        return self._runtime.for_scope(scope.app_name, scope.user_id)
