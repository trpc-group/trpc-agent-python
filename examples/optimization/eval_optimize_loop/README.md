# Evaluation + Optimization Loop — Stage 5

This example provides an auditable evaluation and prompt-optimization loop.
Stages 1–3 prepare an isolated prompt workspace, run baseline and candidate
evaluations on both train and validation datasets, normalize failures, build
case diffs, detect overfitting, and apply an independent Gate. Stage 4 adds a
common Candidate Provider boundary, an `AgentOptimizer` adapter, and guarded
source-prompt writeback. Stage 5 publishes a complete JSON/Markdown report and
artifact index, or preserves a standalone failure report when a run fails.

The deterministic fake mode still runs without a model, API key, judge, or
optimizer. Its built-in scenarios produce ACCEPT for `improve` and REJECT for
both `no_improvement` and `overfit`:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --run-id local_stage5 \
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
failure. Both CLIs print the paths of the JSON report, Markdown report, and
artifact index after a completed run.

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

## Report artifacts

A completed run atomically publishes the formal bundle at
`runs/<run-id>/report/`:

```text
report/
├── optimization_report.json
├── optimization_report.md
├── artifact_index.json
├── inputs/
│   ├── pipeline_config.json
│   ├── optimizer_config.json
│   ├── train_evalset.json
│   └── validation_evalset.json
├── evaluations/
│   ├── baseline_train.json
│   ├── baseline_validation.json
│   ├── candidate_train.json
│   └── candidate_validation.json
└── prompts/
    ├── baseline/
    └── candidate/
```

The formal `report/` directory is visible only after all required artifacts
have been written and validated. If any post-preparation phase fails, the
pipeline does not leave a partial formal report; it atomically writes
`runs/<run-id>/failure_report.json` with the failed phase, completed phases,
sanitized error information, source Prompt hashes, and already existing
artifacts. A failure-report write error is surfaced together with the original
pipeline error.

`artifact_index.json` records each artifact's relative path, SHA-256 hash, byte
size, producer phase, and availability. Consumers can use those fields to
verify that an artifact has not drifted since publication. Input copies are
validated against the preparation snapshot, and sensitive resolved credentials
are not accepted into the report bundle.

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

The report separates whole-pipeline resources from optimizer-only observations.
Pipeline duration is observable, while full pipeline monetary cost and token
usage remain `unavailable` because business-agent calls may not expose complete
telemetry. In real mode, optimizer rounds, reflection calls, duration, cost, and
token usage are reported independently from the native optimizer result; an
unreliable or incomplete field stays `unavailable` instead of being treated as
zero. In fake mode, optimizer-only fields are `not_applicable`.

Run the Stage 1–5 tests with:

```bash
.venv/bin/pytest -q \
  tests/evaluation/test_eval_optimize_loop_stage1.py \
  tests/evaluation/test_eval_optimize_loop_stage2.py \
  tests/evaluation/test_eval_optimize_loop_stage3a.py \
  tests/evaluation/test_eval_optimize_loop_stage3b.py \
  tests/evaluation/test_eval_optimize_loop_stage4.py \
  tests/evaluation/test_eval_optimize_loop_real_integration.py \
  tests/evaluation/test_eval_optimize_loop_stage5_report_builder.py \
  tests/evaluation/test_eval_optimize_loop_stage5_artifacts.py \
  tests/evaluation/test_eval_optimize_loop_stage5_pipeline.py
```
