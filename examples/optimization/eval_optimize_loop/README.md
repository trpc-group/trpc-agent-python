# Evaluation + Optimization Closed Loop

This example implements issue #91 as a reproducible evaluation + optimization
loop. The default path is deterministic fake mode, so it runs in CI and on a
fresh checkout without `TRPC_AGENT_API_KEY` or any external model provider. A
real SDK adapter path is also present in `eval_loop/backends.py` for
`AgentOptimizer` / `TargetPrompt` integration.

## Architecture

```text
train.evalset.json + val.evalset.json + optimizer.json + baseline prompt
        |
        v
loader.py / config.py  --> validated cases, gate config, input hashes
        |
        v
backends.py
  |-- FakeBackend -> FakeModel + FakeJudge + FakeOptimizer
  `-- SDKBackend  -> AgentOptimizer + TargetPrompt + user call_agent
        |
        v
attribution.py -> evaluator.py -> gate.py -> report.py
        |
        v
optimization_report.json / optimization_report.md / runs/<run_id>/ audit files
```

## Quick Start

One-command fake mode:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --fake-model --fake-judge --trace
```

Equivalent new form:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake --trace
```

Full fake command:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --train examples/optimization/eval_optimize_loop/data/train.evalset.json \
  --val examples/optimization/eval_optimize_loop/data/val.evalset.json \
  --optimizer-config examples/optimization/eval_optimize_loop/data/optimizer.json \
  --prompt examples/optimization/eval_optimize_loop/prompts/baseline_system_prompt.txt \
  --output-dir /tmp/eval-optimize-loop \
  --mode fake \
  --trace
```

SDK adapter command shape:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --mode sdk \
  --train path/to/sdk_train.evalset.json \
  --val path/to/sdk_val.evalset.json \
  --optimizer-config path/to/sdk_optimizer.json \
  --prompt path/to/system_prompt.txt \
  --sdk-call-agent your_package.your_module:call_agent \
  --output-dir /tmp/eval-optimize-loop-sdk
```

Optional wrapper gate and multi-prompt form:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --mode sdk \
  --train path/to/sdk_train.evalset.json \
  --val path/to/sdk_val.evalset.json \
  --optimizer-config path/to/sdk_optimizer.json \
  --gate-config path/to/wrapper_gate.json \
  --target-prompt system_prompt=prompts/system.md \
  --target-prompt router_prompt=prompts/router.md \
  --sdk-call-agent your_package.your_module:call_agent \
  --run-id local-sdk-smoke \
  --output-dir /tmp/eval-optimize-loop-sdk
```

`--sdk-call-agent` must point to an async callable compatible with
`AgentOptimizer.optimize(call_agent=...)`. Configure any real model credentials
needed by that callable. SDK mode never silently falls back to fake mode. The
generated reproducibility command records the actual `--sdk-call-agent`
`module:function` target and file/config paths, but it does not record API keys
or other provider secrets.

SDK optimizer config and wrapper gate config are intentionally separate.
`--optimizer-config` is passed unchanged to `AgentOptimizer.optimize`, so it must
follow the SDK `OptimizeConfigFile` schema. Put wrapper-only gate settings in
`--gate-config` (for example `{"gate": {"min_val_score_improvement": 0.05,
"max_total_cost": 1.0}}`). If `--gate-config` is omitted, the wrapper uses the
same default aggregate gate values as the fake example.

`--target-prompt name=path` may be repeated. If omitted, SDK mode keeps the old
single-field behavior and registers `system_prompt=--prompt`. A run can optimize
only `router_prompt`, only `skill_prompt`, or any set of named fields as long as
`OptimizeResult.best_prompts` returns every registered field.

Fake mode is the complete per-case closed loop. SDK mode is the real
`AgentOptimizer` / `TargetPrompt` path with an aggregate wrapper gate. When the
SDK optimizer returns a best prompt, this wrapper maps `OptimizeResult`
aggregate fields into the JSON/Markdown report: baseline/best pass rate,
pass-rate improvement, metric breakdowns, token usage, duration, LLM cost,
all `best_prompts`, and round summaries. SDK mode applies
`gate_status: partial_applied`: it checks `status == SUCCEEDED`, validation
improvement against `gate.min_val_score_improvement`, and total LLM cost against
`gate.max_total_cost`. Protected-case regression, new-hard-failure, per-case
delta, and per-case score-drop checks are not claimed in SDK mode unless the SDK
exposes full per-case validation scores; they are listed in
`not_applied_checks`.

