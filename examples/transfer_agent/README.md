# TransferAgent 多 Agent 路由示例

本示例演示如何使用 `TransferAgent` 包装外部远程 Agent（`TrpcRemoteA2aAgent`），并在主流程中将结果转交给下游分析 Agent，验证“远程能力接入 + 转交路由 + 二次加工输出”链路。

## 关键特性

- **外部 Agent 接入**：将远程 A2A Agent（`TrpcRemoteA2aAgent`）接入框架编排链路
- **转交能力**：通过 `transfer_to_agent` 将中间结果路由给 `data_analyst`
- **流式可观测**：运行日志可看到各节点（`remote-weather-assistant`、`remote-weather-assistant_transfer`、`data_analyst`）输出
- **结果二次加工**：下游 Agent 对天气结果进行结构化整理（表格输出）

## Agent 层级结构说明

本例核心链路如下：

```text
root_agent (TransferAgent wrapper)
├── target agent: remote-weather-assistant
├── transfer node: remote-weather-assistant_transfer
└── sub agent: data_analyst
```

关键文件：

- [examples/transfer_agent/agent/agent.py](./agent/agent.py)：Agent 组装与 transfer 路由
- [examples/transfer_agent/agent/prompts.py](./agent/prompts.py)：各 Agent 指令
- [examples/transfer_agent/agent/tools.py](./agent/tools.py)：天气工具与转交工具
- [examples/transfer_agent/run_agent.py](./run_agent.py)：运行入口与日志打印

## 关键代码解释

### 1) TransferAgent 封装远程 A2A 能力

- 将远程 A2A 服务能力接入为可编排节点，主入口统一调用
- 保留外部 Agent 的输出语义，同时支持内部下游路由

### 2) 结果转交给下游分析 Agent

- 中间节点触发 `transfer_to_agent({'agent_name': 'data_analyst'})`
- `data_analyst` 读取天气结果并转换为更清晰的表格表达

### 3) 流式节点日志验证

- 运行日志按节点分段打印，便于确认每一步是否执行
- 可直接判断“外部查询 -> 转交 -> 二次输出”的完整性

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

在 [examples/transfer_agent/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`
- `REMOTE_A2A_BASE_URL`（可选，默认 `http://127.0.0.1:18081`）
- `TRPC_TRANSFER_AUTO_START_REMOTE_A2A`（可选，默认 `1`，当目标地址是本地且端口未占用时自动拉起内嵌 A2A 服务）

### 启动顺序（自动/手动两种方式）

默认推荐直接运行当前示例，脚本会自动检测并在需要时拉起本地 A2A 服务：

```bash
# Terminal 1
cd examples/transfer_agent
python3 run_agent.py
```

如需手动启动远程服务（例如联调独立进程），可关闭自动拉起后按两终端方式运行：

```bash
# Terminal 1
export TRPC_TRANSFER_AUTO_START_REMOTE_A2A=0
cd examples/a2a
python3 run_server.py

# Terminal 2
cd examples/transfer_agent
python3 run_agent.py
```

## 运行结果（实测）

```text
[2026-04-07 17:18:08][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/_agent_service.py:110][3340485] Initialized A2A Agent Service embedded_weather_agent_service for weather_agent
✅ Embedded remote A2A server started at http://127.0.0.1:18081
🆔 Session ID: f885aa53...
📝 User: What is the weather in Shenzhen today?
🤖 Assistant: [2026-04-07 17:18:09][INFO][trpc_agent_sdk][trpc_agent_sdk/server/a2a/executor/_a2a_agent_executor.py:201][3340485] Execute request for user_id: A2A_USER_f885aa53-d492-4028-9c4e-1de3d8daab31, session_id: f885aa53-d492-4028-9c4e-1de3d8daab31


 ============ [remote-weather-assistant] ============


🔧 [Invoke Tool:: get_weather_report({'city': 'Shenzhen'})]
📊 [Tool Result: {'city': 'Shenzhen', 'temperature': '25C', 'condition': 'Sunny', 'humidity': '60%'}]
The weather in Shenzhen today is sunny with a temperature of 25°C and humidity at 60%. It's a pleasant day!

 ============ [remote-weather-assistant_transfer] ============


🔧 [Invoke Tool:: transfer_to_agent({'agent_name': 'data_analyst'})]
📊 [Tool Result: {'transferred_to': 'data_analyst'}]


 ============ [data_analyst] ============

Here is the weather data for Shenzhen today in tabular form:

| City    | Temperature | Condition | Humidity |
|---------|-------------|-----------|----------|
| Shenzhen | 25°C        | Sunny     | 60%      |
```

## 结果分析（是否符合要求）

结论：**符合当前示例预期**（远程 A2A 调用链路可用，且本地自动拉起生效）。

- **自动拉起生效**：日志出现 `Initialized A2A Agent Service ...` 与 `Embedded remote A2A server started ...`
- **远程调用生效**：出现 `remote-weather-assistant` 节点输出及 `get_weather_report` 工具调用
- **结果可验证**：`Shenzhen` 现已命中示例数据，返回温度/天气/湿度完整字段
- **转交链路生效**：本次实测触发 `transfer_to_agent`，并由 `data_analyst` 产出表格化结果

## 适用场景建议

- 需要把外部远程 Agent（A2A 服务）接入 `trpc-agent` 编排体系的场景
- 需要对外部返回结果做二次加工（分析、结构化、格式化）的场景
- 需要验证“查询 + 转交 + 下游消费”多节点联动链路的场景
