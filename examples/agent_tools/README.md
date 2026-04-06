# AgentTool 跨 Agent 协作示例

本示例演示如何使用 `AgentTool` 将一个 `LlmAgent` 包装为工具，实现主 Agent 通过工具调用的方式委托子 Agent 完成翻译任务。

## 关键特性

- **Agent 工具化**：通过 `AgentTool(agent=...)` 将翻译 Agent 包装为工具，供主 Agent 按需调用
- **跨 Agent 协作**：主 Agent 根据用户意图自动决定是否调用翻译工具，实现职责分离
- **共享模型实例**：主 Agent 与子 Agent 共享同一个 `OpenAIModel` 实例，降低资源开销
- **流式事件处理**：通过 `runner.run_async(...)` 消费事件流，打印工具调用与工具返回
- **会话状态管理**：使用 `InMemorySessionService` 保存会话状态

## Agent 层级结构说明

本例为主/子双 Agent 结构，子 Agent 通过 `AgentTool` 包装后以工具形式挂载到主 Agent：

```text
content_processor (LlmAgent) — 主 Agent
├── model: OpenAIModel
├── instruction: MAIN_INSTRUCTION
└── tools:
    └── AgentTool(translator)
        └── translator (LlmAgent) — 子 Agent
            ├── model: OpenAIModel（共享）
            └── instruction: TRANSLATOR_INSTRUCTION
```

关键文件：

- [examples/agent_tools/agent/agent.py](./agent/agent.py)：组装主 Agent，创建模型、挂载 AgentTool
- [examples/agent_tools/agent/tools.py](./agent/tools.py)：创建翻译子 Agent 并包装为 AgentTool
- [examples/agent_tools/agent/prompts.py](./agent/prompts.py)：主 Agent 与翻译 Agent 的提示词
- [examples/agent_tools/agent/config.py](./agent/config.py)：环境变量读取
- [examples/agent_tools/run_agent.py](./run_agent.py)：测试入口，执行翻译对话

## 关键代码解释

这一节用于快速定位"AgentTool 包装、工具调用委托、事件输出"三条核心链路。

### 1) Agent 组装与 AgentTool 挂载（`agent/agent.py` + `agent/tools.py`）

- 在 `tools.py` 中，使用 `LlmAgent` 创建翻译子 Agent，再通过 `AgentTool(agent=translator)` 将其包装为工具
- 在 `agent.py` 中，将 `AgentTool` 实例挂载到主 Agent 的 `tools` 列表
- 主 Agent 与子 Agent 共享同一个 `OpenAIModel` 实例

### 2) 提示词设计（`agent/prompts.py`）

- `MAIN_INSTRUCTION`：指示主 Agent 作为内容处理助手，根据用户请求判断是否需要调用翻译工具
- `TRANSLATOR_INSTRUCTION`：指示翻译 Agent 进行中英文专业翻译，保持原文语气与语义

### 3) 流式事件处理与可观测输出（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用，即主 Agent 调用翻译 AgentTool）
  - `function_response`（工具返回，即翻译 Agent 的回复结果）

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/agent_tools/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/agent_tools
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: 22fd13a1...
📝 User: Please translate this to Chinese: Artificial intelligence is changing our world.
🤖 Assistant:
🔧 [Invoke Tool: translator({'request': 'Artificial intelligence is changing our world.'})]
📊 [Tool Result: {'result': '人工智能正在改变我们的世界。'}]
翻译结果：人工智能正在改变我们的世界。
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：主 Agent 识别出用户的翻译需求后，正确调用了 `translator` AgentTool
- **工具参数正确**：传入的 `request` 参数为用户原文，符合翻译 Agent 的输入预期
- **工具结果被正确消费**：翻译 Agent 返回的中文翻译结果被主 Agent 正确引用并回复给用户
- **Agent 协作链路完整**：主 Agent → AgentTool → 翻译子 Agent → 返回结果 → 主 Agent 输出，整条链路正常运作

## 适用场景建议

- 快速验证 AgentTool 将子 Agent 包装为工具的能力：适合使用本示例
- 验证跨 Agent 协作与工具调用委托机制：适合使用本示例
- 需要测试多工具并行调用或复杂工具编排：建议使用 `examples/function_tools`
- 需要测试多 Agent 分层路由与分支隔离：建议使用 `examples/filter_with_agent`
