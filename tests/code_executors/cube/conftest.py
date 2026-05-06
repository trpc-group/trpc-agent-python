# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared fixtures for the cube/ test suite.

Exposes a ``fake_e2b`` fixture that patches
:func:`trpc_agent_sdk.code_executors.cube._e2b._import_e2b` to return a
fake vendor module with stub classes / enums that match the surface the
production code consults. This keeps the whole test suite independent
of the real ``e2b-code-interpreter`` dependency.
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


def _make_fake_e2b() -> SimpleNamespace:
    ns = SimpleNamespace()
    ns.SandboxException = _FakeSandboxException
    ns.SandboxNotFoundException = _FakeSandboxNotFoundException
    ns.CommandExitException = _FakeCommandExitException
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
    """Patch ``_import_e2b`` everywhere the cube package imports it."""
    ns = _make_fake_e2b()
    # The production code does ``from ._e2b import _import_e2b`` in
    # _sandbox.py and _code_executor.py, which rebinds the symbol in
    # those modules' globals — so we must patch every import site, not
    # just the original definition.
    monkeypatch.setattr(
        "trpc_agent_sdk.code_executors.cube._e2b._import_e2b",
        lambda: ns,
    )
    monkeypatch.setattr(
        "trpc_agent_sdk.code_executors.cube._sandbox._import_e2b",
        lambda: ns,
    )
    monkeypatch.setattr(
        "trpc_agent_sdk.code_executors.cube._code_executor._import_e2b",
        lambda: ns,
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
    # get_info returns a state holder by default; tests override.
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
