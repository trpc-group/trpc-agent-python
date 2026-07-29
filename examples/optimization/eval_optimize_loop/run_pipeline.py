# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Evaluation + Optimization Pipeline — main entry point.

Usage:
    # Trace mode (no API key needed)
    python run_pipeline.py

    # Live mode (requires TRPC_AGENT_API_KEY)
    TRPC_AGENT_API_KEY=sk-xxx TRPC_AGENT_BASE_URL=https://api.deepseek.com \
        TRPC_AGENT_MODEL_NAME=deepseek-v4-flash python run_pipeline.py

The pipeline auto-detects mode: if API key env vars are missing, it falls
back to trace mode using pre-recorded conversations.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ---- Path bootstrap ----
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _detect_mode() -> tuple[bool, str]:
    """Detect whether to use trace mode.

    Returns (is_trace, reason).
    """
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")

    missing = []
    if not api_key:
        missing.append("TRPC_AGENT_API_KEY")
    if not base_url:
        missing.append("TRPC_AGENT_BASE_URL")
    if not model_name:
        missing.append("TRPC_AGENT_MODEL_NAME")

    if missing:
        return True, f"Missing env vars: {missing}. Using trace mode."
    return False, f"All env vars present. Using live mode with {model_name}."


async def main() -> None:
    """Entry point: detect mode, build context, run pipeline."""
    from pipeline.orchestrator import run_pipeline
    from pipeline.config import PipelineContext, PipelineConfig

    is_trace, reason = _detect_mode()
    print(f"Mode: {'TRACE' if is_trace else 'LIVE'}")
    print(f"  {reason}")

    # Load pipeline config
    config_path = _HERE / "pipeline_config.json"
    pconfig = PipelineConfig.from_file(str(config_path))
    if pconfig.mode == "trace":
        is_trace = True
    elif pconfig.mode == "live":
        is_trace = False

    # Select evalsets
    agent_dir = _HERE / "agent"
    if is_trace:
        train_path = str(agent_dir / "trace_train.evalset.json")
        val_path = str(agent_dir / "trace_val.evalset.json")
    else:
        train_path = str(agent_dir / "train.evalset.json")
        val_path = str(agent_dir / "val.evalset.json")

    # Output directory
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    output_dir = str(_HERE / "output" / timestamp)

    # Build context
    ctx = PipelineContext(
        project_dir=_HERE,
        pipeline_config_path=str(config_path),
        optimizer_config_path=str(agent_dir / "optimizer.json"),
        train_path=train_path,
        val_path=val_path,
        output_dir=output_dir,
        is_trace_mode=is_trace,
        start_time=time.time(),
    )

    print(f"Train set: {train_path}")
    print(f"Val set:   {val_path}")
    print(f"Output:    {output_dir}")
    print(f"Baseline preset: {pconfig.baseline_prompt_preset}")
    print(f"Overfit gate:    reject={pconfig.gate.reject_overfit}, "
          f"train_gain>={pconfig.gate.overfit_train_gain}, "
          f"val_loss<={pconfig.gate.overfit_val_loss}")

    # Run pipeline (Stage 0 materializes isolated runtime prompt snapshots)
    ctx = await run_pipeline(ctx, pconfig.gate, pconfig=pconfig)

    # Final summary
    total = time.time() - ctx.start_time
    print(f"\n{'='*60}")
    print(f"Pipeline complete in {total:.1f}s")
    if ctx.gate_decision:
        decision = "ACCEPTED" if ctx.gate_decision.accepted else "REJECTED"
        print(f"Gate decision: {decision}")
        print(f"  {ctx.gate_decision.reason}")
    print(f"Reports: {output_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
