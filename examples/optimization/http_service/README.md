# HTTP Service — 接入线上 HTTP agent 服务做 prompt 优化

> **适用场景**：业务 agent 已经作为独立 HTTP 服务在线运行（FastAPI / Gin / 自研框架均可），希望对其 prompt 做自动优化，但不想停服、不想改服务代码。本 example 演示通过 `httpx` 把 `call_agent` 接到运行中的服务，prompt 通过磁盘文件热加载。阅读前请先熟悉 `quickstart/README.md` 中的 `AgentOptimizer`、`TargetPrompt`、`call_agent` 等基础概念。

## 1 · 适用问题与设计目标

线上 agent 服务的特点：

- 服务进程长期运行，重启代价高
- 服务实现细节（模型、tools、内部链路）对优化器是黑盒
- prompt 通常以文件或配置中心形式注入，与服务代码解耦

`AgentOptimizer` 在该场景下扮演纯客户端角色：通过 HTTP 把测试 query 发给服务、收集 final 文本、按 metric 评分。优化器与服务进程间的唯一耦合点是 **prompt 文件**——优化器写入新候选，服务在下一次请求时重读该文件。

| 输入 | 输出 |
| --- | --- |
| 一个支持 prompt 热加载的 HTTP agent 服务（双 endpoint：`GET /health` + `POST /chat`） | 满足 metric 阈值的最优 prompt 候选 |
| HTTP 服务对 prompt 文件的读写权限 | 服务代码与服务进程**完全不变**，仅磁盘上 prompt 文件被改写 |

### 本 example 演示的最小用例

| 维度 | 值 |
| --- | --- |
| 业务任务 | 算术应用题求解（与 quickstart 同一类任务，便于横向对比 HTTP 接入与本地接入的差异） |
| HTTP 服务 | `service/server.py` 中的 FastAPI app，监听 `127.0.0.1:8767` |
| 优化目标 | `service/prompts/system.md` 单文件 |
| 验证指标 | `final_response_avg_score`（contains 匹配，阈值 1.0） |
| 训练 / 验证规模 | 5 条 / 3 条 |

## 2 · 术语对照

仅列出本 example 引入的新概念。基础术语见 `quickstart/README.md` §2。

