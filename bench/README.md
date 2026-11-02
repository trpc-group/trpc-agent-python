# 压测

- [使用文档](../docs/mkdocs/zh/bench.md)
- [设计文档](../docs/design/01-bench-design.md)

当前压测目标：`LlmAgent`。

## LlmAgent

### 直接测试

在 `bench/` 目录下执行：

```bash
python -m trpc_agent_bench.run_bench \
  --targets targets.llmagent \
  --benchmarks turn1,turn5,turn10,loop1,loop5,loop10 \
  --iterations 10 \
  --results-dir results/direct
```

环境：16 vCPU，Python 3.12，mock 无额外延迟，`--iterations 10`。

| benchmark | p50 | p95 | memory |
|---|---|---|---|
| `turn1` | 25.79 ms | 26.35 ms | 341.4 KiB |
| `turn5` | 133.32 ms | 145.81 ms | 462.6 KiB |
| `turn10` | 291.23 ms | 319.68 ms | 664.9 KiB |
| `loop1` | 27.79 ms | 30.72 ms | 357.5 KiB |
| `loop5` | 138.92 ms | 147.69 ms | 452.6 KiB |
| `loop10` | 300.37 ms | 314.15 ms | 572.3 KiB |

### 压力测试

```bash
python -m trpc_agent_bench.run_bench \
  --targets targets.llmagent \
  --benchmarks turn1,turn5,turn10,loop1,loop5,loop10 \
  --concurrency 1,8,16,32 \
  --iterations 10 \
  --results-dir results/stress
```

环境：16 vCPU，Python 3.12，mock 无额外延迟，`--iterations 10`。

| benchmark | c1 p50 | c8 p50 | c16 p50 | c32 p50 |
|---|---|---|---|---|
| `turn1` | 27.52 ms | 196.51 ms | 400.87 ms | 772.52 ms |
| `turn5` | 134.43 ms | 1079.48 ms | 2124.66 ms | 4500.42 ms |
| `turn10` | 294.16 ms | 2317.78 ms | 4738.24 ms | 9519.07 ms |
| `loop1` | 27.61 ms | 208.85 ms | 414.94 ms | 818.57 ms |
| `loop5` | 137.59 ms | 1078.72 ms | 2115.57 ms | 4444.14 ms |
| `loop10` | 301.68 ms | 2288.40 ms | 4575.68 ms | 9370.65 ms |

直接测试和压力测试是两次独立运行，两张表的 c1 数字会有少量抖动，但 benchmark 行和口径保持一致。

### 结论

- `turnN` 的耗时随 user turn 数近似线性增长：`turn5` 是 `turn1` 的 5.17 倍，`turn10` 是 11.29 倍；session history 变长后单轮成本略有上升。
- `loopN` 的耗时随 LLM 调用数近似线性增长：`loop5` 是 `loop1` 的 5.00 倍，`loop10` 是 10.81 倍。`loopN` 的 `N-1` 次 tool round 没有带来明显的额外放大，主要成本还是一次 LLM round trip。
- 同样的 5 次 / 10 次 LLM 调用下，`loop5` 比 `turn5` 高约 4.2%，`loop10` 比 `turn10` 高约 3.1%：多轮 session 和一轮 Agent 执行的总成本接近，两种形态可以互相作为对照。
- 内存随会话/调用次数增长：`turn1` 约 341 KiB，`turn10` 约 665 KiB；`loop10` 约 572 KiB。
- 压力测试下，`turn1` 从 c1 的 27.52 ms 增长到 c32 的 772.52 ms；`turn10` 从 294.16 ms 增长到 9519.07 ms，c32 约为 c1 的 30 倍。耗时随并发数近似线性增长，说明当前瓶颈在单进程 mock server：c8/c16/c32 的请求会在 mock 侧排队，这组数据反映的是「框架 + 单 mock server」的整体吞吐，不是纯框架的并行扩展性。
- 并发下 turn / loop 的相对关系保持一致：`turn5` 和 `loop5`、`turn10` 和 `loop10` 的耗时都很接近，说明多轮 session 与一轮 Agent 执行的总成本仍然主要由 LLM 调用次数决定。
