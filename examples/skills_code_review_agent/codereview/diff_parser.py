# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Importlib bridge to the skill's stdlib-only library.

The unified-diff parser, the rule engine and the secret-pattern table live in
``skills/code-review/scripts/lib`` so the SAME implementation runs inside the
sandbox and on the host (single source of truth, no drift). This module loads
that package once and re-exports the host-facing entry points.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from .config import SKILL_NAME
from .config import SKILLS_ROOT

_LIB_PACKAGE = "cr_skill_lib"
_LIB_DIR = os.path.join(SKILLS_ROOT, SKILL_NAME, "scripts", "lib")


def _load_skill_lib():
    if _LIB_PACKAGE in sys.modules:
        return sys.modules[_LIB_PACKAGE]
    spec = importlib.util.spec_from_file_location(
        _LIB_PACKAGE,
        os.path.join(_LIB_DIR, "__init__.py"),
        submodule_search_locations=[_LIB_DIR],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_LIB_PACKAGE] = module
    spec.loader.exec_module(module)
    return module


_load_skill_lib()

# pylint: disable=wrong-import-position,wrong-import-order
from cr_skill_lib.diffparse import build_diff_summary  # noqa: E402
from cr_skill_lib.diffparse import parse_unified_diff  # noqa: E402
from cr_skill_lib.engine import run_all_rules  # noqa: E402
from cr_skill_lib import secret_patterns  # noqa: E402

__all__ = [
    "build_diff_summary",
    "parse_unified_diff",
    "run_all_rules",
    "secret_patterns",
]
