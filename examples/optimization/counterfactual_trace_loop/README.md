# Trust-Aware Counterfactual Trace Diagnosis Loop

This example closes evaluation and prompt optimization with evidence from trace interventions. Unlike loops that classify a failure from reason text or sample metadata, it deep-copies the actual trace, changes one execution surface, and sends the counterfactual `EvalCase` through the same public `AgentEvaluator` metrics. Evaluation-data, evaluator, and infrastructure failures are excluded from prompt optimization.

## Distinction from other Issue #91 proposals

PR #159 uses rule-first attribution from failure text and static expected/actual trace differences. PR #161 replays optimizer candidates and combines static trace/rubric attribution with bootstrap and Pareto selection. This example addresses a different question: which minimal trace intervention causally repairs the failing metric when the same `AgentEvaluator` evaluates it again?

The attribution evidence is therefore an observed before/after metric delta from `replace_final_response`, `replace_tool_name`, `replace_tool_arguments`, or a bounded combination. Static trace differences can propose an intervention, but cannot by themselves establish the diagnosis. The same mechanism is reused after candidate validation to locate regressions. No case ID, expected failure label, attribution hint, or hand-authored metric score participates in the decision.

## Quick start

```bash
python examples/optimization/counterfactual_trace_loop/run_counterfactual_probe.py
python examples/optimization/counterfactual_trace_loop/run_pipeline.py --mode fake
python examples/optimization/counterfactual_trace_loop/run_pipeline.py --mode trace
python examples/optimization/counterfactual_trace_loop/run_pipeline.py --mode fake --candidate-profile accepted
python examples/optimization/counterfactual_trace_loop/run_pipeline.py --mode fake --candidate-profile ineffective
```

Both fake and trace modes need no API key. Trace mode replays `actual_conversation`; `conversation` remains the expected trace. The fake optimizer is prompt-sensitive and introduces an intentionally broad billing rule without reading case IDs.

## Real optimizer

Fake and trace modes are fully runnable and verified end to end. `pipeline.optimizer.run_real_optimizer()` wires the public `AgentOptimizer.optimize(..., update_source=False)` API to selected `TargetPrompt` files and is verified with a mock/spy, including actionable-case filtering and unified optimization fields. It has not been run against a real model in this example. Production use requires a business `call_agent`, model credentials, and a trace-capture adapter for candidate regression diagnosis.

## Write-back

`--apply` is necessary but not sufficient. Files are written only after every gate check passes. The implementation records baseline hashes, calls `TargetPrompt.write_all()` once, verifies changed hashes, and restores the baseline on failure. The bundled candidate is rejected, so running with `--apply` leaves all source prompts unchanged.

## Outputs

- `prototype_output/counterfactual_probe.{json,md}`: feasibility evidence.
- `sample_output/optimizer_failure_digest.json`: actionable and excluded failures.
- `sample_output/optimization_report.{json,md}`: baseline, reliability, attribution, candidate deltas, regression diagnosis, gate, cost, duration, hashes, and reproduction command.

## Known limits

Counterfactual traces can create states that a real agent could not produce, especially when a tool name changes but its response does not. The loop therefore restricts combinations, validates trace shape, exposes invalid interventions, and rejects insufficient evidence. Exact metrics are deterministic; LLM judges require repeated sampling before a case can be trusted. Windows absolute evalset paths are converted to relative paths because the current SDK interprets a drive-letter colon as a case selector.
