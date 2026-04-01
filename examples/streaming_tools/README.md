# Streaming Tool 使用示例

本示例展示了 `Streaming Tool` 与 `FunctionTool` 的组合使用，实现流式文件写入功能：

| 工具 | 类型 | 说明 |
|------|------|------|
| `write_file` | StreamingFunctionTool | 流式写入文件内容，支持实时查看生成进度 |
| `get_file_info` | FunctionTool | 查询文件信息（非流式） |

`Streaming Tool` 允许在 LLM 生成工具参数时实时接收增量内容，适用于大内容（如代码文件）的生成场景。

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
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
cd examples/tool_with_streaming_tool/
python3 run_agent.py
```
