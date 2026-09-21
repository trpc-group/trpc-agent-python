# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""File implementation store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing_extensions import override

from trpc_agent_sdk.storage import FileStorage

from ._base import BaseCodeActImplementationStore
from ._base import CodeActImplementation


class FileCodeActImplementationStore(BaseCodeActImplementationStore):
    """JSON implementation store built on :class:`FileStorage`."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        storage: FileStorage | None = None,
    ) -> None:
        if storage is None and root is None:
            raise ValueError("File CodeAct storage requires root or storage")
        self._storage = storage or FileStorage(root or "")
        self._owns_storage = storage is None
        self._lock = asyncio.Lock()

    @override
    async def save_candidate(
        self,
        implementation: CodeActImplementation,
    ) -> CodeActImplementation:
        async with self._lock:
            key = self._version_key(
                implementation.tool_name,
                implementation.contract_hash,
                implementation.version,
            )
            stored = await self._read_implementation(key)
            if stored is not None:
                if stored.code_hash != implementation.code_hash:
                    raise ValueError(f"CodeAct version collision for {implementation.version!r}")
                return stored
            await self._storage.write_text(
                key,
                implementation.model_dump_json(indent=2),
                overwrite=False,
            )
            stored = await self._read_implementation(key)
            if stored is None:
                raise RuntimeError("CodeAct candidate write did not persist")
            return stored

    @override
    async def list_implementations(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> list[CodeActImplementation]:
        directory = self._contract_key(tool_name, contract_hash) / "versions"
        paths = await self._storage.list_files(directory, "*.json")
        values = [
            implementation for path in paths if (implementation := await self._read_implementation(path)) is not None
        ]
        return sorted(values, key=lambda value: value.created_at)

    @override
    async def get_approved(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> CodeActImplementation | None:
        content = await self._storage.read_text(self._approved_key(tool_name, contract_hash))
        if content is None:
            return None
        data = json.loads(content)
        version = data.get("version")
        if not isinstance(version, str):
            raise TypeError("Invalid CodeAct approved pointer")
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
        async with self._lock:
            implementation = await self._read_implementation(self._version_key(tool_name, contract_hash, version))
            if implementation is None:
                raise KeyError(f"Unknown CodeAct implementation version {version!r}")
            pointer = {
                "version": version,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "approved_by": approved_by,
            }
            await self._storage.write_text(
                self._approved_key(tool_name, contract_hash),
                json.dumps(pointer, ensure_ascii=False, indent=2),
            )
            return implementation

    @override
    async def close(self) -> None:
        if self._owns_storage:
            await self._storage.close()

    @staticmethod
    def _contract_key(tool_name: str, contract_hash: str) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name).strip("._")
        if not safe_name:
            safe_name = "code_act_tool"
        name_hash = hashlib.sha256(tool_name.encode()).hexdigest()[:8]
        contract_dir = hashlib.sha256(contract_hash.encode()).hexdigest()
        return Path(f"{safe_name}-{name_hash}") / contract_dir

    def _version_key(
        self,
        tool_name: str,
        contract_hash: str,
        version: str,
    ) -> Path:
        if re.fullmatch(r"[0-9a-f]{16}", version) is None:
            raise ValueError(f"Invalid CodeAct implementation version {version!r}")
        return (self._contract_key(tool_name, contract_hash) / "versions" / f"{version}.json")

    def _approved_key(self, tool_name: str, contract_hash: str) -> Path:
        return self._contract_key(tool_name, contract_hash) / "approved.json"

    async def _read_implementation(
        self,
        key: str | Path,
    ) -> CodeActImplementation | None:
        content = await self._storage.read_text(key)
        if content is None:
            return None
        return CodeActImplementation.model_validate_json(content)
