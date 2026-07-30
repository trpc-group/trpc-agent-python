# Remote Prompt Store — 接入远端配置中心做 prompt 优化

> **适用场景**：业务 prompt 不在本地文件，而由 ops 配在远端配置中心（七彩石 / Apollo / Nacos / 自研 KV / 数据库），业务服务从中心拉取使用。本 example 演示通过 `TargetPrompt.add_callback` 将优化器对接远端读写接口，并通过 production / sandbox 双 namespace 隔离生产数据。阅读前请先熟悉 `quickstart/README.md` 与 `http_service/README.md`。

## 1 · 适用问题与设计目标

远端 prompt 场景与本地文件场景的关键差异：

- 优化器无法直接读写本地文件——必须通过用户提供的 async 函数操作远端
- 生产 prompt 通常承担线上流量，未经审批的写入意味着合规风险
- 不同环境（生产 / 沙箱 / 灰度）的 prompt 通常已经存在 namespace 隔离机制

本 example 的设计原则：

- **优化器只读写沙箱 namespace**，生产 namespace 全程不被触碰
- **`update_source=False` 强制约束**：跑完后沙箱自动回滚到 baseline，候选只输出到本地 `runs/<timestamp>/best_prompts/`，由人工审批后另行同步到生产
- **配置中心实现透明**：用户提供两个 async 函数（`read` / `write`），优化器对 KV 后端形态完全黑盒

| 输入 | 输出 |
| --- | --- |
| 一对 async 函数：`async read() -> str` 与 `async write(value: str) -> None` | 沙箱 namespace 中的最优 prompt 候选副本（runs/best_prompts/） |
| 沙箱 namespace 的写入权限 | 生产 namespace 不变；沙箱在收尾时自动回滚到 baseline |

### 本 example 演示的最小用例

| 维度 | 值 |
| --- | --- |
| 业务任务 | 算术应用题求解（与 quickstart 同一类任务） |
| 远端 KV 模拟 | `store/fake_kv_store.py` 用本地 JSON 文件持久化的字典 |
| 优化目标 | `system_prompt` 字段，存储于 `system_prompt:sandbox` 这个 KV key |
| 验证指标 | `final_response_avg_score`（contains 匹配） |
| 训练 / 验证规模 | 5 条 / 3 条 |

## 2 · 术语对照

仅列出本 example 引入的新概念。基础术语见 `quickstart/README.md` §2，`call_agent` async 资源约束见 `http_service/README.md` §2。

| 术语 | 含义 |
| --- | --- |
| **TargetPrompt.add_callback(name, read=, write=)** | 注册一个由用户函数驱动的 prompt 字段。`read` / `write` 必须是 async 函数；`read` 无参数返回 prompt 文本，`write` 接收新文本并写入。优化器在评测前调 `read`、产生新候选时调 `write`。 |
| **生产 / 沙箱 namespace** | 配置中心常见的环境隔离形态。本 example 用两个固定 KV key 模拟：`system_prompt:production`（线上读取）与 `system_prompt:sandbox`（优化器写入）。 |
| **自动回滚** | `update_source=False` 时优化器在 `finally` 阶段调用 `write` 把字段还原为运行开始时通过 `read` 获取的 baseline 快照，避免沙箱被遗留的候选污染。 |

## 3 · 运行示例

### 3.1 安装依赖

```bash
pip install -e ".[optimize]"
```

### 3.2 配置环境变量

```bash
export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
```

### 3.3 启动

```bash
python examples/optimization/remote_prompt_store/run_optimization.py
```

启动时脚本会先调 `reset_store(...)` 把 production / sandbox 都初始化为 baseline。**这一步仅用于演示**——真实业务中生产 namespace 已由 ops 维护，无需重置。

### 3.4 产物结构

```
runs/<timestamp>/
├── result.json
├── summary.txt
├── baseline_prompts/         运行前从 KV 读取的 baseline 快照
├── best_prompts/             val 集得分最高的候选（待人工审批）
└── rounds/

store/store.json              KV 持久化文件（演示用）
                              收尾时 sandbox key 已被回滚到 baseline
                              production key 全程未变
```

## 4 · 架构与数据流

```
[配置中心 KV]
    ├── "system_prompt:production"   ← 线上服务读这里（永远不被优化器触碰）
    └── "system_prompt:sandbox"      ← 优化器读 / 写这里

[run_optimization.py]
    │
    ├── reset_store(BASELINE_PROMPT)            演示前置：production = sandbox = baseline
    │                                           （真实业务跳过此步）
    │
    ├── TargetPrompt.add_callback(
    │       "system_prompt",
    │       read=read_sandbox_prompt,           async () -> str    读 sandbox key
    │       write=write_sandbox_prompt,         async (str) -> None  写 sandbox key
    │   )
    │
    ├── call_agent(query):
    │       prompt_text = await read_sandbox_prompt()    # 现读现用
    │       agent = create_agent(prompt_text)            # 即时构造
    │       return await runner.run_async(...)            # 跑一次推理
    │
    └── AgentOptimizer.optimize(update_source=False, ...)
        ├── 每轮把候选 prompt 写入 sandbox key
        ├── 收尾：sandbox key 自动回滚到 baseline 快照
        └── best_prompts/ 落本地，待人工审批
```

### 4.1 文件清单

