# Evaluation + Optimization Loop — Stage 4

This example provides an auditable evaluation and prompt-optimization loop.
Stages 1–3 prepare an isolated prompt workspace, run baseline and candidate
evaluations on both train and validation datasets, normalize failures, build
case diffs, detect overfitting, and apply an independent Gate. Stage 4 adds a
common Candidate Provider boundary, an `AgentOptimizer` adapter, and guarded
source-prompt writeback.

The deterministic fake mode still runs without a model, API key, judge, or
optimizer. Its built-in scenarios produce ACCEPT for `improve` and REJECT for
both `no_improvement` and `overfit`:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id local_stage4 \
  --scenario improve
```

The offline CLI remains self-contained and supports fake mode only. For an
explicit real integration smoke run, configure the OpenAI-compatible business
model connection in the environment:

```bash
export TRPC_AGENT_API_KEY=...
export TRPC_AGENT_BASE_URL=...
export TRPC_AGENT_MODEL_NAME=...

.venv/bin/python examples/optimization/eval_optimize_loop/run_real_integration.py \
  --run-real \
  --optimizer-model-name mimo-v2.5 \
  --max-candidate-proposals 1
```

The business model uses the three environment values above. The reflection
optimizer model is selected explicitly with CLI arguments; its endpoint and
credential reuse the environment without writing their resolved values to run
artifacts. `--run-real` is mandatory so an accidental command cannot spend API
quota. This entry always uses `pipeline.real.json`, where source writeback is
disabled, and reports ACCEPT or REJECT without treating REJECT as a process
failure.

Applications with a custom agent can still use the Python integration point.
Set `execution.mode` to `real`, prepare the run, and inject an async
SDK-compatible `call_agent`:

```python
import asyncio

from examples.optimization.eval_optimize_loop.pipeline import prepare_run
from examples.optimization.eval_optimize_loop.pipeline import run_real_stage

prepared = prepare_run("examples/optimization/eval_optimize_loop/pipeline.json")
agent = MyBusinessAgent(target_prompt=prepared.working_target)
result = asyncio.run(
    run_real_stage(prepared, call_agent=agent.call_agent)
)
```

The injected agent must reread `prepared.working_target` on every call. The
same callable is used for baseline regression, optimizer search, and final
candidate regression. Optimizer minibatches never replace the four complete
pipeline evaluations: baseline train/validation and candidate train/validation.

`AgentOptimizer` always receives the isolated working target and
`update_source=False`. Its native `result.json`, round records, prompt snapshots,
scores, and configuration snapshot are retained under `runs/<run-id>/optimizer/`
when `artifacts.retain_optimizer_native_artifacts` is enabled.

Source prompts are updated only when all of these conditions hold:

- Gate returns ACCEPT;
- `writeback.enabled` is true;
- every source Prompt still matches its preparation-time SHA-256 hash;
- the write succeeds and an exact readback matches the accepted candidate.

Writeback returns `written`, `skipped`, `blocked`, or `failed` with an auditable
reason. Gate rejection and disabled writeback are skipped; concurrent source
edits are blocked; recoverable write/readback failures are rolled back and
returned as failed. If rollback integrity cannot be proven, the pipeline raises
an error instead of claiming the source is safe. The checked-in configuration
keeps writeback disabled by default.

Elapsed duration is observable. Full monetary cost and token usage remain
`unavailable` because an injected business agent may make calls that the SDK
optimizer does not account for; native optimizer resource fields are retained
without treating missing values as zero. JSON/Markdown reports and an artifact
index are Stage 5 work.

Run the Stage 1–4 tests with:

```bash
.venv/bin/pytest -q \
  tests/evaluation/test_eval_optimize_loop_stage1.py \
  tests/evaluation/test_eval_optimize_loop_stage2.py \
  tests/evaluation/test_eval_optimize_loop_stage3a.py \
  tests/evaluation/test_eval_optimize_loop_stage3b.py \
  tests/evaluation/test_eval_optimize_loop_stage4.py \
  tests/evaluation/test_eval_optimize_loop_real_integration.py
```
