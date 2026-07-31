# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Explicit callable, Workspace, and MCP safety adapters."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import Any
from typing import Optional

from trpc_agent_sdk.code_executors import BaseProgramRunner
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.context import InvocationContext

from ._extractors import CallableRequestFactory
from ._extractors import MCPRequestExtractor
from ._extractors import workspace_request
from ._models import SafetyDecision
from ._models import SafetyScanRequest
from ._scanner import SafetyScanner
from ._tool_filter import blocked_envelope


def _extractor_failure(source_type: str, tool_name: Optional[str] = None) -> SafetyScanRequest:
    return SafetyScanRequest(
        script="",
        language="unsupported-extractor",
        source_type=source_type,
        tool_name=tool_name,
    )


class SafetyCallable:
    """Async wrapper for a sync or async callable with explicit extraction."""

    def __init__(self, func: Callable[..., Any], scanner: SafetyScanner, request_factory: CallableRequestFactory):
        self._func = func
        self._scanner = scanner
        self._request_factory = request_factory

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        try:
            request = self._request_factory(args, kwargs)
        except Exception:  # pylint: disable=broad-except
            request = _extractor_failure("callable")
        if request is not None:
            report = self._scanner.scan(request)
            if report.decision is not SafetyDecision.ALLOW:
                return blocked_envelope(report)
        result = self._func(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result


class SafetyProgramRunner(BaseProgramRunner):
    """Wrap only the real Workspace ``run_program`` boundary."""

    def __init__(self, inner: BaseProgramRunner, scanner: SafetyScanner):
        super().__init__()
        self._inner = inner
        self._scanner = scanner

    async def run_program(
        self,
        ws: WorkspaceInfo,
        spec: WorkspaceRunProgramSpec,
        ctx: Optional[InvocationContext] = None,
    ) -> WorkspaceRunResult:
        request = workspace_request(
            spec.cmd,
            spec.args,
            cwd=spec.cwd,
            env=spec.env,
            invocation_context=ctx,
        )
        report = self._scanner.scan(request)
        if report.decision is not SafetyDecision.ALLOW:
            return WorkspaceRunResult(
                stderr=json.dumps(blocked_envelope(report), ensure_ascii=False, allow_nan=False, sort_keys=True),
                exit_code=126,
            )
        return await self._inner.run_program(ws, spec, ctx)


class SafetyMCPAdapter:
    """Wrap a ClientSession-like object at its visible ``call_tool`` boundary."""

    def __init__(
        self,
        session: Any,
        scanner: SafetyScanner,
        extractors: Optional[dict[str, MCPRequestExtractor]] = None,
    ):
        self._session = session
        self._scanner = scanner
        self._extractors = dict(extractors or {})

    async def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> Any:
        actual_arguments = arguments if arguments is not None else {}
        extractor = self._extractors.get(name)
        if extractor is not None:
            try:
                request = extractor(name, actual_arguments)
            except Exception:  # pylint: disable=broad-except
                request = _extractor_failure("mcp", name)
            if request is not None:
                report = self._scanner.scan(request)
                if report.decision is not SafetyDecision.ALLOW:
                    return blocked_envelope(report)
        return await self._session.call_tool(name, arguments=arguments)
