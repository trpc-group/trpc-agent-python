# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Fake Workspace runner and ordinary callable/Skill-boundary adapter tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from trpc_agent_sdk.code_executors import BaseProgramRunner
from trpc_agent_sdk.code_executors import WorkspaceInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.safety import SafetyCallable
from trpc_agent_sdk.safety import SafetyProgramRunner
from trpc_agent_sdk.safety import SafetyScanRequest


class FakeRunner(BaseProgramRunner):

    def __init__(self):
        super().__init__()
        self.calls = 0
        self.last_spec = None
        self.result = WorkspaceRunResult(stdout="ok")

    async def run_program(self, ws, spec, ctx=None):
        del ws, ctx
        self.calls += 1
        self.last_spec = spec
        return self.result


async def test_workspace_allow_forwards_same_spec_and_result(scanner):
    inner = FakeRunner()
    wrapper = SafetyProgramRunner(inner, scanner)
    spec = WorkspaceRunProgramSpec(cmd="echo", args=["hello"], cwd="workspace")
    result = await wrapper.run_program(WorkspaceInfo(id="w", path="workspace"), spec)
    assert inner.calls == 1
    assert inner.last_spec is spec
    assert result is inner.result


async def test_workspace_deny_and_review_call_runner_zero(scanner):
    inner = FakeRunner()
    wrapper = SafetyProgramRunner(inner, scanner)
    denied = await wrapper.run_program(
        WorkspaceInfo(id="w", path="workspace"),
        WorkspaceRunProgramSpec(cmd="rm", args=["-rf", "/"]),
    )
    reviewed = await wrapper.run_program(
        WorkspaceInfo(id="w", path="workspace"),
        WorkspaceRunProgramSpec(cmd="$COMMAND", args=["arg"]),
    )
    assert inner.calls == 0
    assert denied.exit_code == 126
    assert '"decision": "deny"' in denied.stderr
    assert '"decision": "needs_human_review"' in reviewed.stderr


async def test_sync_and_async_callable_preserve_allow_results(scanner):
    calls = []

    def factory(args, kwargs):
        del kwargs
        return SafetyScanRequest(script=args[0], language="python", source_type="callable")

    def sync_func(script):
        calls.append(("sync", script))
        return 7

    async def async_func(script):
        calls.append(("async", script))
        return {"ok": True}

    assert await SafetyCallable(sync_func, scanner, factory)("print('ok')") == 7
    result = await SafetyCallable(async_func, scanner, factory)("print('ok')")
    assert result == {"ok": True}
    assert len(calls) == 2


async def test_callable_deny_and_review_do_not_execute(scanner):
    func = MagicMock(return_value="must not run")

    def factory(args, kwargs):
        del kwargs
        return SafetyScanRequest(script=args[0], language="python", source_type="skill_callable")

    wrapper = SafetyCallable(func, scanner, factory)
    denied = await wrapper("import os\nos.remove('/etc/hosts')")
    reviewed = await wrapper("import requests\nrequests.get(target)")
    assert func.call_count == 0
    assert denied["safety"]["decision"] == "deny"
    assert reviewed["safety"]["decision"] == "needs_human_review"