| 文件 | 角色 | 接入自有业务时的修改方向 |
| --- | --- | --- |
| `run_optimization.py` | 优化器入口，注册 callback | `reset_store(...)` 改为 ops 真实初始化（或直接删除）；其余基本不变 |
| `agent/agent.py` | LlmAgent 工厂，prompt 通过参数注入 | 替换为业务 agent 构建逻辑 |
| `store/prompt_client.py` | async `read` / `write` 函数定义 | **核心改造点**：把内部实现替换为业务配置中心 SDK 调用，函数签名保持不变 |
| `store/fake_kv_store.py` | 本地 JSON 文件模拟 KV | 真实业务下整体删除 |
| `optimizer.json` | 算法 + metric 配置 | 与 quickstart 一致 |
| `train.evalset.json` / `val.evalset.json` | 数据集 | 替换为业务用例 |

### 4.2 与 `http_service/` 的对照

唯一差异在 `TargetPrompt` 的注册方式：

```python
# http_service：prompt 在本地文件
target = TargetPrompt().add_path("system_prompt", "service/prompts/system.md")

# remote_prompt_store：prompt 在远端 KV
target = TargetPrompt().add_callback(
    "system_prompt",
    read=read_sandbox_prompt,
    write=write_sandbox_prompt,
)
```

`optimizer.json`、`call_agent` 的整体结构、metric 定义、产物 layout 均保持一致。

## 5 · 关键配置

### 5.1 `update_source` 的强制约束

远端 prompt 场景下**强烈建议始终保持 `update_source=False`**。理由：

- 远端配置通常承担线上流量，自动写回意味着未审批变更直接进生产
- 即便沙箱 namespace 也有联调 / 灰度等隐式约束，应避免让框架替业务做"提交"决策
- `update_source=False` 时优化器收尾会把沙箱回滚到 baseline，唯一遗留物是本地 `best_prompts/`，由人工或审批工具决定后续动作

### 5.2 `read` / `write` 的实现约束

| 约束 | 说明 |
| --- | --- |
| 签名必须是 async | `read: async () -> str`；`write: async (str) -> None` |
| `read` 异常处理 | 优化器启动期会调一次 `read` 获取 baseline 快照。该次调用抛错会让 `optimize()` 直接 fail-fast，异常透传给调用方。运行中 `read` 抛错会导致当前 case 评测失败 |
| `write` 幂等性 | 优化器收尾时会再次调 `write` 把沙箱回滚到 baseline；若 `write` 不幂等或无事务保护，回滚可能失败。建议实现支持重复调用同一 value |
| 重试 | 配置中心 SDK 通常有内置重试；本 example 的 `read` / `write` 不额外封装重试，业务方按需自行加上 |

## 6 · 接入真实配置中心

将 `store/prompt_client.py` 内部实现替换为业务 SDK 调用，**保持函数签名不变**：

```python
# store/prompt_client.py 替换示例
async def read_sandbox_prompt() -> str:
    return await your_config_sdk.get(
        namespace="sandbox",
        key="system_prompt",
    )

async def write_sandbox_prompt(value: str) -> None:
    await your_config_sdk.put(
        namespace="sandbox",
        key="system_prompt",
        value=value,
    )
```

`run_optimization.py` 中 `TargetPrompt.add_callback(...)` 调用与其他配置无需修改。

`fake_kv_store.py` 在真实接入后可整体删除。

## 7 · 常见问题

**Q：业务服务在另一个进程，优化器写入沙箱后服务能感知吗？**
A：取决于业务服务的 prompt 加载策略。**业务服务必须在每次请求时重新拉配置**（即"热加载"），否则优化器的写入对服务不可见、反思循环失效。这是与 `http_service/` example 完全相同的约束，只是介质从本地文件换成了远端 KV。

**Q：`reset_store(BASELINE_PROMPT)` 在生产环境也要调吗？**
A：不要。该调用仅用于演示首次接入时把 KV 初始化到已知状态。真实业务的生产 namespace 已由 ops 维护，优化器**只关心读 / 写沙箱**。

**Q：`read` 一次返回的内容会被缓存吗？**
A：不会。优化器在每次评测候选前都重新调 `read`，因此沙箱被写入新值后下一次 `call_agent` 立即生效。

**Q：跑完后如何同步候选到生产？**
A：本 example 的产物 `best_prompts/system_prompt.md` 为人工审批起点。建议的工作流：人工 review → 通过审批工具调用业务自有 SDK 把候选写入 production namespace（不通过本框架）。

**Q：能否优化多个远端字段？**
A：可以。`TargetPrompt` 支持多次 `add_callback`，每次注册一组独立的 `read` / `write`。多字段联合优化的算法层配置参见 `multi_agent_pipeline/` example。

## 8 · 接入自有业务的步骤

1. **替换 `store/prompt_client.py`**：实现 `read_sandbox_prompt` / `write_sandbox_prompt` 调用业务配置中心 SDK
2. **删除 `reset_store(...)` 调用** 或改为业务真实初始化逻辑
3. **修改 `agent/agent.py`**：对接业务模型 / tools / output schema
4. **替换数据集**：`train.evalset.json` / `val.evalset.json`
5. **保持 `update_source=False`**：合规约束
6. **运行**：观察 `summary.txt` 与 `result.json`；最优候选位于 `runs/<timestamp>/best_prompts/`，由人工审批后通过业务自有流程同步到生产
