# Evaluation + Optimization Loop

本示例把 `AgentEvaluator`、`AgentOptimizer` 和 `TargetPrompt` 组合成可审计的
baseline → optimize → candidate → gate 闭环。默认 `fake-model` 不需要 API Key，
运行时间通常小于 30 秒；`real` 模式读取 `TRPC_AGENT_API_KEY`、
`TRPC_AGENT_BASE_URL` 和 `TRPC_AGENT_MODEL_NAME`。`trace` 模式读取预录制的
baseline/candidate evalset，适合离线回归。

```bash
uv run python examples/optimization/eval_optimize_loop/run_pipeline.py
```

输出目录包含 `optimization_report.json`、`optimization_report.md` 和临时工作副本。
只有 `real` 模式显式传入 `--write-back` 且 gate 接受时才会更新 prompt 源文件；
fake/trace 模式会拒绝回写，避免合成候选污染源文件。
