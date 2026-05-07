# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared fixtures for the cube/ test suite.

Production code does ``import e2b_code_interpreter as e2b`` at module
top-level in :mod:`trpc_agent_sdk.code_executors.cube._sandbox` and
:mod:`trpc_agent_sdk.code_executors.cube._code_executor`. Tests still
need to swap that vendor surface out for a fake — both to avoid talking
to a real Cube server and to inject precise exception types — so the
``fake_e2b`` fixture monkeypatches the ``e2b`` symbol in *both* importer
modules' globals (NOT ``sys.modules['e2b_code_interpreter']``, which
would only affect callers that re-import the package after the patch).

The fake mirrors just the surface the production code actually
consults: a handful of vendor exceptions, the ``SandboxState`` /
``FileType`` enums, and a placeholder ``AsyncSandbox``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest


class _FakeSandboxException(Exception):
    """Mirrors e2b_code_interpreter.SandboxException."""


class _FakeSandboxNotFoundException(_FakeSandboxException):
    """Mirrors e2b_code_interpreter.SandboxNotFoundException."""


class _FakeCommandExitException(Exception):
    """Mirrors e2b_code_interpreter.CommandExitException.

    Carries stdout/stderr/exit_code, matching how the real vendor raises
    it. ``commands_run`` reads these via getattr so the attribute names
    are the contract here.
    """

    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 1):
        super().__init__(f"cmd exit {exit_code}")
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class _FakeTimeoutException(Exception):
    """Mirrors e2b_code_interpreter.TimeoutException.

    The real vendor message is long and prescriptive ("passing 'timeout'
    when making the request", "Use '0' to disable"). Production code
    catches this type in :meth:`CubeSandboxClient.commands_run` and
    rewrites it into a structured ``CubeCommandResult(timed_out=True)``,
    so what actually gets surfaced to callers never contains this
    message. The fake keeps the *type* precise while leaving the message
    empty so tests can assert on the translated shape.
    """


def _make_fake_e2b() -> SimpleNamespace:
    ns = SimpleNamespace()
    ns.SandboxException = _FakeSandboxException
    ns.SandboxNotFoundException = _FakeSandboxNotFoundException
    ns.CommandExitException = _FakeCommandExitException
    ns.TimeoutException = _FakeTimeoutException
    ns.SandboxState = SimpleNamespace(
        RUNNING=SimpleNamespace(value="running"),
        PAUSED=SimpleNamespace(value="paused"),
        STOPPED=SimpleNamespace(value="stopped"),
    )
    ns.FileType = SimpleNamespace(DIR="dir", FILE="file")
    ns.AsyncSandbox = MagicMock()
    return ns


@pytest.fixture
def fake_e2b(monkeypatch):
    """Swap ``e2b_code_interpreter`` for a fake in every cube import site.

    Production code does ``import e2b_code_interpreter as e2b`` at the
    top of ``_sandbox.py`` and ``_code_executor.py``, which binds an
    ``e2b`` name in those modules' globals. We patch each of those
    bindings independently (rather than ``sys.modules``) so already-
    executed ``from … import e2b`` statements see the fake.
    """
    ns = _make_fake_e2b()
    monkeypatch.setattr(
        "trpc_agent_sdk.code_executors.cube._sandbox.e2b",
        ns,
    )
    monkeypatch.setattr(
        "trpc_agent_sdk.code_executors.cube._code_executor.e2b",
        ns,
    )
    return ns


def _make_fake_async_sandbox(sandbox_id: str = "sbx-1"):
    """Build a MagicMock shaped like ``e2b_code_interpreter.AsyncSandbox``.

    All the methods the production client touches are ``AsyncMock``s so
    tests can configure ``return_value`` / ``side_effect`` as needed.
    """
    sbx = MagicMock()
    sbx.sandbox_id = sandbox_id
    sbx.kill = AsyncMock(return_value=None)
    sbx.set_timeout = AsyncMock(return_value=None)
    info = SimpleNamespace(state=SimpleNamespace(value="running"))
    sbx.get_info = AsyncMock(return_value=info)
    sbx.commands = MagicMock()
    sbx.commands.run = AsyncMock()
    sbx.files = MagicMock()
    sbx.files.read = AsyncMock(return_value=b"")
    sbx.files.write = AsyncMock(return_value=None)
    sbx.files.get_info = AsyncMock(return_value=SimpleNamespace(type="file"))
    return sbx


@pytest.fixture
def fake_async_sandbox(fake_e2b):
    """A fresh fake AsyncSandbox whose ``get_info`` defaults to RUNNING."""
    sbx = _make_fake_async_sandbox()
    sbx.get_info = AsyncMock(return_value=SimpleNamespace(state=fake_e2b.SandboxState.RUNNING))
    return sbx
