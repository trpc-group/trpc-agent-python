# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Prepare an isolated evaluation/optimization run.

This is intentionally a stage-one-only entry point.  It validates the complete
pipeline contract and makes a prompt workspace, but does not evaluate, optimize,
gate, report, or write source prompts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if __package__ in (None, ""):
    _REPO_ROOT = _HERE.parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from examples.optimization.eval_optimize_loop.pipeline import prepare_run
else:
    from .pipeline import prepare_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare an isolated eval/optimization pipeline run.")
    parser.add_argument("--config", type=Path, default=_HERE / "pipeline.json")
    parser.add_argument("--run-id", help="Optional reproducible run identifier.")
    args = parser.parse_args()

    prepared = prepare_run(args.config, run_id=args.run_id)
    print(f"Prepared pipeline run: {prepared.workspace.run_dir}")
    print("Stage 1 complete: evaluation, optimization, gate, report, and source writeback are not implemented yet.")


if __name__ == "__main__":
    main()