Fake mode uses a deterministic run id (`eval_optimize_loop_seed_<seed>`) so the
example outputs are byte-stable. SDK mode is append-only by default: the wrapper
derives a compact UTC `run.run_id` from the SDK result `started_at` when
available, otherwise from the current UTC timestamp. Pass `--run-id` only when
a fixed audit path is useful for tests or local smoke runs; only explicit
`--run-id` values are included in the reproducibility command.

## Source Prompt Writes

The default is **no source write-back**. The baseline prompt file is not modified
by fake mode, and `SDKBackend` calls `AgentOptimizer.optimize(update_source=False)`
unless `--update-source` is explicitly passed. The report records
`run.update_source` and the Markdown report states whether source write-back was
enabled.

## Candidate Behavior

The fake optimizer proposes exactly two candidates:

- `candidate_001_overfit`: fixes train formatting but forces JSON too broadly;
  it improves train score and regresses validation, so the gate rejects it.
- `candidate_002_safe`: applies strict JSON/exact-answer behavior only when
  requested; it improves validation without protected-case regression, so the
  gate may accept it.

The fake model is driven by `EvalCase.expectation`, `tags`, `protected`, and
optional `simulated_outputs`; it does not depend on sample `case_id` names.

## Reports

`optimization_report.json` includes:

- `schema_version`;
- `run` metadata: mode, fake flags, trace flag, case counts, update-source flag,
  and input paths;
- `baseline` plus compatibility fields `baseline_train` and
  `baseline_validation`;
- all candidate train/validation results, rationale, and prompt diff;
- per-case deltas with `delta_type` (`new_pass`, `new_fail`, `score_up`,
  `score_down`, `unchanged`);
- failure attribution summary and attribution accuracy when expected labels are
  present;
- gate decisions with overfit detection, protected regressions, new hard
  failures, excessive drops, cost fields, and SDK `not_applied_checks` when
  per-case validation details are not exposed;
- audit data: seed, duration, config hash, input hashes, candidate prompt hashes,
  cost, prompt diffs, and reproducibility command.

`gate.max_total_cost` is interpreted as the total evaluated run cost at the time
each candidate is judged: baseline cost plus all candidates evaluated so far,
including rejected candidates. This makes budget decisions deterministic and
auditable when multiple candidates are considered.

`optimization_report.md` includes final decision, gate reasons, score table,
per-case delta table, failure attribution summary, cost/audit details, prompt
diffs, and the reproducibility command.

`report.py` also writes audit artifacts under `output_dir/runs/<run_id>/`:

- `config.snapshot.json`;
- `input_hashes.json` with train, validation, optimizer, prompt,
  `target_prompts.<field>`, and optional `gate_config` hashes;
- fake mode: `candidate_prompts/<candidate_id>/system_prompt.txt`;
- SDK mode: `candidate_prompts/<candidate_id>/<field_name>.txt` for every
  returned `best_prompts` field, plus `bundle.txt` with the combined prompt
  shown in the wrapper report;
- `case_results/<candidate_id>_<split>.json`;
- `prompt_diffs/<candidate_id>.diff`.

The repository keeps only stable examples:

- `outputs/optimization_report.example.json`
- `outputs/optimization_report.example.md`

Runtime `optimization_report.json`, `optimization_report.md`, and `runs/`
directories are not committed.

## Run Tests

```bash
python -m pytest examples/optimization/eval_optimize_loop/tests
```

The tests cover fake hidden-sample generalization, config validation, gate
rejection paths, protected-case behavior, failure attribution, tool/knowledge
judge paths, SDK adapter wiring through monkeypatching, deterministic report
generation, and both CLI forms. CI can run fake mode plus the monkeypatched SDK
smoke tests without real API credentials; real SDK/model calls are opt-in local
or integration runs.
