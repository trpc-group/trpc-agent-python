# Evaluation + Optimization Loop Example

This example demonstrates an auditable evaluation and prompt-optimization loop.
It is intentionally offline-first: `fake` and `trace` modes need no API key.

## Modes

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode online
```

`fake` mode evaluates deterministic fixture outputs for baseline and three
candidates through `AgentEvaluator`. It should select `candidate_local_patch`,
reject a no-op candidate, and reject `candidate_overfit`, which improves
training while regressing a critical validation case.

`trace` mode materializes recorded conversations for baseline and every
candidate, then runs the same `AgentEvaluator` summary path with
`eval_mode="trace"`. It reads `fixtures/trace_outputs.json`, independently of
the fake-mode fixture, and proves the replay path works without model inference.

`online` mode uses `AgentOptimizer.optimize(...)`, `TargetPrompt`, and
`optimizer.json`. It first prints only whether required environment variables
are present. It requires:

```bash
TRPC_AGENT_API_KEY
TRPC_AGENT_BASE_URL
TRPC_AGENT_MODEL_NAME
```

The online smoke path is opt-in because it performs real optimizer and
revalidation calls. The default optimizer config is bounded for this example,
but real-provider latency is not held to the fake/trace three-minute
deterministic expectation. A native optimizer `SUCCEEDED` status is recorded as
an artifact only; the product decision is always the report's
`gate_decision.accepted`.

## Outputs

Each run writes to `runs/<run_id>/` by default:

- `optimization_report.json`: issue-facing machine-readable summary.
- `optimization_report.md`: concise human-readable report.
- `trace_evalset.json` and `trace_metrics.json`: trace-mode replay inputs.
- `online/result.json`, `online/summary.txt`, `online/rounds/`,
  `online/baseline_prompts/`, `online/best_prompts/`, and
  `online/config.snapshot.json`: native optimizer artifacts in online mode.

The top-level report always includes `run_id`, `mode`, `seed`, `baseline`,
`candidates`, `delta`, `gate_decision`, `failure_attribution`, `cost`,
`duration_seconds`, `config_snapshot`, `environment_snapshot`, and `artifacts`.
`optimization_report.schema.json` is the contract used by the CLI before it
writes `optimization_report.json`; schema validation failures stop report
generation instead of producing a partial artifact.

In online mode, the duration gate uses elapsed pipeline time through
optimization, baseline revalidation, and candidate revalidation. The separate
`online_duration` fields retain those phase durations, while each candidate's
audit retains its own revalidation duration. Optimizer `model_calls` includes
candidate-evaluation agent calls, reflection calls, and judge calls. Optimizer
judge calls are derived from counted candidate evaluations and every configured
judge model sample. `judge_calls_per_candidate_evaluation` records that
multiplier; a nonzero native counter is reconciled with the derived count
without double counting, and `judge_model_call_source` records the source.
Final revalidation records the corresponding `judge_calls_per_agent_call` and
counts every conversation turn, not only top-level cases. Because
`AgentOptimizer` does not expose token or cost usage for candidate-evaluation
or judge calls, optimizer phase totals are `null` and marked unknown whenever
those calls occur. `optimizer.reflection_reported_usage` preserves the native
reflection-only cost and token counters under an explicit scope. Final
revalidation token usage is likewise `null` when `AgentEvaluator` cannot expose
it. Top-level `model_calls` is the optimizer phase plus final revalidation.

A compact sample output is checked in at `fixtures/optimization_report.sample.json`.

## CLI Inputs

```bash
python examples/optimization/eval_optimize_loop/run_pipeline.py \
  --mode fake \
  --train-evalset train.evalset.json \
  --optimizer-dev-evalset optimizer_dev.evalset.json \
  --val-evalset val.evalset.json \
  --optimizer-config optimizer.json \
  --gate-config gate.json \
  --output-dir runs \
  --run-id demo
