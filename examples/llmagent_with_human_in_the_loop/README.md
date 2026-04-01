# Human-in-the-Loop Agent 示例

## 概述

本示例演示了如何基于 trpc-agent 框架，利用 `LongRunningFunctionTool` 实现 **Human-in-the-Loop（人机协同）** 模式。当 Agent 遇到需要人工审批的高风险操作（如删除生产数据库、重启服务器）时，会暂停执行并等待人工介入，审批通过后再恢复执行流程。

## 核心内容

### 架构设计

```
用户请求 → Main Agent（human_in_loop_agent）
                ├── 直接处理：调用 human_approval_required 工具 → 等待审批 → 恢复执行
                └── 转发给 Sub-Agent（system_operations_agent）
                        └── 调用 check_system_critical_operation 工具 → 等待审批 → 恢复执行
```

### 关键组件

| 组件 | 说明 |
|------|------|
| `LlmAgent` | 主 Agent 与子 Agent，负责理解用户意图并调用相应工具 |
| `LongRunningFunctionTool` | 将普通异步函数包装为长时运行工具，触发 `LongRunningEvent` 暂停执行流 |
| `human_approval_required` | 主 Agent 工具，处理通用的人工审批请求 |
| `check_system_critical_operation` | 子 Agent 工具，处理高风险系统操作的人工审批 |
| `Runner` | 执行器，通过 `run_async` 异步迭代事件流，捕获 `LongRunningEvent` 并恢复执行 |

### 执行流程

1. **用户发起请求** — 描述需要审批的操作
2. **Agent 调用长时运行工具** — 返回 `pending_approval` 状态，触发 `LongRunningEvent`
3. **Runner 捕获事件并暂停** — 将审批信息展示给人工操作者
4. **人工审批** — 修改状态为 `approved` 并构造 `FunctionResponse`
5. **恢复执行** — 将审批结果作为 `resume_content` 重新发送给 Agent，继续后续处理

### 项目结构

```
llmagent_with_human_in_the_loop/
├── run_agent.py          # 入口文件，包含运行逻辑和测试场景
├── .env                  # LLM 配置（API Key、Base URL、Model Name）
└── agent/
    ├── __init__.py
    ├── agent.py          # Agent 定义（主 Agent + 子 Agent）
    ├── tools.py          # 长时运行工具（审批类函数）
    ├── prompts.py        # Agent 指令提示词
    └── config.py         # 模型配置读取
```

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

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/llmagent_with_human_in_the_loop/
python3 run_agent.py
```
