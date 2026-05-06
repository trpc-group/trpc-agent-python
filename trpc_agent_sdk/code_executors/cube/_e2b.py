# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""e2b-code-interpreter vendor seam for the Cube package.

Centralizes the lazy-import boundary (:func:`_import_e2b`) and the small
set of vendor-aware constants used by sibling modules (`_sandbox.py` for
lifecycle/commands, `_transfer.py` for the tar protocol). Keeping this
file thin so neither downstream module has to repeat the install hint or
the ``user=`` plumbing.
"""

from __future__ import annotations

# The unix user we run sandbox commands and FS ops as. Standard cube/e2b
# templates ship with `root`; downstream callers do not need to override
# this and we deliberately do not expose a knob to keep the surface small.
_GUEST_USER = "root"

_E2B_INSTALL_HINT = ("e2b-code-interpreter is required for CubeCodeExecutor; "
                     "install with `pip install trpc-agent-py[cube]`.")


def _import_e2b():
    """Lazily import :mod:`e2b_code_interpreter` symbols.

    Deferred so that ``from trpc_agent_sdk.code_executors.cube import ...``
    never requires the optional ``[cube]`` extra to be installed; only
    actual sandbox construction or vendor-exception handling pays the
    import cost.

    Raises:
        ImportError: if the optional ``[cube]`` extra is not installed,
            with a message pointing at the install command.
    """
    try:
        import e2b_code_interpreter as _mod  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        raise ImportError(_E2B_INSTALL_HINT) from exc
    return _mod