```

All path arguments have defaults pointing at this example. `--system-prompt`
and `--router-prompt` are used by online mode and are still recorded in offline
configuration snapshots. Gate config may override validation delta, hard-fail,
critical-regression, cost, duration, and required-metric checks. By default the
gate inherits `optimize.stop.required_metrics` from `optimizer.json`.
Gate booleans require JSON booleans, numeric thresholds require finite
non-negative JSON numbers, and malformed values reject without raising. The
gate accepts only the documented field names; misspelled or unknown keys fail
closed instead of falling back to a weaker default. The
three evalset roles must be different files with no byte-identical content and
no overlap in case IDs, normalized user inputs, or canonical gold outputs.
Overlap checks cover every conversation turn and canonicalize valid JSON by
structure, so whitespace and key ordering cannot hide shared gold data.

The deterministic metric in this example is `route_tool_args_score`: it parses
the final JSON response and scores only `route`, `tool.name`, and
`tool.arguments`. Reason wording and safety are handled by the rubric metric, so
a harmless explanation rewrite does not zero out an otherwise correct route.

`environment_snapshot` records the git commit, dirty flag, Python version, SDK
version when installed, model name, redacted base URL host, seed, command, and
optimizer config path. It never records API keys. When a judge provider does
not support native JSON schemas, the judge uses JSON-object mode and validates
the response locally instead of sending an ignored schema.
Provider and runtime warnings are not globally suppressed. Judge calls run
non-streaming and explicitly close their agent run so normal online evaluation
does not leave OpenAI/httpx stream-shutdown diagnostics behind.
The pipeline awaits `Runner.close()` for its agent execution paths.
If an upstream OpenAI/httpx diagnostic still occurs outside this lifecycle,
warnings remain observable rather than being filtered.

Report error text redacts provider URLs, configured provider credentials,
standalone provider-key shapes, and semantic credential markers while retaining
ordinary error context. Run IDs, candidate IDs, and optimizer prompt artifact
names also reject or normalize Windows reserved device basenames on every
platform. Before each write, report, metrics, trace, prompt, and optimizer
output paths are resolved beneath the run directory; pre-existing symlink
redirection is rejected.

Online mode writes an exact run-local `optimizer.json`, overrides its algorithm
seed from `--seed`, and passes that file to `AgentOptimizer`. Candidate and
environment audits reference the same path and SHA-256. `config_snapshot` also
stores a normalized, credential-free evaluation configuration and its hash;
report validation derives optimizer and final judge-call multipliers from that
snapshot rather than trusting the reported counters. Evaluation-metric and
evalset manifests bind that snapshot to file hashes, case counts, and
conversation-turn counts. A prompt-target manifest binds the registered target
names to source paths and SHA-256 values. Validation authenticates those source
files, recomputes every candidate diff from embedded baseline content, and
rejects unknown optimizer targets. Prompt artifacts and optimizer rounds embed
content so their reported SHA-256 values remain independently recomputable even
when the original run directory is unavailable.

`optimizer_dev.evalset.json` is the optimizer-internal holdout passed to
`AgentOptimizer.optimize(..., validation_dataset_path=...)`. `val.evalset.json`
is the final validation set and is only used for baseline scoring and final
candidate gate scoring.

## Design Notes

本示例把评测、归因、候选生成、验证回归和产品 gate 组织成一个可复现闭环。fake 与 trace 模式只替换 agent 输出来源，分数、逐 case pass/fail 和 metric 明细仍由 AgentEvaluator 生成，因此 CI 不依赖 API，也不会用 fixture 直接冒充分数。online 模式调用 AgentOptimizer 和 TargetPrompt，optimizer_dev 只服务优化器，val 仅参与 baseline 与最终候选复评，避免验证集答案进入 prompt 搜索。

报告先写 JSON，再渲染 Markdown。每个候选保存 prompt 摘要与哈希、训练和验证结果、逐 case 变化、失败原因、gate 检查、成本和耗时。gate 要求验证分数严格超过阈值，且不得新增 hard fail、关键 case 退化、必需 metric 失败或预算越界；成本未知且配置了成本上限时按失败处理。候选只写入运行目录，原始 prompt 不会被覆盖，随机种子、配置哈希和环境快照用于复现实验。

## Verification

```bash
pytest tests/evaluation/test_eval_optimize_loop_example.py -q
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode fake
python examples/optimization/eval_optimize_loop/run_pipeline.py --mode trace
```

Full repository pytest includes optional integration suites. In an environment
without optional extras such as Cube/E2B, Mempalace, A2A, AG-UI, Claude Agent
SDK, or OpenClaw dependencies, pytest may fail during collection before any
tests run. Those dependency errors must be reported as an environment boundary;
this example does not install a global collection hook to hide them.
