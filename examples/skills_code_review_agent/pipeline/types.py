# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Shared data-transfer objects for the code-review pipeline.

The ``Finding`` schema is fixed by issue #92 (the 9 required fields); everything
downstream — dedup, persistence, report rendering — is anchored to it. Keep this
in sync with ``skills/code-review/docs/OUTPUT_SCHEMA.md`` (the JSON contract the
sandbox scripts emit).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low"]
FindingStatus = Literal["active", "duplicate", "warning", "needs_human_review"]
FindingSource = Literal["rule", "llm", "static"]


class Hunk(BaseModel):
    """One @@ hunk of a changed file, with new-file line numbers resolved."""

    old_start: int = 0
    old_len: int = 0
    new_start: int = 0
    new_len: int = 0
    candidate_lines: list[int] = Field(default_factory=list)  # added/changed new-file line numbers


class ChangedFile(BaseModel):
    path: str
    change_type: Literal["added", "modified", "deleted", "renamed"] = "modified"
    language: Optional[str] = None
    hunks: list[Hunk] = Field(default_factory=list)


class DiffSummary(BaseModel):
    files: list[ChangedFile] = Field(default_factory=list)
    files_changed: int = 0
    added: int = 0
    removed: int = 0
    languages: dict[str, int] = Field(default_factory=dict)


class Finding(BaseModel):
    """A single review finding. The 9 fields below are mandated by issue #92."""

    severity: Severity
    category: str
    file: str
    line: Optional[int] = None
    title: str
    evidence: str
    recommendation: str
    confidence: float  # 0.0 - 1.0
    source: FindingSource
    # pipeline bookkeeping (not part of the required 9 fields):
    status: FindingStatus = "active"
    dedup_key: Optional[str] = None
    rule_id: Optional[str] = None  # e.g. "bandit:B602", "semgrep:python.lang.security..."


class SandboxRunResult(BaseModel):
    script: str
    exit_code: int = 0
    duration_sec: float = 0.0
    timed_out: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    blocked: bool = False
    block_reason: Optional[str] = None
    block_category: Optional[str] = None  # "path" | "network" | "budget" | "script"


class ReviewReport(BaseModel):
    """The rendered report. All 7 sections exist from day 1 (issue criterion 8);
    sections not yet populated render empty so the schema never churns across slices."""

    task_id: str
    findings_summary: dict = Field(default_factory=dict)  # 1. findings summary
    severity_stats: dict[str, int] = Field(default_factory=dict)  # 2. severity statistics
    human_review: list[Finding] = Field(default_factory=list)  # 3. needs-human-review items
    filter_blocks: list[dict] = Field(default_factory=list)  # 4. Filter interception summary
    monitoring: dict = Field(default_factory=dict)  # 5. monitoring metrics
    sandbox_summary: list[SandboxRunResult] = Field(default_factory=list)  # 6. sandbox execution summary
    findings: list[Finding] = Field(default_factory=list)  # 7. actionable findings + fixes
