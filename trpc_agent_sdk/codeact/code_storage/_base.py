# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Versioned persistence for generated CodeAct function implementations."""

from __future__ import annotations

import abc
import ast
import hashlib
import json
from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any

from pydantic import Field
from pydantic import BaseModel
from pydantic import ConfigDict


class CodeActImplementationPolicy(str, Enum):
    """Select how a CodeActTool obtains its implementation."""

    DYNAMIC = "dynamic"
    PREFER_APPROVED = "prefer_approved"
    APPROVED_ONLY = "approved_only"


class CodeActImplementation(BaseModel):
    """One immutable generated implementation candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str
    contract_hash: str
    capability_hash: str
    version: str
    code_hash: str
    code_cells: tuple[str, ...]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        tool_name: str,
        contract_hash: str,
        capability_hash: str,
        code_cells: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> CodeActImplementation:
        """Create a content-addressed candidate."""
        if not code_cells:
            raise ValueError("A CodeAct implementation requires at least one cell")
        for cell in code_cells:
            ast.parse(cell, mode="exec")
        code_hash = hashlib.sha256(json.dumps(
            code_cells,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()).hexdigest()
        return cls(
            tool_name=tool_name,
            contract_hash=contract_hash,
            capability_hash=capability_hash,
            version=code_hash[:16],
            code_hash=code_hash,
            code_cells=tuple(code_cells),
            metadata=metadata or {},
        )


class BaseCodeActImplementationStore(abc.ABC):
    """Store generated candidates and one approved version per contract."""

    @abc.abstractmethod
    async def save_candidate(
        self,
        implementation: CodeActImplementation,
    ) -> CodeActImplementation:
        """Persist an immutable candidate and return the stored value."""

    @abc.abstractmethod
    async def list_implementations(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> list[CodeActImplementation]:
        """List candidates for a function contract."""

    @abc.abstractmethod
    async def get_approved(
        self,
        tool_name: str,
        contract_hash: str,
    ) -> CodeActImplementation | None:
        """Return the approved implementation, if any."""

    @abc.abstractmethod
    async def approve(
        self,
        tool_name: str,
        contract_hash: str,
        version: str,
        *,
        approved_by: str | None = None,
    ) -> CodeActImplementation:
        """Promote a candidate. Re-approving an older version rolls back."""

    async def close(self) -> None:
        """Release resources owned by this store."""
