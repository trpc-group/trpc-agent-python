# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for the PEP 562 lazy-import wiring in the cube package.

The commit promises that
``import trpc_agent_sdk.code_executors`` does NOT require the optional
``[cube]`` extra. Accessing a Cube symbol triggers lazy loading but
still defers the ``e2b-code-interpreter`` import until
``CubeCodeExecutor.create`` / ``.attach`` actually wants to talk to a
sandbox.

Tests that need a **cold** ``sys.modules`` state are run in a subprocess
so they never corrupt the in-process module cache (which is shared
across the whole test session and is what makes other tests'
``monkeypatch`` calls resolve).
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


def test_all_lists_all_lazy_symbols():
    """``__all__`` must include every element of ``_CUBE_LAZY_ATTRS``."""
    from trpc_agent_sdk import code_executors as ce
    for name in ce._CUBE_LAZY_ATTRS:
        assert name in ce.__all__, f"{name!r} missing from __all__"


def test_dir_contains_lazy_symbols():
    from trpc_agent_sdk import code_executors as ce
    d = dir(ce)
    for name in ce._CUBE_LAZY_ATTRS:
        assert name in d


def test_unknown_attribute_raises():
    from trpc_agent_sdk import code_executors as ce
    with pytest.raises(AttributeError, match="definitely_not_a_thing"):
        _ = ce.definitely_not_a_thing


def test_lazy_attribute_access_populates_globals():
    """After first access the lazy symbol is cached in the module's globals()."""
    # Isolated subprocess so we get a cold module cache.
    result = _run_isolated("""
        import sys
        import trpc_agent_sdk.code_executors as ce
        # First access triggers __getattr__.
        cls1 = ce.CubeCodeExecutorConfig
        # After first access, ce.__dict__ holds the symbol.
        assert "CubeCodeExecutorConfig" in ce.__dict__
        cls2 = ce.CubeCodeExecutorConfig
        assert cls1 is cls2
        print("OK")
    """)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_import_does_not_touch_e2b():
    """Plain import of ``code_executors`` does NOT import e2b_code_interpreter.

    This is the core promise of the lazy wiring. Run in a subprocess so
    the main test session's module cache cannot mask the behaviour.
    """
    result = _run_isolated("""
        import sys
        import trpc_agent_sdk.code_executors  # noqa: F401
        assert "e2b_code_interpreter" not in sys.modules, \
            "bare import pulled in e2b_code_interpreter"
        # Sub-package cube/ may or may not be imported yet — the contract
        # is only that e2b is not.
        print("OK")
    """)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cube_subpackage_import_does_not_touch_e2b():
    """Even importing the ``cube`` subpackage is e2b-free.

    ``_import_e2b`` is wrapped in a function; it only runs when we open
    a real sandbox. Just importing the package must not trigger it.
    """
    result = _run_isolated("""
        import sys
        import trpc_agent_sdk.code_executors.cube as cube  # noqa: F401
        assert "e2b_code_interpreter" not in sys.modules, \
            "importing cube pulled in e2b_code_interpreter"
        print("OK")
    """)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_config_types_usable_without_e2b():
    """`CubeCodeExecutorConfig` can be constructed without the [cube] extra."""
    result = _run_isolated("""
        import sys
        import trpc_agent_sdk.code_executors as ce
        cfg = ce.CubeCodeExecutorConfig(template="t", api_url="u", api_key="k")
        assert cfg.template == "t"
        assert "e2b_code_interpreter" not in sys.modules
        print("OK")
    """)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cube_subpackage_reexports_public_api():
    """Every entry on the cube subpackage's ``__all__`` must resolve."""
    from trpc_agent_sdk.code_executors import cube
    for name in cube.__all__:
        assert hasattr(cube, name), f"{name} missing from cube/__init__.py"


def test_subpackage_all_matches_parent_lazy_set():
    """Parent-package lazy set must match the subpackage ``__all__``.

    BUG PROBE: if a symbol is added to ``cube/__init__.py`` but forgotten
    in ``code_executors/__init__.py`` lazy wiring (or vice versa), this
    test catches the drift.
    """
    from trpc_agent_sdk.code_executors import cube as sub
    from trpc_agent_sdk import code_executors as parent
    assert set(sub.__all__) == set(parent._CUBE_LAZY_ATTRS), (
        f"drift between cube/__init__.py __all__ and parent _CUBE_LAZY_ATTRS: "
        f"only-in-subpackage={set(sub.__all__) - set(parent._CUBE_LAZY_ATTRS)!r}, "
        f"only-in-parent={set(parent._CUBE_LAZY_ATTRS) - set(sub.__all__)!r}"
    )


def test_onexisting_resolves_via_parent_lazy_import():
    """Regression for the drift bug: ``OnExisting`` was exported from
    the subpackage but not wired into the parent's ``_CUBE_LAZY_ATTRS``.

    ``from trpc_agent_sdk.code_executors import OnExisting`` used to
    raise ``AttributeError``. Pin both access paths so a reintroduction
    of the drift fails loudly.
    """
    from trpc_agent_sdk.code_executors import OnExisting as parent_symbol
    from trpc_agent_sdk.code_executors.cube import OnExisting as sub_symbol
    assert parent_symbol is sub_symbol
