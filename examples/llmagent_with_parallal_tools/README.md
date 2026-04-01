# LLM Agent 并行工具调用示例

本示例演示如何在 `LlmAgent` 中开启 `parallel_tool_calls`，并让模型在单轮请求中并行调用多个异步工具（运动 / 看电视 / 听音乐）。

## 关键特性

- **并行工具调用**：通过 `parallel_tool_calls=True` 允许模型并行发起多个 tool call
- **ToolSet 集合管理**：使用 `HobbyToolSet` 统一注册多个 `FunctionTool`
- **异步工具模拟**：三个工具分别有不同耗时（3s / 5s / 6s），便于观察并行效果
- **单轮多能力分析**：一次输入同时触发运动、电视、音乐三个维度分析
- **结果聚合输出**：并行返回各工具结果后，统一生成总结性回答

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
hobby_toolset_agent (LlmAgent)
├── tools: HobbyToolSet
│   ├── sports(name)        # ~3s
│   ├── watch_tv(tv)        # ~5s
│   └── listen_music(music) # ~6s
└── parallel_tool_calls=True
```

关键文件：

- `examples/llmagent_with_parallal_tools/agent/agent.py`：Agent 创建与并行参数配置
- `examples/llmagent_with_parallal_tools/agent/tools.py`：`HobbyToolSet` 与 3 个异步工具
- `examples/llmagent_with_parallal_tools/agent/prompts.py`：工具调用规则提示词
- `examples/llmagent_with_parallal_tools/run_agent.py`：运行入口与结果打印

## 关键代码解释

这一节用于快速定位“并行调用是如何生效的”。

### 1) 开启并行调用（`agent/agent.py`）

- 在 `LlmAgent` 中设置 `parallel_tool_calls=True`
- 模型可在同一轮调用出现非阻塞异步调用工具

### 2) ToolSet 注册多个工具（`agent/tools.py`）

- `HobbyToolSet.initialize()` 注册 `sports`、`watch_tv`、`listen_music`
- 三个工具均为 async 函数，并模拟不同耗时

### 3) 运行与观测（`run_agent.py`）

- 发送包含三种兴趣关键词的用户请求
- 在输出中可观测到 3 次工具调用与 3 次工具结果
- 最终由模型聚合结果生成自然语言总结

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 `examples/llmagent_with_parallal_tools/.env` 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_parallal_tools
python3 run_agent.py
```

## 运行结果（实测）

```text
🔧 [Invoke Tool: sports({'name': 'running'})]
🔧 [Invoke Tool: watch_tv({'tv': 'cctv'})]
🔧 [Invoke Tool: listen_music({'music': 'QQ music'})]
📊 [Tool Result: {'result': 'running takes 3s'}]
📊 [Tool Result: {'result': 'cctv is broadcasting News Network'}]
📊 [Tool Result: {'result': 'QQ music is playing light music'}]
...
cost 16.232512712478638
✅ HobbyToolSet Agent Demo End！
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **多工具调用正确触发**：同一轮请求中触发了 3 个不同工具
- **工具结果完整返回**：三类结果（运动/电视/音乐）均成功回传
- **结果可被聚合消费**：模型基于三个工具结果给出综合分析
- **并行能力验证通过**：配置与行为符合“并行工具调用”设计目标

## 适用场景建议

- 需要一次请求并发查询多个信息源：适合使用本示例
- 需要验证 ToolSet + 并行调用链路：适合使用本示例
- 仅验证基础单工具调用：建议使用 `examples/llmagent`
