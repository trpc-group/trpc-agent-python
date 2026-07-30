# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for the cube subpackage import surface.

The Cube/E2B backend is shipped as the optional ``[cube]`` extra and
requires ``e2b-code-interpreter`` at import time. Two contracts are
pinned here:

1. The parent ``trpc_agent_sdk.code_executors`` package intentionally
   does NOT re-export Cube symbols. Re-exporting optional-dependency
   symbols would silently force every importer of the parent package
   to install ``[cube]``; instead, business code that genuinely needs
   the Cube backend imports the subpackage directly:

       from trpc_agent_sdk.code_executors.cube import CubeCodeExecutor

2. As a corollary, importing the parent package alone does NOT pull in
   ``e2b_code_interpreter`` — the parent's ``__init__.py`` deliberately
   does not reference ``trpc_agent_sdk.code_executors.cube``. (Importing
   the cube subpackage itself, by contrast, does eagerly import the
   vendor SDK; that is the explicit cost of opting into the [cube]
   backend.)

Tests that need a **cold** ``sys.modules`` state are run in a subprocess
so they never corrupt the in-process module cache (which is shared
across the whole test session).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


def _run_isolated(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_parent_package_does_not_reexport_cube_symbols():
    """Cube symbols must NOT be reachable from the parent package.

    Re-exporting optional-dependency symbols from the eager package
    would silently make ``[cube]`` mandatory for everyone who imports
    ``code_executors``. Force callers to make the dependency explicit
    by importing from the subpackage.
    """
    from trpc_agent_sdk import code_executors as ce
    cube_symbols = (
        "CubeCodeExecutor",
        "CubeCodeExecutorConfig",
        "CubeCommandResult",
        "CubeProgramRunner",
        "CubeSandboxClient",
        "CubeWorkspaceFS",
        "CubeWorkspaceManager",
        "CubeWorkspaceRuntime",
        "CubeWorkspaceRuntimeConfig",
        "OnExisting",
        "create_cube_workspace_runtime",
    )
    for name in cube_symbols:
        assert name not in ce.__all__, f"{name!r} leaked into parent __all__"
        with pytest.raises(AttributeError):
            getattr(ce, name)


def test_parent_package_import_does_not_touch_e2b():
    """Plain ``import code_executors`` does NOT import e2b_code_interpreter.

    The parent package never references the cube subpackage, so
    importing it must stay cheap and dependency-free even when the
    [cube] extra is installed in the same environment.

    Run in a subprocess so the main test session's module cache cannot
    mask the behaviour.
    """
    result = _run_isolated("""
        import sys
        import trpc_agent_sdk.code_executors  # noqa: F401
        assert "e2b_code_interpreter" not in sys.modules, \
            "bare import of code_executors pulled in e2b_code_interpreter"
        print("OK")
    """)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cube_subpackage_reexports_public_api():
    """Every entry on the cube subpackage's ``__all__`` must resolve."""
    from trpc_agent_sdk.code_executors import cube
    for name in cube.__all__:
        assert hasattr(cube, name), f"{name} missing from cube/__init__.py"
