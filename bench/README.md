# 压测

- [使用文档](../docs/mkdocs/zh/bench.md)
- [设计文档](../docs/design/01-bench-design.md)

当前压测目标：`LlmAgent`。

## LlmAgent

### Base

#### 直接测试

在 `bench/` 目录下执行：

```bash
python -m trpc_agent_bench.run_bench \
  --targets llmagent.base \
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

#### 压力测试

```bash
python -m trpc_agent_bench.run_bench \
  --targets llmagent.base \
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

#### 结论

- `turnN` 的耗时随 user turn 数近似线性增长：`turn5` 是 `turn1` 的 5.17 倍，`turn10` 是 11.29 倍；session history 变长后单轮成本略有上升。
- `loopN` 的耗时随 LLM 调用数近似线性增长：`loop5` 是 `loop1` 的 5.00 倍，`loop10` 是 10.81 倍。`loopN` 的 `N-1` 次 tool round 没有带来明显的额外放大，主要成本还是一次 LLM round trip。
- 同样的 5 次 / 10 次 LLM 调用下，`loop5` 比 `turn5` 高约 4.2%，`loop10` 比 `turn10` 高约 3.1%：多轮 session 和一轮 Agent 执行的总成本接近，两种形态可以互相作为对照。
- 内存随会话/调用次数增长：`turn1` 约 341 KiB，`turn10` 约 665 KiB；`loop10` 约 572 KiB。
- 压力测试下，`turn1` 从 c1 的 27.52 ms 增长到 c32 的 772.52 ms；`turn10` 从 294.16 ms 增长到 9519.07 ms，c32 约为 c1 的 30 倍。耗时随并发数近似线性增长，说明当前瓶颈在单进程 mock server：c8/c16/c32 的请求会在 mock 侧排队，这组数据反映的是「框架 + 单 mock server」的整体吞吐，不是纯框架的并行扩展性。
- 并发下 turn / loop 的相对关系保持一致：`turn5` 和 `loop5`、`turn10` 和 `loop10` 的耗时都很接近，说明多轮 session 与一轮 Agent 执行的总成本仍然主要由 LLM 调用次数决定。

### UnixSocket

多worker场景下，将会在 Agent 执行主链路中通过 unix-socket 来获取跨进程的信息（比如：Cancel信号或Steering插话），该压测主要测试在通过 `cancel.init_unix_socket` 开启 unix-socket下的性能。每个 run 会启动一个 per-run poller，按 `poll_interval` 从 master 同步 cancel / steering 到本地；Agent checkpoint 只读本地状态，不在主链路上发 RPC。

#### 直接测试

在 `bench/` 目录下执行：

```bash
python -m trpc_agent_bench.run_bench \
  --targets llmagent.multi_worker \
  --benchmarks turn1,turn5,turn10,loop1,loop5,loop10 \
  --iterations 10 \
  --results-dir results/unix_socket_direct
```

环境：16 vCPU，Python 3.12.13，mock 无额外延迟，`--iterations 10`。

| benchmark | p50 | p95 | memory |
|---|---|---|---|
| `turn1` | 27.87 ms | 30.41 ms | 358.2 KiB |
| `turn5` | 150.01 ms | 158.20 ms | 492.7 KiB |
| `turn10` | 324.85 ms | 349.60 ms | 629.7 KiB |
| `loop1` | 28.78 ms | 29.88 ms | 376.7 KiB |
| `loop5` | 145.35 ms | 152.30 ms | 491.9 KiB |
| `loop10` | 303.73 ms | 368.66 ms | 589.6 KiB |

#### 压力测试

在 `bench/` 目录下执行：

```bash
python -m trpc_agent_bench.run_bench \
  --targets llmagent.multi_worker \
  --benchmarks turn1,turn5,turn10,loop1,loop5,loop10 \
  --concurrency 1,8,16,32 \
  --iterations 10 \
  --results-dir results/unix_socket_stress
```

环境：16 vCPU，Python 3.12.13，mock 无额外延迟，`--iterations 10`。

| benchmark | c1 p50 | c8 p50 | c16 p50 | c32 p50 |
|---|---|---|---|---|
| `turn1` | 28.15 ms | 208.67 ms | 402.06 ms | 801.52 ms |
| `turn5` | 146.11 ms | 1106.12 ms | 2188.88 ms | 4608.47 ms |
| `turn10` | 307.90 ms | 2337.18 ms | 4933.20 ms | 9962.37 ms |
| `loop1` | 28.91 ms | 220.01 ms | 420.22 ms | 854.39 ms |
| `loop5` | 144.34 ms | 1080.68 ms | 2119.73 ms | 4536.00 ms |
| `loop10` | 297.58 ms | 2291.13 ms | 4541.92 ms | 9469.80 ms |

#### 结论

- Unix Socket 模式下 `turnN` / `loopN` 仍近似线性增长：`turn5` 是 `turn1` 的 5.38 倍，`turn10` 是 11.66 倍；`loop5` 是 `loop1` 的 5.05 倍，`loop10` 是 10.55 倍。
- 与 Base 表对照（同机、同 Python 3.12，两次独立运行）：direct 下 `turn1` / `turn5` / `turn10` 的 p50 分别高 2.08 ms（8.1%）/ 16.69 ms（12.5%）/ 33.62 ms（11.5%），`loop1` / `loop5` / `loop10` 分别高 0.99 ms（3.6%）/ 6.43 ms（4.6%）/ 3.36 ms（1.1%）。`turnN` 每个 turn 都会新起一个 run，固定成本来自 connect + register/unregister + close；`loopN` 只有一个 run，checkpoint 只读本地状态，成本被摊薄，`loop10` 已经接近 Base。stress 的 c1 也在同一范围（`turn1` +2.3%，`loop10` -1.4%），差值包含两次运行的抖动。
- 每个 run 在 `register_run()` 后启动一个 poller，按 `poll_interval` 发一次 `sync`；Agent checkpoint 上的 `ctx.raise_if_cancelled()` / `consume_steering()` 不再发 RPC。跨 worker 的 cancel / steering 延迟上限是一个 `poll_interval`，本进程内发送是即时的。
- Unix Socket 压力测试下，c8 / c16 / c32 相对 c1 分别约为 7.4~7.7 倍、14.3~16.0 倍、28.5~32.3 倍，和 Base 一样随并发近似线性增长。c32 时 `turn10` 为 9962.37 ms，Base 是 9519.07 ms，高并发下控制面开销相对单进程 mock server 的排队时间占比很小。
- Unix Socket 的 c1 内存与 Base 同量级（`turn1` 357.9 KiB 对 341.4 KiB，`loop10` 588.6 KiB 对 572.3 KiB）；每个 run 的连接和 poller 在 run 结束后释放。并发下内存随活跃 session 数增长，`turn10` c32 约 13.8 MiB，`turn1` c32 约 3.3 MiB。
