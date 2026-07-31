"""Thin CLI for the auditable evaluation and optimization loop."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from examples.optimization.eval_optimize_loop.pipeline.orchestrator import run_pipeline
from examples.optimization.eval_optimize_loop.pipeline.live_adapter import load_callback
from examples.optimization.eval_optimize_loop.pipeline.schema import sanitized_exception_message

_CLI_ERROR_MAX_CHARS = 4000


def _format_cli_error(error: BaseException) -> str:
    error_type = type(error).__name__[:128]
    prefix = f"ERROR: {error_type}: "
    summary = sanitized_exception_message(
        error,
        max_text_chars=_CLI_ERROR_MAX_CHARS - len(prefix),
    )
    return prefix + summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the auditable prompt optimization loop.")
    parser.add_argument("--mode", choices=("fake", "trace", "live"))
    parser.add_argument("--config")
    parser.add_argument("--train")
    parser.add_argument("--validation")
    parser.add_argument("--run-id")
    parser.add_argument("--call-agent", metavar="MODULE:FUNCTION")
    parser.add_argument(
        "--apply-candidate",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    return parser


async def _main(args: argparse.Namespace) -> int:
    callback = load_callback(args.call_agent) if args.call_agent else None
    report = await run_pipeline(
        str(_HERE),
        config_path=args.config,
        train_path=args.train,
        validation_path=args.validation,
        mode=args.mode,
        run_id=args.run_id,
        apply_candidate=args.apply_candidate,
        call_agent=callback,
        callback_spec=args.call_agent,
    )
    print(f"{report.status.value}: run={report.run_id} stage={report.stage}")
    if report.gate_decision and report.gate_decision.reasons:
        print("reasons=" + ",".join(report.gate_decision.reasons))
    return 1 if report.status.value == "ERROR" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main(_parser().parse_args())))
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as error:
        print(_format_cli_error(error), file=sys.stderr)
        raise SystemExit(1)
