#!/usr/bin/env python3
"""Eval-Optimize Loop CLI entry point r11.

Usage:
    python run_pipeline.py                    # fake mode (fast smoke test)
    python run_pipeline.py --max-iter 3       # max optimization iterations
    python run_pipeline.py --quiet            # minimal output
"""

import argparse, asyncio, json, os as _os, sys
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
    except Exception as e:
        print(f"Warning: cannot read critical case ids from {val_path}: {e}", file=sys.stderr)
        return []  # empty: skip critical-case gate rather than guess wrong id


async def main():
    parser = argparse.ArgumentParser(description="Eval-Optimize Loop Pipeline")
    parser.add_argument("--mode", default="fake", choices=["fake", "real", "real-agent"])
    parser.add_argument("--max-iter", type=int, default=None)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--train", type=str, default=None)
    parser.add_argument("--val", type=str, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    # Gate unimplemented modes at CLI to prevent cryptic NotImplementedError (AI review round 3)
    if args.mode in ("real", "real-agent"):
        print("Error: --mode real / real-agent is not yet implemented.", file=sys.stderr)
        print("Use --mode fake for the working 6-phase smoke-test pipeline.", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    train_path = Path(args.train) if args.train else BASE_DIR / "config" / "train.evalset.json"
    val_path = Path(args.val) if args.val else BASE_DIR / "config" / "val.evalset.json"
    output_dir = Path(args.output) if args.output else BASE_DIR / "output"
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_mode = args.mode

    # ---- PID-based lock in output directory ----
    LOCK_FILE = _os.path.join(str(output_dir), ".pipeline.lock")
    _os.makedirs(str(output_dir), exist_ok=True)

    def _pid_alive(pid):
        """Check if a process is running. Cross-platform: signal 0 on Unix,
        OpenProcess on Windows. Returns False for dead/invalid PIDs."""
        # Unix: os.kill(pid, 0) raises if process doesn't exist
        try:
            _os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but we can't signal it
        except OSError:
            pass  # fall through to platform check

        # Windows: use kernel32.OpenProcess
        if sys.platform == "win32":
            try:
                import ctypes
                h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    return True
                return False
            except Exception:
                return False  # cannot verify liveness; treat as dead so stale lock can be cleaned
        # On Unix, if os.kill(pid, 0) succeeded above, process exists
        return True

    my_pid = _os.getpid()

    # Atomic acquire: O_CREAT|O_EXCL fails if file exists (cross-platform)
    acquired = False
    try:
        fd = _os.open(LOCK_FILE, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o644)
        with _os.fdopen(fd, "w", encoding="utf-8") as lf:
            lf.write(f"{my_pid} {started_at}")
            lf.flush()
            _os.fsync(lf.fileno())
        acquired = True
    except FileExistsError:
        # Lock exists -- check if owner is alive
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as lf:
                old_pid = int(lf.read().strip().split()[0])
            if _pid_alive(old_pid):
                print("another pipeline instance is running, aborting", file=sys.stderr)
                sys.exit(75)
            if not args.quiet:
                print(f"Cleaning stale lock from dead PID {old_pid}", file=sys.stderr)
            _os.remove(LOCK_FILE)
            fd = _os.open(LOCK_FILE, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o644)
            with _os.fdopen(fd, "w", encoding="utf-8") as lf:
                lf.write(f"{my_pid} {started_at}")
                lf.flush()
                _os.fsync(lf.fileno())
            acquired = True
        except (FileNotFoundError, ValueError, FileExistsError):
            pass

    if not acquired:
        print("cannot acquire pipeline lock, aborting", file=sys.stderr)
        sys.exit(75)

    # ---- try/finally: ensure lock release even on exception ----
    try:
        if not args.quiet:
            print(f"Eval-Optimize Loop | mode={run_mode} seed={args.seed}")
            print()

        # Phase 1: Baseline
        if not args.quiet: print("[1/6] Baseline...")
        br = BaselineRunner(mode=run_mode)
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
        pipeline_cfg = config.get("pipeline", {})
        if args.max_iter is not None:  # user explicitly set --max-iter
            pipeline_cfg = dict(pipeline_cfg, max_iterations=args.max_iter)
        opt_runner = OptimizationRunner(mode=run_mode, config=pipeline_cfg)
        opt_result = opt_runner.run(attr)
        if not args.quiet: print(f"  candidates: {opt_result.total_iterations}")

        # Phase 4: Validation
        if not args.quiet: print("[4/6] Validation...")
        vr = ValidationRunner(mode=run_mode)
        val_result = vr.run(val_bl, opt_result)
        if not args.quiet: print(f"  delta: {val_result.summary.avg_score_delta:+.3f}")

        # Phase 5: Gate
        if not args.quiet: print("[5/6] Gate...")
        gate = AcceptanceGate(config.get("gate", {}))

        # FAKE MODE NOTICE: candidate_train_scores, candidate_cost, and baseline_cost
        # are simulated placeholder values. Gate decisions in fake mode are for
        # pipeline demo purposes only and do not reflect real optimization outcomes.
        # Real mode would re-evaluate with the optimized agent on the training set.
        #
        # Overfit detection caveat: candidate_train_scores uses a flat +0.05 delta
        # which makes train_improved always true in fake mode.  This means
        # overfit_detection degenerates to "reject iff val regresses" and is NOT
        # a genuine overfit signal.  In real mode, train scores come from actual
        # re-evaluation with the optimized prompt.
        # overfit detection: simulate candidate train scores with +0.05 delta
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
        # release lock — only if we still own it (avoid removing another process's lock)
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as lf:
                lock_pid = int(lf.read().strip().split()[0])
            if lock_pid == my_pid:
                _os.remove(LOCK_FILE)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())