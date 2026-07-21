#!/usr/bin/env python3
"""Eval-Optimize Loop CLI entry point.

Usage:
    python run_pipeline.py                    # fake mode (fast smoke test)
    python run_pipeline.py --mode real        # real mode (needs PlateAgent)
    python run_pipeline.py --max-iter 3       # max optimization iterations
"""

import argparse, asyncio, json, os as _os, sys, time
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.baseline import BaselineRunner
from src.attribution import AttributionRunner
from src.optimizer import OptimizationRunner
from src.validator import ValidationRunner
from src.auditor import Auditor
from src.reporter import generate_json_report, generate_markdown_report
from src.gate import AcceptanceGate


def load_config():
    with open(BASE_DIR / "config" / "optimizer.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _read_critical_case_ids(val_path: Path) -> list[str]:
    """Dynamically read critical case ids from evalset (fixed: was hardcoded)."""
    try:
        with open(val_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [c["case_id"] for c in data.get("cases", []) if c.get("critical", False)]
    except Exception:
        return ["val_001"]  # fallback


async def main():
    parser = argparse.ArgumentParser(description="Eval-Optimize Loop Pipeline")
    parser.add_argument("--mode", default="fake", choices=["fake", "real", "real-agent"])
    parser.add_argument("--max-iter", type=int, default=3)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--train", type=str, default=None)
    parser.add_argument("--val", type=str, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    config = load_config()
    train_path = Path(args.train) if args.train else BASE_DIR / "config" / "train.evalset.json"
    val_path = Path(args.val) if args.val else BASE_DIR / "config" / "val.evalset.json"
    output_dir = Path(args.output) if args.output else BASE_DIR / "output"
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_mode = args.mode

    # ---- concurrent lock (mkdir atomic) ----
    LOCK_DIR = _os.path.join(str(BASE_DIR), "output", ".pipeline.lock")
    try:
        _os.makedirs(LOCK_DIR, exist_ok=False)
    except FileExistsError:
        print("another pipeline instance is running, aborting", file=sys.stderr)
        sys.exit(75)

    # ---- try/finally: ensure lock release even on exception (fix: Critical) ----
    try:
        if not args.quiet:
            print(f"Eval-Optimize Loop | mode={run_mode} seed={args.seed}")
            print()

        # Phase 1: Baseline
        if not args.quiet: print("[1/6] Baseline...")
        br = BaselineRunner(mode=run_mode if run_mode in ("real",) else "fake",
                           plate_agent_root=str(BASE_DIR.parent.parent.parent / "plate-agent"))
        baseline = await br.run(train_path, val_path)
        train_bl, val_bl = baseline["train"], baseline["val"]
        if not args.quiet:
            print(f"  train: {train_bl.summary.pass_rate:.1%} val: {val_bl.summary.pass_rate:.1%}")

        # Phase 2: Attribution
        if not args.quiet: print("[2/6] Attribution...")
        ar = AttributionRunner()
        attr = ar.run(train_bl, val_bl)
        if not args.quiet:
            p = attr.primary_failure_category
            print(f"  failures: {attr.total_failures} primary: {p.category if p else 'none'}")

        # Phase 3: Optimization
        if not args.quiet: print("[3/6] Optimization...")
        use_real_agent = run_mode == "real-agent"
        if use_real_agent:
            sdk_train = BASE_DIR / "config" / "train.sdk.evalset.json"
            sdk_val = BASE_DIR / "config" / "val.sdk.evalset.json"
            sdk_opt = BASE_DIR / "config" / "optimizer.sdk.json"
            prompt_dir = BASE_DIR / "config" / "prompts"
            from src.call_agent import echo_call_agent
            opt_runner = OptimizationRunner(
                mode="real",
                config=config.get("pipeline", {}),
                call_agent=echo_call_agent,
                train_dataset=str(sdk_train),
                validation_dataset=str(sdk_val),
                sdk_config_path=str(sdk_opt),
                prompt_dir=str(prompt_dir),
                output_dir=str(output_dir / "optimizer"),
            )
        else:
            opt_runner = OptimizationRunner(mode="fake", config=config.get("pipeline", {}))
        opt_result = opt_runner.run(attr)
        if not args.quiet: print(f"  candidates: {opt_result.total_iterations}")

        # Phase 4: Validation
        if not args.quiet: print("[4/6] Validation...")
        vr = ValidationRunner(mode=run_mode if run_mode in ("real",) else "fake")
        val_result = vr.run(val_bl, opt_result)
        if not args.quiet: print(f"  delta: {val_result.summary.avg_score_delta:+.3f}")

        # Phase 5: Gate
        if not args.quiet: print("[5/6] Gate...")
        gate = AcceptanceGate(config.get("gate", {}))

        # fix: overfit detection — run candidate on train set for real delta
        if run_mode == "real":
            candidate_train = await br.run_split(train_path, "train_candidate")
            candidate_train_scores = candidate_train.score_map
        else:
            # fake mode: derive candidate train scores from baseline + simulated delta
            candidate_train_scores = {
                cid: min(1.0, score + 0.05) for cid, score in train_bl.score_map.items()
            }

        critical_case_ids = _read_critical_case_ids(val_path)

        decision = gate.decide(
            baseline_scores=val_bl.score_map,
            candidate_scores=val_result.score_map,
            baseline_train_scores=train_bl.score_map,
            candidate_train_scores=candidate_train_scores,
            baseline_cost=val_bl.summary.avg_cost * val_bl.summary.total,
            candidate_cost=val_result.summary.total_cost_candidate,
            critical_case_ids=critical_case_ids,
        )
        gate_dict = {
            "accepted": decision.accepted,
            "reason": decision.reason,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in decision.checks],
        }
        if not args.quiet: print(f"  decision: {'ACCEPTED' if decision.accepted else 'REJECTED'}")

        # Phase 6: Audit
        if not args.quiet: print("[6/6] Audit...")
        auditor = Auditor(output_dir=output_dir)
        trail = auditor.build_trail(
            pipeline_name="PlateAgent Eval-Optimize Loop",
            mode=run_mode, random_seed=args.seed,
            optimization=opt_result, baseline_val=val_bl,
            validation=val_result, gate_decision=gate_dict,
            started_at=started_at,
        )
        audit_path = auditor.save(
            audit_trail=trail, baseline=baseline, attribution=attr,
            optimization=opt_result, validation=val_result, gate_decision=gate_dict,
        )

        report_dir = output_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        generate_json_report(train_bl, val_bl, attr, opt_result, val_result, gate_dict,
                              report_dir / "optimization_report.json")
        generate_markdown_report(train_bl, val_bl, attr, opt_result, val_result, gate_dict,
                                  report_dir / "optimization_report.md")

        if not args.quiet:
            print(f"  audit: {audit_path}")
            print(f"  reports: {report_dir}")
            print("Done. 6 phases completed.")

    finally:
        # release lock — always runs, even on exception
        try:
            _os.rmdir(LOCK_DIR)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())