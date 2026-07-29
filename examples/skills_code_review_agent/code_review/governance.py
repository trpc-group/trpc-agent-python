#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Manifest-driven, fail-closed governance for sandbox Skill execution."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.filter import BaseFilter, FilterResult, FilterType, run_filters

from code_review.config import ReviewConfig
from code_review.redaction import contains_plaintext_secret
from code_review.skill_integrity import (
    canonical_source_sha256,
    parse_integrity_files,
    safe_script_relative_path,
)


_ALLOWED_ENVIRONMENT_NAMES = frozenset(
    {
        "LANG",
        "LC_ALL",
        "PYTHONUNBUFFERED",
        "PATH",
        "PYTHONPATH",
        "WORKSPACE_DIR",
        "SKILLS_DIR",
        "WORK_DIR",
        "OUTPUT_DIR",
        "RUN_DIR",
    }
)
_SHELL_METACHARACTERS = frozenset(";|&`$><\n\r")
_HIGH_RISK_SCRIPT_PATTERNS = (
    re.compile(r"\brm\s+-[^\n]*\brf\b", re.IGNORECASE),
    re.compile(r"\b(?:curl|wget)\b[^\n]*\|\s*(?:sh|bash)\b", re.IGNORECASE),
    re.compile(r"\b(?:curl|wget)\b[^\n]*(?:https?://|ftp://)", re.IGNORECASE),
)
_SAFE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


class FilterAction(str, Enum):
    """表示治理层的沙箱执行决定，不与 finding 分桶概念混用。"""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


