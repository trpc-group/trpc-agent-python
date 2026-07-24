#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""One-command entry point for the evaluation/optimization regression loop."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from datetime import timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_OPTIMIZATION_EXAMPLES = _HERE.parent
for path in (_REPO_ROOT, _OPTIMIZATION_EXAMPLES):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval_optimize_loop.loop import EvalOptimizePipeline
from eval_optimize_loop.loop import PipelineSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=("Run baseline evaluation, real GEPA prompt optimization, "
                                                  "independent regression, gate, and reports without an API key."), )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_HERE / "pipeline.json",
        help="Pipeline manifest (default: pipeline.json beside this script).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Artifact directory. Default: runs/<UTC timestamp>.",
    )
    parser.add_argument(
        "--apply-if-accepted",
        action="store_true",
        help="Atomically write the selected prompt back only after every gate passes.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        output_dir = _HERE / "runs" / stamp
    spec = PipelineSpec.from_file(
        args.manifest,
        output_dir=output_dir,
    )
    if args.apply_if_accepted:
        spec = spec.model_copy(update={"apply_if_accepted": True})
    report = await EvalOptimizePipeline(spec).run()
    print(f"decision={report.status}")
    print(f"selected_candidate={report.selected_candidate_id or 'none'}")
    print(f"json_report={spec.output_dir / 'optimization_report.json'}")
    print(f"markdown_report={spec.output_dir / 'optimization_report.md'}")
    return 0 if report.status == "accepted" else 2


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
