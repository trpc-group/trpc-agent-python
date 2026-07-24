# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Code Review Agent — CLI entry point.

Thin launcher that mirrors the layout of other tRPC-Agent examples:
``run_agent.py`` (this file) sits at the example root and delegates to the
real implementation in ``agent/agent.py``. Configuration (including the
optional LLM integration) is read from the project-root ``.env``.

Examples
--------
    # Dry-run on a built-in fixture (no model API, full pipeline):
    python run_agent.py --fixture security --dry-run

    # Real sandbox run with an LLM second-opinion triage enabled:
    python run_agent.py --diff-file ./my_change.diff --mode real --enable-llm
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Ensure the example root (and thus the ``agent`` package) is importable
# even when invoked as a bare script.
_EXAMPLE_ROOT = Path(__file__).resolve().parent
if str(_EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_ROOT))

load_dotenv(_EXAMPLE_ROOT / ".env")


def main() -> int:
    """Parse CLI args and run the orchestration pipeline."""
    from agent.agent import main as _agent_main

    return _agent_main()


if __name__ == "__main__":
    raise SystemExit(main())
