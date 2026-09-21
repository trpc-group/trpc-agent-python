# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Redis persistence for generated CodeAct implementations."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from datetime import timezone
from typing import Any
from typing_extensions import override

from trpc_agent_sdk.storage import RedisCommand
from trpc_agent_sdk.storage import RedisExpire
from trpc_agent_sdk.storage import RedisStorage

from ._base import BaseCodeActImplementationStore
from ._base import CodeActImplementation


class RedisCodeActImplementationStore(BaseCodeActImplementationStore):
    """Persist CodeAct versions through the shared :class:`RedisStorage`."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        storage: RedisStorage | None = None,
        is_async: bool = True,
        key_prefix: str = "trpc_agent:codeact",
        **storage_kwargs: Any,
    ) -> None:
        if storage is None and redis_url is None:
            raise ValueError("Redis CodeAct storage requires redis_url or storage")
        self._storage = storage or RedisStorage(
            redis_url=redis_url or "",
            is_async=is_async,
            **storage_kwargs,
        )
        self._owns_storage = storage is None
        self._key_prefix = key_prefix.rstrip(":")

    @override
    async def save_candidate(
        self,
        implementation: CodeActImplementation,
    ) -> CodeActImplementation:
        key = self._version_key(
            implementation.tool_name,
            implementation.contract_hash,
            implementation.version,
        )
        created = await self._command(
            "set",
            key,
            implementation.model_dump_json(),
            nx=True,
        )
        if created not in (True, b"OK", "OK"):
            stored = await self._read_implementation(key)
            if stored is None:
                raise RuntimeError("Redis CodeAct candidate write did not persist")
            if stored.code_hash != implementation.code_hash:
                raise ValueError(f"CodeAct version collision for {implementation.version!r}")
            implementation = stored
        await self._command(
            "sadd",
            self._versions_key(
                implementation.tool_name,
                implementation.contract_hash,
            ),
            implementation.version,
        )
        return implementation

    @override
    async def list_implementations(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> list[CodeActImplementation]:
        versions = await self._command(
            "smembers",
            self._versions_key(tool_name, contract_hash),
        ) or []
        values: list[CodeActImplementation] = []
        for value in versions:
            version = self._text(value)
            implementation = await self._read_implementation(self._version_key(tool_name, contract_hash, version))
            if implementation is not None:
                values.append(implementation)
        return sorted(values, key=lambda value: value.created_at)

    @override
    async def get_approved(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> CodeActImplementation | None:
        pointer_value = await self._command(
            "get",
            self._approved_key(tool_name, contract_hash),
        )
        if pointer_value is None:
            return None
        pointer = json.loads(self._text(pointer_value))
        version = pointer.get("version")
        if not isinstance(version, str):
            raise TypeError("Invalid Redis CodeAct approved pointer")
        implementation = await self._read_implementation(self._version_key(tool_name, contract_hash, version))
        if implementation is None:
            raise ValueError(f"Approved CodeAct implementation {version!r} is missing")
        return implementation

    @override
    async def approve(
        self,
        tool_name: str,
        contract_hash: str,
        version: str,
        *,
        approved_by: str | None = None,
    ) -> CodeActImplementation:
        implementation = await self._read_implementation(self._version_key(tool_name, contract_hash, version))
        if implementation is None:
            raise KeyError(f"Unknown CodeAct implementation version {version!r}")
        pointer = {
            "version": version,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": approved_by,
        }
        await self._command(
            "set",
            self._approved_key(tool_name, contract_hash),
            json.dumps(pointer, ensure_ascii=False, separators=(",", ":")),
        )
        return implementation

    @override
    async def close(self) -> None:
        if self._owns_storage:
            await self._storage.close()

    async def _read_implementation(
        self,
        key: str,
    ) -> CodeActImplementation | None:
        value = await self._command("get", key)
        if value is None:
            return None
        return CodeActImplementation.model_validate_json(self._text(value))

    async def _command(self, method: str, *args: Any, **kwargs: Any) -> Any:
        async with self._storage.create_db_session() as connection:
            return await self._storage.execute_command(
                connection,
                RedisCommand(
                    method=method,
                    args=args,
                    kwargs=kwargs,
                    expire=RedisExpire(),
                ),
            )

    def _scope_key(self, tool_name: str, contract_hash: str) -> str:
        tool_hash = hashlib.sha256(tool_name.encode()).hexdigest()[:16]
        contract_key = hashlib.sha256(contract_hash.encode()).hexdigest()
        scope = f"{tool_hash}:{contract_key}"
        return f"{self._key_prefix}:{{{scope}}}"

    def _versions_key(self, tool_name: str, contract_hash: str) -> str:
        return f"{self._scope_key(tool_name, contract_hash)}:versions"

    def _version_key(
        self,
        tool_name: str,
        contract_hash: str,
        version: str,
    ) -> str:
        if re.fullmatch(r"[0-9a-f]{16}", version) is None:
            raise ValueError(f"Invalid CodeAct implementation version {version!r}")
        return f"{self._scope_key(tool_name, contract_hash)}:version:{version}"

    def _approved_key(self, tool_name: str, contract_hash: str) -> str:
        return f"{self._scope_key(tool_name, contract_hash)}:approved"

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)
