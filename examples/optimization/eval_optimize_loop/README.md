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
  `-- SDKBackend  -> AgentOptimizer + TargetPrompt + user call_agent + AgentEvaluator
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
or other provider secrets. Persisted optimizer config snapshots recursively
replace credential values with `<redacted>`.

The checked-in `train.evalset.json` and `val.evalset.json` use the SDK
`EvalSet` schema (`eval_set_id` / `eval_cases`). Fake mode reads its
deterministic judge metadata from each case's `session_input.state`
(`eval_optimize_expectation`, `eval_optimize_tags`, and
`eval_optimize_protected`).

SDK optimizer config and wrapper gate config are intentionally separate.
`--optimizer-config` is passed unchanged to `AgentOptimizer.optimize`, so it must
follow the SDK `OptimizeConfigFile` schema. Put wrapper-only gate settings in
`--gate-config` (for example `{"gate": {"min_val_score_improvement": 0.05,
"allow_new_hard_fail": false, "max_score_drop_per_case": 0.0,
"max_total_cost": null}}`). If `--gate-config` is omitted, the wrapper uses the
same default gate values as fake mode and auto-adds validation cases marked with
`eval_optimize_protected`. A numeric `max_total_cost` fails closed with
`cost_unavailable` whenever the backend cannot prove a complete total. Use
`null` only when you intentionally want to run the quality gates without a cost
limit; `reported_optimizer_cost` remains an incomplete lower bound, not a total.

`--target-prompt name=path` may be repeated. If omitted, SDK mode keeps the old
single-field behavior and registers `system_prompt=--prompt`. A run can optimize
only `router_prompt`, only `skill_prompt`, or any set of named fields as long as
`OptimizeResult.best_prompts` returns every registered field.

Fake mode is the deterministic offline closed loop. SDK mode is the real
`AgentOptimizer` / `TargetPrompt` path followed by explicit post-optimization
`AgentEvaluator` runs. The adapter deduplicates every prompt bundle found in
`OptimizeResult.rounds[].candidate_prompts` and `best_prompts`. The shared
pipeline temporarily installs each bundle, reruns the complete train and
validation evalsets, restores the exact source bytes, then computes the same
per-case deltas and complete gate decision used by fake mode. SDK case results
carry real per-metric scores, failure reason/evidence, and `trace_available`;
missing SDK trace data is reported as unavailable rather than fabricated.
Optimizer aggregates remain audit context and are never substituted for these
post-optimization case results.

Both modes default to a collision-resistant run id of the form
`eval_optimize_loop_<mode>_<UTC timestamp>_<random suffix>`. Pass `--run-id`
when a stable path is useful. Existing temporary or final run ids are never
reused or overwritten. The checked-in example is generated in a fresh output
directory with the explicit id `example`.

## Source Prompt Writes

The default is **no source write-back**. `SDKBackend` always calls
`AgentOptimizer.optimize(update_source=False)` so the optimizer can never commit
early. `--update-source` is handled only by the shared pipeline: after all
candidate evaluations, full gate acceptance, input-integrity checks, and audit
preparation, it transactionally writes the selected prompt bundle. This rule is
the same in fake and SDK modes. The report records the requested action and the
terminal `writeback` state.

## Candidate Behavior

The fake optimizer proposes exactly two candidates:

- `candidate_001_overfit`: fixes train formatting but forces JSON too broadly;
  it improves train score and regresses validation, so the gate rejects it.
- `candidate_002_safe`: applies strict JSON/exact-answer behavior only when
  requested; it improves validation without protected-case regression, so the
  gate may accept it.

The fake model reads only the prompt and user input. It cannot inspect
expectations, labels, tags, protected markers, or case ids. Those fields are
available only to the fake judge, failure attribution, and acceptance gate.

## Reports

`optimization_report.json` uses `eval_optimize_loop.v2` and includes:

- `schema_version`;
- `run` metadata: mode, fake flags, trace flag, case counts, update-source flag,
  and input paths;
- `baseline` plus compatibility fields `baseline_train` and
  `baseline_validation`;
- `baseline_prompts`, all candidate prompt bundles, complete train/validation
  results, rationale, and prompt diff;
- optimizer `rounds`, `cost_summary`, and terminal `writeback`;
- per-case deltas with `delta_type` (`new_pass`, `new_fail`, `score_up`,
  `score_down`, `unchanged`);
- failure attribution summary and attribution accuracy when expected labels are
  present;
- gate decisions with overfit detection, protected regressions, new hard
  failures, excessive drops, and cost fields in both fake and SDK modes;
- audit data: seed, real duration, redacted config and raw-file hashes, input
  hashes, candidate artifact mapping, complete/incomplete cost semantics,
  prompt integrity journal, and reproducibility command.

When `CostSummary.complete` is true, `gate.max_total_cost` is interpreted as the
total evaluated run cost when each candidate is judged: baseline cost plus all
candidates evaluated so far, including rejected candidates. When it is false,
the report separates `known_run_cost` and `reported_optimizer_cost`, and any
numeric total-cost gate rejects with `cost_unavailable`.

`optimization_report.md` includes final decision, gate reasons, score table,
per-case delta table, failure attribution summary, cost/audit details, prompt
diffs, and the reproducibility command.

Audit data is first written under `output_dir/runs/.<run_id>.tmp/`. After every
final artifact and prompt-integrity check succeeds, the directory is published
once to `output_dir/runs/<run_id>/` with no-replace semantics. Windows uses
`MoveFileExW` without a replace flag; Linux uses
`renameat2(RENAME_NOREPLACE)`. A POSIX platform without that atomic primitive
fails closed. Published run directories are authoritative and immutable; the
top-level JSON/Markdown files are convenience copies created afterward.

Each run contains `pre_write_report.*`, final reports, a redacted config
snapshot, input hashes, `baseline_prompts/`, every candidate prompt field and
diff, baseline and candidate split results, per-round JSON, per-case deltas,
gate decisions, evaluation failures, `writeback.json`, and the durable
writeback journal. SDK optimizer artifacts are kept beneath `optimizer/` with a
redacted config snapshot. `artifact_manifest.json` maps the logical artifacts
and records the SHA-256 and byte size of every file.

The repository keeps only stable examples:

- `outputs/optimization_report.example.json`
- `outputs/optimization_report.example.md`

Runtime `optimization_report.json`, `optimization_report.md`, and `runs/`
directories are not committed.

## Run Tests

```bash
python -m pytest examples/optimization/eval_optimize_loop/tests
```

The tests cover fake hidden-sample generalization, SDK EvalSet compatibility,
config validation, gate rejection paths, protected-case behavior, failure
attribution, tool/knowledge judge paths, SDK adapter and post-evaluator wiring
through monkeypatching, deterministic report generation, and both CLI forms. CI
can run fake mode plus the monkeypatched SDK smoke tests without real API
credentials; real SDK/model calls are opt-in local or integration runs.
