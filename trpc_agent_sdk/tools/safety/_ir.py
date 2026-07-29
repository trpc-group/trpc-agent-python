# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Language-neutral operations emitted by safety analyzers."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from ._models import AnalysisStatus
from ._models import SafetyFinding
from ._models import ScriptLanguage


class OperationKind(str, Enum):
    """External effects relevant to the tool safety policy."""

    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    PROCESS_EXECUTE = "process_execute"
    NETWORK_CONNECT = "network_connect"
    DEPENDENCY_CHANGE = "dependency_change"
    RESOURCE_REQUEST = "resource_request"
    SECRET_FLOW = "secret_flow"
    DYNAMIC_CODE_EXECUTION = "dynamic_code_execution"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


class SafetyOperation(BaseModel):
    """One normalized operation without raw source or secret values."""

    model_config = ConfigDict(extra="forbid")

    kind: OperationKind
    block_id: str = "block-0"
    language: ScriptLanguage
    line_number: int | None = None
    column: int | None = None
    capability: str | None = None
    target: str | None = None
    target_known: bool = True
    recursive: bool = False
    background: bool = False
    shell: bool = False
    amount: float | int | None = None
    bounded: bool | None = None
    evidence: str


class AnalysisResult(BaseModel):
    """Complete bounded result from one language analyzer."""

    model_config = ConfigDict(extra="forbid")

    status: AnalysisStatus = AnalysisStatus.COMPLETE
    operations: list[SafetyOperation] = Field(default_factory=list)
    findings: list[SafetyFinding] = Field(default_factory=list)
    unsupported_nodes: list[str] = Field(default_factory=list)
    unknown_side_effects: list[str] = Field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.status == AnalysisStatus.COMPLETE