@dataclass(frozen=True)
class ExecutionBudget:
    """记录本次请求前已消耗的评审运行、时间和输出预算。"""

    runs_started: int = 0
    sandbox_elapsed_seconds: float = 0.0
    output_bytes: int = 0
    review_elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class GovernanceRequest:
    """描述一次仅接受 script_id 与结构化参数的受控执行请求。"""

    script_id: str
    structured_args: Mapping[str, Any]
    skill_root: Path
    workspace_root: Path
    input_paths: tuple[Path, ...] = ()
    output_paths: tuple[Path, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    runtime_type: str = "container"
    effective_network_mode: str | None = None
    network_policy_verified: bool = False
    explicit_local: bool = False
    user_network_confirmation: bool = False
    capability_network_allowed: bool | None = None
    budget: ExecutionBudget = field(default_factory=ExecutionBudget)
    raw_command: str | None = None
    argument_names: tuple[str, ...] = ()
    _filter_decision: GovernanceDecision | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class GovernanceDecision:
    """保存只含受控原因代码、可安全持久化的 Filter 结果。"""

    action: FilterAction
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    script_id: str = "unknown"

    @property
    def event(self) -> dict[str, Any]:
        """生成可由 pipeline 直接落库、不会带入原始请求内容的审计事件。"""

        return {
            "stage": "pre_execution",
            "target": self.script_id,
            "action": self.action.value,
            "rule": "sandbox_governance",
            "reasons": list(self.reasons),
        }

    def to_mapping(self) -> dict[str, Any]:
        """转换为 ReviewPipeline 治理端口所需的字典契约。"""

        return {
            "action": self.action.value,
            "events": [self.event],
            "warnings": list(self.warnings),
        }


class SandboxGovernanceFilter(BaseFilter):
    """通过 SDK Filter 链执行 manifest、路径、预算和运行时前置校验。"""

    def __init__(self, manifest_path: Path, *, config: ReviewConfig | None = None) -> None:
        """绑定真实 manifest 与不可变 ReviewConfig，避免请求方覆盖默认安全预算。"""

        super().__init__()
        self.type = FilterType.TOOL
        self.name = "sandbox_governance"
        self._manifest_path = manifest_path.resolve()
        self._scripts_root = self._manifest_path.parent.resolve()
        self._skill_root = self._scripts_root.parent.resolve()
        self._config = ReviewConfig() if config is None else config

    def decide(self, request: GovernanceRequest) -> GovernanceDecision:
        """调用真实 SDK ``run_filters`` 链；事件循环或 Filter 异常均按拒绝处理。"""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._run_chain(request))
        return self._decision(FilterAction.DENY, "filter_event_loop_unsupported")

    def run_if_allowed(
        self,
        request: GovernanceRequest,
        executor: Callable[[], Any],
    ) -> GovernanceDecision:
        """仅在 ALLOW 后调用执行回调；拒绝或人工复核绝不产生回退副作用。"""

        decision = self.decide(request)
        if decision.action is FilterAction.ALLOW:
            executor()
        return decision

    async def _run_chain(self, request: GovernanceRequest) -> GovernanceDecision:
        """在同步应用边界内桥接 SDK 的异步 Filter 运行器。"""

        async def _allowed_handle() -> GovernanceDecision:
            """返回 ``_before`` 保存的决定，不在 Filter 链内启动沙箱。"""

            return request._filter_decision or self._decision(FilterAction.DENY, "filter_decision_missing")

        try:
            decision = await run_filters(
                new_agent_context(timeout=self._config.review_deadline_seconds * 1000),
                request,
                [self],
                _allowed_handle,
            )
        except Exception:
            return self._decision(FilterAction.DENY, "filter_chain_failed")
        if isinstance(decision, GovernanceDecision):
            return decision
        return self._decision(FilterAction.DENY, "filter_chain_invalid_result")

    async def _before(self, _ctx: Any, request: Any, result: FilterResult) -> None:
        """在 SDK 分派到执行句柄前完成全部前置治理检查并实现短路。"""

        if not isinstance(request, GovernanceRequest):
            decision = self._decision(FilterAction.DENY, "governance_request_invalid")
        else:
            decision = self._evaluate(request)
        object.__setattr__(request, "_filter_decision", decision)
        result.rsp = decision
        result.is_continue = decision.action is FilterAction.ALLOW

    def _evaluate(self, request: GovernanceRequest) -> GovernanceDecision:
        """按 2.7 节顺序依次校验授权、参数、路径、环境、网络、预算与 runtime。"""

        script_id = self._safe_script_id(request.script_id)
        if request.raw_command is not None:
            return self._decision(FilterAction.DENY, "arbitrary_command_forbidden", script_id)
        if script_id is None:
            return self._decision(FilterAction.DENY, "script_id_invalid")
        manifest = self._load_manifest()
        if manifest is None:
            return self._decision(FilterAction.DENY, "manifest_invalid", script_id)
        definition = manifest.get(script_id)
        if definition is None:
            return self._decision(FilterAction.DENY, "script_unregistered", script_id)
        if not self._entrypoint_is_verified(definition, request.skill_root):
            return self._decision(FilterAction.DENY, "script_integrity_mismatch", script_id)
        argument_reason = self._argument_reason(request, definition)
        if argument_reason is not None:
            return self._decision(FilterAction.DENY, argument_reason, script_id)
        path_reason = self._path_reason(request)
        if path_reason is not None:
            return self._decision(FilterAction.DENY, path_reason, script_id)
        environment_reason = self._environment_reason(request.environment)
        if environment_reason is not None:
            return self._decision(FilterAction.DENY, environment_reason, script_id)
        if definition["requires_network"]:
            return self._decision(FilterAction.DENY, "networked_script_forbidden", script_id)
        budget_action, budget_reason = self._budget_reason(request.budget, definition)
        if budget_reason is not None:
            return self._decision(budget_action, budget_reason, script_id)
        runtime_decision, runtime_warnings = self._runtime_decision(request, script_id)
        if runtime_decision is not None:
            return runtime_decision
        return self._decision(
            FilterAction.ALLOW,
            "manifest_verified",
            script_id,
            warnings=runtime_warnings,
        )

    def _load_manifest(self) -> dict[str, dict[str, Any]] | None:
        """只加载结构完整的 manifest 条目，损坏授权文件一律失败关闭。"""

        try:
            payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("scripts"), list):
            return None
        definitions: dict[str, dict[str, Any]] = {}
        for item in payload["scripts"]:
            if not isinstance(item, Mapping):
                return None
            script_id = item.get("script_id")
            if not isinstance(script_id, str) or self._safe_script_id(script_id) is None:
                return None
            if script_id in definitions:
                return None
            entrypoint = item.get("entrypoint")
            normalized_entrypoint = safe_script_relative_path(entrypoint)
            sha256 = item.get("sha256")
            integrity_files = parse_integrity_files(item.get("files"))
            arguments = item.get("arguments")
            timeout = item.get("timeout_seconds")
            output_limit = item.get("max_output_bytes")
            requires_network = item.get("requires_network")
            if (
                normalized_entrypoint is None
                or not isinstance(sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", sha256)
                or integrity_files is None
                or not isinstance(arguments, Mapping)
                or isinstance(timeout, bool)
                or not isinstance(timeout, int)
                or timeout <= 0
                or isinstance(output_limit, bool)
                or not isinstance(output_limit, int)
                or output_limit <= 0
                or not isinstance(requires_network, bool)
            ):
                return None
            integrity_by_path = {
                integrity_file.path: integrity_file.sha256
                for integrity_file in integrity_files
            }
            if integrity_by_path.get(normalized_entrypoint) != sha256:
                return None
            definitions[script_id] = {
                "entrypoint": normalized_entrypoint,
                "sha256": sha256,
                "files": integrity_files,
                "arguments": arguments,
                "timeout_seconds": timeout,
                "max_output_bytes": output_limit,
                "requires_network": requires_network,
            }
        return definitions

    def _entrypoint_is_verified(self, definition: Mapping[str, Any], request_root: Path) -> bool:
        """复验 staged Skill 根目录、入口 containment、摘要和高危内容策略。"""

        try:
            if request_root.resolve() != self._skill_root:
                return False
            integrity_files = definition["files"]
            if not isinstance(integrity_files, tuple):
                return False
            verified_sources: list[str] = []
            for integrity_file in integrity_files:
                source_path = (
                    self._scripts_root / integrity_file.path
                ).resolve(strict=True)
                source_path.relative_to(self._scripts_root)
                content = source_path.read_bytes()
                if canonical_source_sha256(content) != integrity_file.sha256:
                    return False
                verified_sources.append(content.decode("utf-8-sig"))
        except (OSError, ValueError):
            return False
        except UnicodeDecodeError:
            return False
        return not any(
            pattern.search(source)
            for source in verified_sources
            for pattern in _HIGH_RISK_SCRIPT_PATTERNS
        )

    def _argument_reason(
        self,
        request: GovernanceRequest,
        definition: Mapping[str, Any],
    ) -> str | None:
        """校验结构化参数，禁止任何 shell 控制字符或未授权参数进入 argv 构造。"""

        if not isinstance(request.structured_args, Mapping):
            return "arguments_invalid"
        if len(request.argument_names) != len(set(request.argument_names)):
            return "arguments_duplicate"
        if any(not isinstance(name, str) for name in request.argument_names):
            return "arguments_invalid"
        if self._contains_shell_metacharacter(request.structured_args):
            return "shell_metacharacter_forbidden"
        template = definition["arguments"]
        if template.get("type") != "object" or not isinstance(template.get("properties"), Mapping):
            return "manifest_arguments_invalid"
        properties = template["properties"]
        if template.get("additional_properties") is False:
            unknown = set(request.structured_args) - set(properties)
            if unknown:
                return "arguments_unknown"
        for name, value in request.structured_args.items():
            if not isinstance(name, str) or name not in properties:
                return "arguments_unknown"
            schema = properties[name]
            if not isinstance(schema, Mapping) or not self._value_matches_schema(value, schema):
                return "arguments_invalid"
        return None

    def _path_reason(self, request: GovernanceRequest) -> str | None:
        """要求全部输入输出路径是任务 workspace 内的相对后代路径。"""

        if not request.input_paths or not request.output_paths:
            return "workspace_paths_missing"
        for raw_path in (*request.input_paths, *request.output_paths):
            if not self._is_workspace_relative(raw_path, request.workspace_root):
                return "workspace_path_escape"
        return None

    def _environment_reason(self, environment: Mapping[str, str]) -> str | None:
        """仅允许 runtime 所需且由应用重构的非敏感环境变量白名单。"""

        for name, value in environment.items():
            if not isinstance(name, str) or name not in _ALLOWED_ENVIRONMENT_NAMES:
                return "environment_not_allowlisted"
            if not isinstance(value, str):
                return "environment_invalid"
            if contains_plaintext_secret(value):
                return "environment_secret_forbidden"
        return None

    def _runtime_decision(
        self,
        request: GovernanceRequest,
        script_id: str,
    ) -> tuple[GovernanceDecision | None, tuple[str, ...]]:
        """按 runtime 应用可机器验证的无网络策略，不把 capability 当实例状态。"""

        if request.runtime_type == "container":
            if request.effective_network_mode != "none" or not request.network_policy_verified:
                return self._decision(FilterAction.DENY, "network_proof_missing", script_id), ()
            return None, ()
        if request.runtime_type == "cube":
            if request.effective_network_mode != "none" or not request.network_policy_verified:
                return self._decision(FilterAction.DENY, "network_proof_missing", script_id), ()
            return None, ()
        if request.runtime_type == "local":
            if not request.explicit_local:
                return self._decision(FilterAction.DENY, "local_runtime_not_explicit", script_id), ()
            return None, ("local_isolation_unverifiable",)
        return self._decision(FilterAction.DENY, "runtime_unsupported", script_id), ()

    def _budget_reason(
        self,
        budget: ExecutionBudget,
        definition: Mapping[str, Any],
    ) -> tuple[FilterAction, str | None]:
        """拒绝会超过运行次数、时间、输出或总截止时间的预检请求。"""

        numeric_values = (
            budget.runs_started,
            budget.sandbox_elapsed_seconds,
            budget.output_bytes,
            budget.review_elapsed_seconds,
        )
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 for value in numeric_values):
            return FilterAction.DENY, "budget_invalid"
        timeout = definition["timeout_seconds"]
        output_limit = definition["max_output_bytes"]
        if timeout > self._config.per_run_timeout_seconds or output_limit > self._config.max_output_bytes_per_run:
            return FilterAction.DENY, "manifest_budget_exceeds_config"
        if budget.runs_started + 1 > self._config.max_sandbox_runs:
            return FilterAction.NEEDS_HUMAN_REVIEW, "sandbox_run_budget_exceeded"
        if budget.sandbox_elapsed_seconds + timeout > self._config.sandbox_time_budget_seconds:
            return FilterAction.NEEDS_HUMAN_REVIEW, "sandbox_time_budget_exceeded"
        if budget.output_bytes + output_limit > self._config.max_output_bytes_per_review:
            return FilterAction.NEEDS_HUMAN_REVIEW, "sandbox_output_budget_exceeded"
        if budget.review_elapsed_seconds + timeout > self._config.review_deadline_seconds:
            return FilterAction.NEEDS_HUMAN_REVIEW, "review_deadline_exceeded"
        return FilterAction.ALLOW, None

    def _decision(
        self,
        action: FilterAction,
        reason: str,
        script_id: str = "unknown",
        *,
        warnings: Sequence[str] = (),
    ) -> GovernanceDecision:
        """构造不会暴露请求内容、路径或密钥的受限决定对象。"""

        safe_reason = reason if _SAFE_REASON.fullmatch(reason) else "governance_policy_failed"
        safe_script_id = self._safe_script_id(script_id) or "unknown"
        safe_warnings = tuple(
            warning for warning in warnings if isinstance(warning, str) and _SAFE_REASON.fullmatch(warning)
        )
        return GovernanceDecision(action, (safe_reason,), safe_warnings, safe_script_id)

    @staticmethod
    def _safe_script_id(value: object) -> str | None:
        """仅允许 manifest 风格标识进入面向持久化的审计字段。"""

        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value):
            return None
        return value

    @staticmethod
    def _contains_shell_metacharacter(value: Any) -> bool:
        """递归检测 shell 控制字符，阻止结构化值进入 argv 构造器前被解释。"""

        if isinstance(value, str):
            return any(character in _SHELL_METACHARACTERS for character in value)
        if isinstance(value, Mapping):
            return any(
                SandboxGovernanceFilter._contains_shell_metacharacter(key)
                or SandboxGovernanceFilter._contains_shell_metacharacter(item)
                for key, item in value.items()
            )
        if isinstance(value, (tuple, list)):
            return any(SandboxGovernanceFilter._contains_shell_metacharacter(item) for item in value)
        return False

    @staticmethod
    def _value_matches_schema(value: Any, schema: Mapping[str, Any]) -> bool:
        """实现 manifest 规定的基础类型、枚举值和字符串长度契约。"""

        expected_type = schema.get("type")
        matches_type = (
            expected_type == "string"
            and isinstance(value, str)
            or expected_type == "integer"
            and isinstance(value, int)
            and not isinstance(value, bool)
            or expected_type == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            or expected_type == "boolean"
            and isinstance(value, bool)
            or expected_type == "array"
            and isinstance(value, list)
            or expected_type == "object"
            and isinstance(value, Mapping)
        )
        if not matches_type:
            return False
        max_length = schema.get("maxLength")
        if isinstance(value, str) and isinstance(max_length, int) and len(value) > max_length:
            return False
        enum = schema.get("enum")
        return not isinstance(enum, list) or value in enum

    @staticmethod
    def _is_workspace_relative(raw_path: Path, workspace_root: Path) -> bool:
        """在 staging 或打开文件前拒绝绝对路径、遍历路径和符号链接逃逸。"""

        if raw_path.is_absolute() or ".." in raw_path.parts:
            return False
        try:
            (workspace_root.resolve() / raw_path).resolve().relative_to(workspace_root.resolve())
        except (OSError, ValueError):
            return False
        return True
