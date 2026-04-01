# FunctionTool 使用示例

本示例展示了 `FunctionTool` 的两种创建方式以及多种工具的组合使用：

- **直接包装函数** — 用 `FunctionTool(func)` 包装普通函数（同步/异步均可）
- **装饰器注册** — 用 `@register_tool` 注册工具，再通过 `get_tool` 获取

示例中包含以下工具：

| 工具 | 创建方式 | 说明 |
|------|---------|------|
| `get_weather` | FunctionTool 包装 | 查询城市天气信息 |
| `calculate` | FunctionTool 包装 | 基础数学运算 |
| `get_postal_code` | FunctionTool 包装 | 根据地址查询邮编（使用 Pydantic 模型作为参数） |
| `get_session_info` | @register_tool 注册 | 获取当前会话信息（自动注入 InvocationContext） |

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
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
cd examples/tool_with_function/
python3 run_agent.py
```
