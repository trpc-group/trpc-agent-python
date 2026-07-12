# Evaluation + Optimization automatic loop

This example turns prompt optimization into an auditable release decision:

1. evaluate the baseline prompt on separate training and validation sets;
2. attribute every failure from response, tool, argument, knowledge, format, and rubric evidence;
3. generate a candidate `TargetPrompt` from training failure clusters;
4. rerun both datasets and produce per-case regression deltas;
5. accept only when validation improvement, hard-fail, critical-case, regression, and cost gates pass;
6. persist the candidate, traces, seed, cost, duration, decision, and reasons.

Run without an API key:

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py
```

The command reads `train.evalset.json`, `val.evalset.json`, `optimizer.json`, and `prompt.md`, then writes
`optimization_report.json` and `optimization_report.md`. The included traces intentionally show training improvement
combined with validation regression; the candidate must therefore be rejected.

## Production integration

`TraceModel` is the offline adapter. Replace its `run` method with an `AgentEvaluator` call that returns the same trace
fields. `PromptOptimizer.optimize_prompt` can likewise be replaced by `AgentOptimizer.optimize` with a
`TargetPrompt().add_path("system_prompt", "prompt.md")`. Keep `compare`, `apply_gate`, and report serialization outside
the optimizer: this prevents the component proposing a prompt from also deciding whether its own result is safe to
ship. Leave `prompt.update_source=false` until the gate accepts and a reviewer approves the report.

Configuration controls the seed, target prompt, validation threshold, hard-fail policy, critical cases, maximum
per-case regression, and validation cost. Adding a new metric only requires extending `_score`; adding an attribution
requires one evidence-first branch in `FailureAttributor` and paired tests.

See [DESIGN.md](DESIGN.md) for the overfitting and audit design.
