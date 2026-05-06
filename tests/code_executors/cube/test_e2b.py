# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.code_executors.cube._e2b."""

from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace

import pytest


def test_guest_user_is_root():
    from trpc_agent_sdk.code_executors.cube import _e2b
    # Downstream hermes adapters rely on `root`; changing it silently
    # would break file-upload permissions across the fleet.
    assert _e2b._GUEST_USER == "root"


def test_install_hint_mentions_cube_extra():
    from trpc_agent_sdk.code_executors.cube import _e2b
    assert "trpc-agent-py[cube]" in _e2b._E2B_INSTALL_HINT


def test_import_e2b_returns_module_when_present(monkeypatch):
    """When ``e2b_code_interpreter`` is importable, return it verbatim."""
    fake_mod = SimpleNamespace(AsyncSandbox=object())
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", fake_mod)
    from trpc_agent_sdk.code_executors.cube._e2b import _import_e2b
    assert _import_e2b() is fake_mod


def test_import_e2b_raises_import_error_when_missing(monkeypatch):
    """When the extra is not installed, raise ImportError with install hint."""
    # Scrub any cached import first.
    monkeypatch.delitem(sys.modules, "e2b_code_interpreter", raising=False)

    # Force the import to fail at the builtin layer.
    original_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "e2b_code_interpreter":
            raise ImportError("not installed in this venv")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)

    from trpc_agent_sdk.code_executors.cube._e2b import _import_e2b
    with pytest.raises(ImportError, match=r"trpc-agent-py\[cube\]"):
        _import_e2b()
