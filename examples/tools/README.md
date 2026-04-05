# Tools 能力组合示例

本示例演示如何在同一工程中组合使用 `AgentTool`、`FunctionTool`、`LangChain Tool` 和 `ToolSet`，验证多种工具接入方式与权限控制策略。

## 关键特性

- **AgentTool 复用**：将翻译 Agent 封装为工具，由主 Agent 直接调用
- **FunctionTool 双模式**：同时演示“直接函数封装”与“装饰器注册”两种接入方式
- **LangChain 集成**：通过 `tavily_search` 展示第三方检索工具接入
- **ToolSet 动态权限**：依据 `session.state.user_type` 动态返回可用工具（BASIC/VIP）
- **统一流式观测**：日志中可同时看到工具调用参数、工具返回结果与最终回答

## Agent 层级结构说明

本例不是单一链路，而是 4 个独立 demo 串行执行：

```text
run_agent.py
├── AgentTool Demo
│   └── content_processor (LlmAgent) -> AgentTool(translator)
├── FunctionTool Demo
│   └── function_tool_demo_agent (LlmAgent) -> [get_weather, calculate, get_postal_code, get_session_info]
├── LangChain Tool Demo
│   └── langchain_tool_agent (LlmAgent) -> [tavily_search]
└── ToolSet Demo
    └── toolset_agent (LlmAgent) -> [WeatherToolSet(dynamic tools by user_type)]
```

关键文件：

- [examples/tools/run_agent.py](./run_agent.py)：四段 demo 的统一入口
- [examples/tools/agent/agent.py](./agent/agent.py)：各 demo Agent 的构建逻辑
- [examples/tools/agent/function_tool.py](./agent/function_tool.py)：函数工具与装饰器注册工具
- [examples/tools/agent/langchain_tool.py](./agent/langchain_tool.py)：LangChain Tavily 工具封装
- [examples/tools/agent/toolset.py](./agent/toolset.py)：基于用户类型的动态工具集

## 关键代码解释

### 1) AgentTool：Agent 能力工具化

- 先构建 `translator` Agent，再用 `AgentTool(agent=translator)` 包装
- 主 Agent `content_processor` 根据请求触发翻译工具，输出统一结果

### 2) FunctionTool：两种注册方式

- 直接封装：`FunctionTool(get_weather)`、`FunctionTool(calculate)`、`FunctionTool(get_postal_code)`
- 装饰器注册：`@register_tool("get_session_info")` 后通过 `get_tool("get_session_info")` 获取

### 3) ToolSet：按用户类型动态暴露能力

- `WeatherToolSet.get_tools(...)` 读取 `invocation_context.session.state["user_type"]`
- BASIC 用户仅能用当前天气工具，VIP 用户可额外使用预报工具

## 环境与运行

### 环境要求

- Python 3.10+（推荐 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .[langchain_tool]
```

### 环境变量要求

在 [examples/tools/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

可选（仅 LangChain Tool demo 需要）：

- `TAVILY_API_KEY`

### 运行命令

```bash
cd examples/tools
python3 run_agent.py
```

## 运行结果（实测）

```text
Test 1: 请将这段中文翻译成英文：人工智能正在改变我们的世界。
🔧 [Tool call: translator]
📊 [Tool result: {'result': 'Artificial intelligence is transforming our world.'}]

🔧 Function Tool demo
🔧 [Tool call: get_weather]
📊 [Tool result: {'status': 'success', 'city': 'Beijing', 'temperature': '15°C', 'condition': 'Sunny', 'humidity': '45%', ...}]
🔧 [Tool call: get_postal_code]
📊 [Tool result: {'result': '{"city":"Shenzhen","postal_code":"518000"}'}]
🔧 [Tool call: get_session_info]
📊 [Tool result: {'status': 'success', 'session_id': 'c8aa23f8-...', 'user_id': 'demo_user', 'app_name': 'function_tool_demo'}]
🔧 [Tool call: calculate]
📊 [Tool result: {'result': 52.5}]

🔎 LangChain Tool demo
🔧 [Tool call: tavily_search]
📊 [Tool result: {'status': 'error', 'error_message': '... Did not find tavily_api_key ...'}]

🔧 ToolSet demo
👤 User type: BASIC
🔧 [Tool call: get_current_weather]
👤 User type: VIP
🔧 [Tool call: get_weather_forecast]
✅ ToolSet demo completed!
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **AgentTool 路径正确**：翻译请求成功命中 `translator` 并返回预期翻译结果
- **FunctionTool 能力完整**：天气、邮编、会话信息、计算四类工具都被正确调用
- **ToolSet 权限控制生效**：BASIC 只调用当前天气工具，VIP 可调用天气预报工具
- **LangChain 异常可解释**：`tavily_search` 报错原因为缺失 `TAVILY_API_KEY`，属于环境配置问题，不是工具接入链路错误

## 适用场景建议

- 需要对比多种工具接入模式（AgentTool / FunctionTool / ToolSet）的场景
- 需要在同一 Agent 中按用户权限动态控制工具能力的场景
- 需要接入第三方检索能力并处理外部依赖配置的场景
