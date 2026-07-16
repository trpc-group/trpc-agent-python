# Eval + Optimize Closed Loop

A reproducible Evaluation + Optimization pipeline that builds a closed loop of "baseline evaluation → failure attribution → prompt optimization → candidate validation → acceptance gating → audit reporting".

## Quickstart (trace mode, no API keys)

```bash
source .venv/bin/activate
python run_pipeline.py
```

This runs against the 6 sample cases (3 train, 3 val) using pre-recorded traces. No API keys required.

## Pipeline stages

1. **Baseline Evaluation** — Runs AgentEvaluator on train and val evalsets against the baseline prompt, recording per-case metrics, pass/fail status, and key trajectories.
2. **Failure Attribution** — Clusters failed cases by type: final_response_mismatch, format_violation, etc. Each failed case gets at least one attributed category.
3. **Optimization** — In live mode, delegates to AgentOptimizer (GEPA reflective optimization). In trace mode, uses a pre-cooked optimized prompt.
4. **Candidate Validation** — Re-evaluates the candidate prompt on both train and val sets, producing per-case results for delta comparison.
5. **Delta Analysis** — Compares baseline vs candidate per case: newly passing, newly failing, per-metric score deltas.
6. **Acceptance Gate** — Configurable rules: min_improvement, allow_new_fails, protected_case_ids, max_cost_usd, max_duration_seconds. Detects overfitting (train improves but val degrades).
7. **Audit Reports** — Generates optimization_report.json (machine-readable) and optimization_report.md (human-readable).

## Configuration

See `pipeline.json` for trace mode or create your own. Key sections:

- `mode`: `"trace"` (no API keys) or `"live"` (requires call_agent)
- `evaluate`: Metric definitions and thresholds
- `gate`: Acceptance rules

## Sample data

The 6 sample cases are designed to demonstrate three scenarios:
- **Optimizable**: Case fails with baseline, passes with optimized prompt
- **No improvement**: Case fails with both prompts
- **Regression**: Case passes with baseline, fails with optimized prompt

With `allow_new_fails: false`, the gate correctly REJECTS when the candidate introduces new failures (anti-overfitting protection).

## Live mode

To use live mode with your own agent:

```python
from pipeline import EvalOptimizePipeline
from trpc_agent_sdk.evaluation import TargetPrompt

target = TargetPrompt().add_path("system_prompt", "path/to/prompt.md")
pipeline = EvalOptimizePipeline.from_config(
    "pipeline.json",
    call_agent=your_call_agent,
    target_prompt=target,
)
result = await pipeline.run()
```

## Output

- `outputs/optimization_report.json` — Full structured result
- `outputs/optimization_report.md` — Human-readable summary with verdict, pass rates, per-case delta table, failure attribution, gate results, overfitting check, and audit trail
