"""Command-line entry point for the evaluation optimization loop."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from examples.optimization.eval_optimize_loop.loop.models import InputPaths
from examples.optimization.eval_optimize_loop.loop.models import PipelineOptions
from examples.optimization.eval_optimize_loop.loop.pipeline import run_pipeline

DEFAULT_ROOT = Path(__file__).parent
DEFAULT_OUTPUT = DEFAULT_ROOT / "artifacts"
REAL_MODE = "real"
DEFAULT_REAL_MODEL_NAME = "real"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_ROOT / "agent/prompts/system.md")
    parser.add_argument("--train", type=Path, default=DEFAULT_ROOT / "data/train.evalset.json")
    parser.add_argument("--validation", type=Path, default=DEFAULT_ROOT / "data/val.evalset.json")
    parser.add_argument("--optimizer", type=Path, default=DEFAULT_ROOT / "optimizer.json")
    parser.add_argument("--gate", type=Path, default=DEFAULT_ROOT / "gate.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mode", choices=("fake-model", "real", "trace"), default="fake-model")
    parser.add_argument("--model-name")
    parser.add_argument("--trace-file", type=Path)
    parser.add_argument("--write-back", action="store_true")
    parser.add_argument("--fake-judge", action="store_true")
    return parser


def _model_name(mode: str, explicit_name: str | None) -> str:
    if explicit_name:
        return explicit_name
    if mode == REAL_MODE:
        return os.getenv("TRPC_AGENT_MODEL_NAME", DEFAULT_REAL_MODEL_NAME)
    return "fake-model"


def main() -> int:
    args = _parser().parse_args()
    paths = InputPaths(
        prompt_path=args.prompt,
        train_path=args.train,
        validation_path=args.validation,
        optimizer_path=args.optimizer,
        gate_path=args.gate,
    )
    result = asyncio.run(
        run_pipeline(
            PipelineOptions(
                paths=paths,
                output_dir=args.output,
                mode=args.mode,
                model_name=_model_name(args.mode, args.model_name),
                trace_file=args.trace_file,
                fake_judge=args.fake_judge,
                write_back=args.write_back,
            )))
    print(f"{result.report.status}: {result.json_path}")
    print(result.markdown_path)
    return 1 if result.report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
