# TransferAgent 示例代码

## 简介

本示例演示如何使用 `TransferAgent` 将自定义 Agent（KnotAgent）接入框架的多 Agent 系统。

TransferAgent 的主要作用是：
- **接入自定义 Agent**：将不支持 transfer 能力的自定义 Agent（如 KnotAgent）接入框架的多 Agent 系统
- **作为 sub_agent**：TransferAgent 本身可以作为其他 Agent 的 sub_agent，被其他 Agent 调用
- **转发给其他 Agent**：TransferAgent 可以将目标 Agent 的返回结果转发给其他子 Agent 进行进一步处理

在本示例中，TransferAgent 包装了 KnotAgent（作为目标 Agent），并根据 KnotAgent 的返回结果路由到子 Agent（data_analyst）进行进一步处理。

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 运行此代码示例

在 `.env` 文件中设置相关环境变量（也可以通过export设置）:

**Knot API 配置:**
- `KNOT_API_URL`: Knot API 端点 URL，格式为 `http://knot.woa.com/apigw/api/v1/agents/agui/{agent_id}`
- `KNOT_API_KEY`: Knot API 认证密钥
- `KNOT_MODEL`: 使用的模型名称

（Knot API 用户名传入方式如下：通过 user_id 或 session.user_id。）

**LLM Model 配置（用于 TransferAgent 和子 Agent）:**
- `TRPC_AGENT_API_KEY`: LLM API 密钥
- `TRPC_AGENT_BASE_URL`: LLM API 基础 URL
- `TRPC_AGENT_MODEL_NAME`: LLM 模型名称

然后运行下面的命令：

```bash
cd examples/transfer_agent/
python3 run_agent.py
```

## 说明

### 使用场景

- **场景 1：作为 sub_agent**
  ```python
  coordinator = LlmAgent(
      name="coordinator",
      model=model,
      sub_agents=[transfer_agent],  # TransferAgent 作为 sub_agent
  )
  ```

- **场景 2：转发给其他 Agent**
  在本示例中，TransferAgent 将 KnotAgent 作为目标 Agent，当 KnotAgent 返回包含数据或统计信息的结果时，TransferAgent 会将其转发给 `data_analyst` 进行深度分析。如果返回的是简单文本，则直接返回给用户。

### 特性

- 支持流式响应和工具调用
- 支持会话连续性
- 无缝集成自定义 Agent 到多 Agent 系统
