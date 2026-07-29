#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Configuration contract for the automatic code-review Agent."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, fields
from typing import ClassVar, Mapping


@dataclass(frozen=True)
class ReviewConfig:
    """Immutable limits and version identifiers for one review.

    Environment overrides use the ``CODE_REVIEW_`` prefix followed by the
    upper-case field name, for example ``CODE_REVIEW_MAX_INPUT_FILES``.
    """

    ENV_PREFIX: ClassVar[str] = "CODE_REVIEW_"

    schema_version: str = "1.0.0"
    rule_pack_version: str = "1.0.0"

    max_input_file_bytes: int = 1024 * 1024
    max_input_files: int = 500
    max_input_bytes: int = 10 * 1024 * 1024
    max_diff_lines: int = 50_000

    max_sandbox_runs: int = 10
    per_run_timeout_seconds: int = 30
    sandbox_time_budget_seconds: int = 90
    review_deadline_seconds: int = 110
    max_output_bytes_per_run: int = 1024 * 1024
    max_output_bytes_per_review: int = 2 * 1024 * 1024
    network_policy: str = "deny"

    def __post_init__(self) -> None:
        """校验评审输入、沙箱和输出预算均满足锁定安全约束。"""

        positive_integer_fields = (
            "max_input_file_bytes",
            "max_input_files",
            "max_input_bytes",
            "max_diff_lines",
            "max_sandbox_runs",
            "per_run_timeout_seconds",
            "sandbox_time_budget_seconds",
            "review_deadline_seconds",
            "max_output_bytes_per_run",
            "max_output_bytes_per_review",
        )
        for name in positive_integer_fields:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than zero")

        if self.max_input_bytes < self.max_input_file_bytes:
            raise ValueError("max_input_bytes must be at least max_input_file_bytes")
        if self.review_deadline_seconds < self.sandbox_time_budget_seconds:
            raise ValueError("review_deadline_seconds must be at least sandbox_time_budget_seconds")
        if self.max_output_bytes_per_review < self.max_output_bytes_per_run:
            raise ValueError("max_output_bytes_per_review must be at least max_output_bytes_per_run")
        if self.network_policy != "deny":
            raise ValueError("network_policy must be 'deny' for the current rule pack")
        if not self.schema_version.strip():
            raise ValueError("schema_version must not be empty")
        if not self.rule_pack_version.strip():
            raise ValueError("rule_pack_version must not be empty")

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        prefix: str = ENV_PREFIX,
    ) -> "ReviewConfig":
        """从受控环境变量映射构造配置，并拒绝格式错误的限制值。

        Unknown variables are ignored. Integer fields are parsed strictly so
        malformed limits fail before staging or sandbox execution.
        """

        source = os.environ if environ is None else environ
        overrides: dict[str, object] = {}

        for field in fields(cls):
            environment_name = f"{prefix}{field.name.upper()}"
            if environment_name not in source:
                continue

            raw_value = source[environment_name]
            if isinstance(field.default, int):
                try:
                    overrides[field.name] = int(raw_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{environment_name} must be an integer") from exc
            else:
                overrides[field.name] = str(raw_value)

        return cls(**overrides)

    @property
    def config_digest(self) -> str:
        """返回规范化配置载荷的 SHA-256 摘要。"""

        canonical = json.dumps(
            asdict(self),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def to_dict(self) -> dict[str, object]:
        """返回可安全持久化的配置快照及其摘要。"""

        snapshot: dict[str, object] = asdict(self)
        snapshot["config_digest"] = self.config_digest
        return snapshot