| 术语 | 含义 |
| --- | --- |
| **prompt 热加载** | 服务进程在每次请求处理前重新读取 prompt 文件，使外部对该文件的写入立即生效。本 example 的 `_build_agent()` 在每次 `/chat` 都重读 `system.md` 实现该语义。 |
| **call_agent 内 client 即用即关** | `call_agent` 用 `async with httpx.AsyncClient()` 创建并退出时自动关闭。`httpx.AsyncClient` 的连接池绑定到首次使用所在的事件循环（参考 [httpx Discussion #2959](https://github.com/encode/httpx/discussions/2959)），不支持跨循环复用。 |
| **健康检查（pre-flight）** | 优化开始前同步探测 `GET /health`，服务不通时 fail-fast 而非浪费 LLM 配额跑到一半才报错。 |

## 3 · 运行示例

### 3.1 安装依赖

```bash
pip install -e ".[optimize]"
pip install fastapi uvicorn httpx
```

`fastapi` / `uvicorn` 用于 mock 线上服务；`httpx` 用于优化器作为客户端访问该服务。

### 3.2 配置环境变量

```bash
export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
```

服务进程与优化器进程共用同一组凭据。

### 3.3 启动（双终端）

**终端 A** —— 启动 mock 服务并保持运行：

```bash
python examples/optimization/http_service/service/server.py
```

预期日志：`Uvicorn running on http://127.0.0.1:8767`。

**终端 B** —— 启动优化器：

```bash
python examples/optimization/http_service/run_optimization.py
```

启动时优化器会先做一次同步健康检查，服务不通直接报错并提示先启动 server。

### 3.4 产物结构

```
runs/<timestamp>/
├── result.json           完整运行记录
├── summary.txt           人类可读摘要
├── baseline_prompts/     运行前 prompt 快照
├── best_prompts/         val 集得分最高的候选
└── rounds/               每轮反思与评估明细
```

## 4 · 架构与数据流

```
[终端 A: HTTP 服务]
    │
    └── FastAPI :8767
        ├── GET  /health  → {"status":"ok"}
        └── POST /chat    → 每次都重读 service/prompts/system.md，
                            构造 LlmAgent，跑 Runner.run_async，
                            返回 {"final_text": "..."}

[终端 B: 优化器]
    │
    ├── pre-flight: GET /health
    │
    ├── TargetPrompt.add_path("system_prompt", service/prompts/system.md)
    │       │  GEPA 每轮把候选 prompt 写入磁盘
    │       ▼
    │   service/prompts/system.md
    │       │  HTTP 服务下一次请求时重读该文件
    │       ▼
    ├── call_agent(query):
    │       └── async with httpx.AsyncClient() as client:
    │              POST /chat → final_text
    │
    └── AgentOptimizer.optimize → runs/<timestamp>/
```

### 4.1 文件清单

| 文件 | 角色 | 接入自有业务时的修改方向 |
| --- | --- | --- |
| `run_optimization.py` | 优化器入口（客户端） | 修改 `SERVICE_BASE_URL` / `CHAT_URL`；调整 `call_agent` 中的请求 / 响应 schema |
| `service/server.py` | mock 线上 HTTP 服务 | 真实业务下删除该文件，由实际服务承担相同角色 |
| `service/prompts/system.md` | 服务读取的 prompt（GEPA 写入目标） | 替换为业务 baseline；路径需与服务进程的读取路径一致 |
| `optimizer.json` | 算法 + metric 配置 | 调整 metric 与停止条件 |
| `train.evalset.json` | 反思 minibatch 来源 | 替换为业务训练用例 |
| `val.evalset.json` | 候选评分依据 | 替换为业务验证用例 |

### 4.2 prompt 热加载是核心约束

服务必须在**每次请求时重读 prompt 文件**，否则优化器写入的新候选不会被服务感知，整个反思循环失效。

`service/server.py` 通过在每次 `/chat` 中调用 `_build_agent()`（其内部 `_read_system_prompt()` 重读磁盘）实现该语义。LlmAgent 构建本身不涉及 LLM 调用，单次开销可忽略。

## 5 · 关键配置

`optimizer.json` 中本 example 与 quickstart 的差异点：

```jsonc
{
  "optimize": {
    "algorithm": {
      "seed": 42,
      "score_threshold": 1.0,            // 主停止条件：val pass_rate ≥ 1.0 立即停止
      "max_metric_calls": 40,
      "max_iterations_without_improvement": 5
    }
  }
}
```

| 字段 | 影响 |
| --- | --- |
| `score_threshold` | 算法层早停阈值。本 example 设为 1.0（要求 val 全 case 通过），追求快速收敛 |
| `seed` | 控制 GEPA 内部抽样的随机性。固定 seed 配合相同输入应得相同结果 |
| `REQUEST_TIMEOUT=120.0`（在 `run_optimization.py`） | 单次 HTTP 请求超时。首次请求需经历 FastAPI 冷启动 + LLM 推理，需要充足时间 |

## 6 · 运行控制

### 6.1 优雅停止

```bash
touch runs/<timestamp>/optimize.stop
```

下一次 stopper 检查时框架立即收尾，`OptimizeResult.stop_reason=user_requested_stop`。

### 6.2 调试 GEPA 内部行为

`run_optimization.py` 中 `verbose=1` 改为 `verbose=2`，会附加 `trpc_agent_sdk.optimizer.gepa` logger 的诊断输出。

## 7 · 常见问题

**Q：服务与优化器必须在同一台机器吗？**
A：不必。`SERVICE_BASE_URL` 改成远端地址即可。但 `TargetPrompt.add_path` 操作的是优化器进程本地的文件系统——若服务在远端，要么挂载相同存储卷使两端看到同一份 `system.md`，要么改用 `add_callback` 直连配置中心（参见 `remote_prompt_store/` example）。

**Q：服务首次请求很慢？**
A：FastAPI 进程冷启动 + 首次 LLM 调用确实较慢。`REQUEST_TIMEOUT=120s` 已留出充分缓冲。

**Q：端口 `8767` 被占用？**
A：同时修改 `service/server.py` 的 `PORT` 与 `run_optimization.py` 的 `SERVICE_BASE_URL`。

**Q：`call_agent` 抛 HTTP 错误会怎样？**
A：异常会传播到优化器，导致当前 case 评测失败、当前候选可能被拒绝。建议在 `call_agent` 内部加上重试逻辑（如 `httpx.HTTPStatusError` 触发 1–2 次重试）以应对临时性故障。

## 8 · 接入自有 HTTP 服务的步骤

1. **确认服务支持 prompt 热加载**：服务在每次请求处理前重读 prompt 文件（或重新拉配置）
2. **修改优化器入口**：
   - `SERVICE_BASE_URL` 改为实际服务地址
   - `call_agent` 内部的请求 payload / 响应字段名按服务实际 schema 调整
   - `SYSTEM_PROMPT_PATH` 指向服务进程实际读取的 prompt 文件
3. **替换数据集**：`train.evalset.json` / `val.evalset.json` 写入业务用例
4. **调整 metric**：`optimizer.json` 中 `evaluate.metrics` 选择合适的 metric 类型
5. **运行**：先启动服务，再启动优化器；根据 `summary.txt` 决定后续调参

若服务的 prompt 不在本地文件而在配置中心，参见 `remote_prompt_store/` example，仅需将 `add_path` 替换为 `add_callback`。
