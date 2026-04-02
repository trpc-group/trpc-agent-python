# FunctionTool 使用示例

本示例演示 `FunctionTool` 的两种创建方式（直接包装函数 与 装饰器注册）以及多种工具的组合使用，验证 `FunctionTool + @register_tool + Pydantic 参数 + InvocationContext 注入` 的核心链路是否正常工作。

## 关键特性

- **直接包装函数**：通过 `FunctionTool(func)` 包装普通函数（同步/异步均可），快速将业务逻辑转化为可调用工具
- **装饰器注册**：使用 `@register_tool` 注册工具，再通过 `get_tool` 按名称获取，适合模块化管理
- **Pydantic 模型参数**：`get_postal_code` 使用嵌套 Pydantic 模型作为入参，验证结构化参数的自动解析能力
- **InvocationContext 自动注入**：`get_session_info` 通过框架自动注入 `InvocationContext`，无需调用方手动传递
- **多工具路由覆盖**：同一程序内覆盖"天气查询 + 邮编查询 + 会话信息 + 数学运算"四类典型场景

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
function_tool_demo_agent (LlmAgent)
├── model: OpenAIModel
├── tools:
│   ├── get_weather(city)             — FunctionTool 包装
│   ├── calculate(operation, a, b)    — FunctionTool 包装
│   ├── get_postal_code(addr)         — FunctionTool 包装（Pydantic 参数）
│   └── get_session_info()            — @register_tool 注册（自动注入 InvocationContext）
└── session: InMemorySessionService
```

关键文件：

- [examples/function_tools/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载工具
- [examples/function_tools/agent/tools.py](./agent/tools.py)：四个工具函数的实现（含 Pydantic 模型定义）
- [examples/function_tools/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/function_tools/agent/config.py](./agent/config.py)：环境变量读取
- [examples/function_tools/run_agent.py](./run_agent.py)：测试入口，执行 4 轮对话

## 关键代码解释

这一节用于快速定位"两种工具创建方式、Pydantic 参数、InvocationContext 注入"四条核心链路。

### 1) 两种工具创建方式（`agent/tools.py` + `agent/agent.py`）

- **FunctionTool 包装**：`get_weather`、`calculate`、`get_postal_code` 定义为普通函数，在 `agent.py` 中通过 `FunctionTool(func)` 包装为工具
- **@register_tool 注册**：`get_session_info` 使用 `@register_tool("get_session_info")` 装饰器注册，在 `agent.py` 中通过 `get_tool("get_session_info")` 按名称获取

### 2) Pydantic 模型作为工具参数（`agent/tools.py`）

- 定义嵌套模型 `City` → `Address` → `PostalCodeInfo`，用于 `get_postal_code` 的入参和返回值
- 框架自动从 Pydantic 模型生成 JSON Schema，LLM 可直接构造结构化参数调用

### 3) InvocationContext 自动注入（`agent/tools.py`）

- `get_session_info` 的参数 `tool_context: InvocationContext` 由框架自动注入
- 通过 `tool_context.session` 可访问当前会话的 `id`、`user_id`、`app_name` 等信息

### 4) 流式事件处理与可观测输出（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用）
  - `function_response`（工具返回）

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

在 [examples/function_tools/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/function_tools
python3 run_agent.py
```

## 运行结果（实测）

```text
🆔 Session ID: 0ef2e018...
📝 User: What's the weather in Beijing?
🤖 Assistant:
🔧 [Invoke Tool: get_weather({'city': 'Beijing'})]
📊 [Tool Result: {'status': 'success', 'city': 'Beijing', 'temperature': '15°C', 'condition': 'Sunny', 'humidity': '45%', 'last_updated': '2024-01-01T12:00:00Z'}]
The current weather in Beijing is sunny with a temperature of 15°C and humidity at 45%. The information was last updated on January 1, 2024, at 12:00 PM UTC.
----------------------------------------
🆔 Session ID: 8fc152f4...
📝 User: What is the postal code for Shenzhen, Guangdong?
🤖 Assistant:
🔧 [Invoke Tool: get_postal_code({'addr': {'city': {'city': 'Shenzhen'}, 'province': 'Guangdong'}})]
📊 [Tool Result: {'result': '{"city":"Shenzhen","postal_code":"518000"}'}]
The postal code for Shenzhen, Guangdong is **518000**.
----------------------------------------
🆔 Session ID: 2e30a1aa...
📝 User: Show me the current session info.
🤖 Assistant:
🔧 [Invoke Tool: get_session_info({})]
📊 [Tool Result: {'status': 'success', 'session_id': '2e30a1aa-9f6f-46fb-8bcc-3bc39f4033de', 'user_id': 'demo_user', 'app_name': 'function_tool_demo'}]
Here is the current session information:

- **Session ID**: 2e30a1aa-9f6f-46fb-8bcc-3bc39f4033de
- **User ID**: demo_user
- **Application Name**: function_tool_demo
----------------------------------------
🆔 Session ID: 0381773c...
📝 User: Calculate 15 * 3.5
🤖 Assistant:
🔧 [Invoke Tool: calculate({'operation': 'multiply', 'a': 15, 'b': 3.5})]
📊 [Tool Result: {'result': 52.5}]
The result of 15 × 3.5 is 52.5.
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：天气问题调用 `get_weather`，邮编问题调用 `get_postal_code`，会话信息调用 `get_session_info`，计算问题调用 `calculate`
- **工具参数正确**：邮编查询中嵌套 Pydantic 模型参数 `{'addr': {'city': {'city': 'Shenzhen'}, 'province': 'Guangdong'}}` 结构完整
- **InvocationContext 注入成功**：`get_session_info` 正确返回了当前会话的 `session_id`、`user_id`、`app_name`
- **工具结果被正确消费**：回复内容与工具返回数据一致，并能组织为可读答案
- **能力覆盖完整**：4 轮测试分别覆盖"天气查询、邮编查询（Pydantic 参数）、会话信息（Context 注入）、数学运算"四类核心场景

说明：该示例每轮使用新的 `session_id`，因此主要验证的是工具调用与回复质量，不强调跨轮记忆一致性。

## 适用场景建议

- 快速验证 FunctionTool 两种创建方式（直接包装 vs 装饰器注册）：适合使用本示例
- 验证 Pydantic 模型作为工具参数的自动解析能力：适合使用本示例
- 验证 InvocationContext 自动注入机制：适合使用本示例
- 需要测试多 Agent 分支隔离行为：建议使用其他多 Agent 示例
