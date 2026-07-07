# Design note

This example runs a closed loop:

1. Convert train and validation references into trace evalsets.
2. Evaluate baseline traces with `AgentEvaluator`.
3. Attribute failures by response mismatch, tool call error, argument error, rubric failure, knowledge recall gap, and format noncompliance.
4. Optimize `TargetPrompt` candidates.
5. Regenerate candidate traces from the candidate prompt text.
6. Compare baseline and candidate per case, apply the configured gate, and write audit artifacts.

The default backend is `fake`, so the example stays runnable without network access or API keys. The fake optimizer appends deterministic prompt rules such as `STRICT_JSON_OUTPUT`, `STRICT_TOOL_ARGUMENTS`, and `TRAINING_PATTERN_BIAS`.

The optional `agent_optimizer` backend calls `AgentOptimizer.optimize(...)` with the same `TargetPrompt` files. It uses response-only metrics during GEPA black-box optimization, then reruns the full trace-mode report metrics afterward, including tool trajectory. Secrets are read from environment variables only and are not written to checked-in config or reports.
