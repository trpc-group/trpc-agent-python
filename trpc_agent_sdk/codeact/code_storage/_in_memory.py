# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Process-local implementation store for tests and development."""

import asyncio
from typing_extensions import override

from ._base import BaseCodeActImplementationStore
from ._base import CodeActImplementation


class InMemoryCodeActImplementationStore(BaseCodeActImplementationStore):
    """Process-local implementation store for tests and development."""

    def __init__(self) -> None:
        self._implementations: dict[tuple[str, str, str], CodeActImplementation] = {}
        self._approved: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    @override
    async def save_candidate(
        self,
        implementation: CodeActImplementation,
    ) -> CodeActImplementation:
        async with self._lock:
            key = (
                implementation.tool_name,
                implementation.contract_hash,
                implementation.version,
            )
            self._implementations.setdefault(key, implementation)
            return self._implementations[key]

    @override
    async def list_implementations(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> list[CodeActImplementation]:
        async with self._lock:
            values = [
                value for (stored_tool, stored_contract, _), value in self._implementations.items()
                if stored_tool == tool_name and stored_contract == contract_hash
            ]
        return sorted(values, key=lambda value: value.created_at)

    @override
    async def get_approved(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> CodeActImplementation | None:
        async with self._lock:
            version = self._approved.get((tool_name, contract_hash))
            if version is None:
                return None
            return self._implementations.get((tool_name, contract_hash, version))

    @override
    async def approve(
        self,
        tool_name: str,
        contract_hash: str,
        version: str,
        *,
        approved_by: str | None = None,
    ) -> CodeActImplementation:
        del approved_by
        async with self._lock:
            key = (tool_name, contract_hash, version)
            implementation = self._implementations.get(key)
            if implementation is None:
                raise KeyError(f"Unknown CodeAct implementation version {version!r}")
            self._approved[(tool_name, contract_hash)] = version
            return implementation

    @override
    async def close(self) -> None:
        pass
