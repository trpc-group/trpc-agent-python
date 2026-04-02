# ClaudeAgent 选择性流式工具调用示例

本示例演示如何基于 `ClaudeAgent` 实现**选择性流式工具调用**，即同一个 Agent 中同时使用 `StreamingFunctionTool`（实时流式参数）与普通 `FunctionTool`（参数完成后才显示），行为与 `LlmAgent` 完全对齐。

## 关键特性

- **选择性流式**：只有 `is_streaming=True` 的工具才接收流式参数事件，其余工具参数完成后才返回
- **混合工具使用**：同一个 Agent 中同时挂载 `StreamingFunctionTool` 与 `FunctionTool`，互不干扰
- **与 LlmAgent 行为对齐**：`ClaudeAgent` 的流式工具调用语义与 `LlmAgent` 完全一致
- **统一事件处理**：通过 `runner.run_async(...)` 消费 streaming 事件，区分流式增量与完整工具调用
- **多轮测试覆盖**：同一程序内覆盖"流式写文件 + 非流式查询文件信息"两类典型场景

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
claude_streaming_file_writer (ClaudeAgent)
├── model: OpenAIModel
├── tools:
│   ├── write_file(path, content)         [StreamingFunctionTool, is_streaming=True]
│   └── get_file_info(path)               [FunctionTool, is_streaming=False]
└── session: InMemorySessionService
```

关键文件：

- [examples/claude_agent_with_streaming_tool/agent/agent.py](./agent/agent.py)：构建 `ClaudeAgent`、挂载流式与非流式工具
- [examples/claude_agent_with_streaming_tool/agent/tools.py](./agent/tools.py)：`write_file`（模拟写文件）与 `get_file_info`（模拟查询文件信息）工具实现
- [examples/claude_agent_with_streaming_tool/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/claude_agent_with_streaming_tool/agent/config.py](./agent/config.py)：环境变量读取
- [examples/claude_agent_with_streaming_tool/run_agent.py](./run_agent.py)：测试入口，执行 2 轮对话（流式写文件 + 非流式查询文件信息）

## 关键代码解释

这一节用于快速定位"流式工具注册、事件分类处理、选择性过滤"三条核心链路。

### 1) Agent 组装与工具注册（`agent/agent.py`）

- 使用 `ClaudeAgent` 组装文件操作助手，挂载 `StreamingFunctionTool(write_file)` 与 `FunctionTool(get_file_info)`
- `StreamingFunctionTool` 的 `is_streaming=True`，运行时 LLM 生成的参数会实时推送
- `FunctionTool` 的 `is_streaming=False`（默认），参数完成后才触发一次性回调

### 2) 流式事件分类处理（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.is_streaming_tool_call()` 为 `True` 时，提取 `TOOL_STREAMING_ARGS` 增量打印（仅 `StreamingFunctionTool` 触发）
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用完成，含完整参数）
  - `function_response`（工具返回结果）

### 3) 选择性流式实现原理

- `ClaudeAgent` 在 `_run_async_impl` 开始时检测 `is_streaming=True` 的工具名称集合
- 监听 Claude SDK 的 `content_block_start` / `content_block_delta` 事件
- 仅当工具名在流式集合中时，才发射带有 `tool_streaming_args` 的 Event，否则跳过

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e '.[agent-claude]'
```

### 环境变量要求

在 [examples/claude_agent_with_streaming_tool/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/claude_agent_with_streaming_tool
python3 run_agent.py
```

## 运行结果（实测）

```text
╔══════════════════════════════════════════════════════════════╗
║   ClaudeAgent Selective Streaming Tool Call Demo             ║
╠══════════════════════════════════════════════════════════════╣
║  This demo shows selective streaming - only tools with       ║
║  is_streaming=True receive real-time argument updates.       ║
║                                                              ║
║  - write_file (StreamingFunctionTool): Shows ⏳ events       ║
║  - get_file_info (FunctionTool): No streaming events         ║
╚══════════════════════════════════════════════════════════════╝

============================================================
🆔 Session ID: 09d29ef8...
📝 User: 请帮我创建一个简单的 HTML 页面，文件名为 index.html，内容是一个带有标题和段落的网页。
============================================================

🤖 Processing...

⏳ [Streaming] mcp__claude_streaming_file_writer_tools__write_file: {"
⏳ [Streaming] mcp__claude_streaming_file_writer_tools__write_file: path":"
⏳ [Streaming] mcp__claude_streaming_file_writer_tools__write_file: index.html","
⏳ [Streaming] mcp__claude_streaming_file_writer_tools__write_file: content":"<!
⏳ [Streaming] mcp__claude_streaming_file_writer_tools__write_file: DOCTYPE html>\
⏳ [Streaming] mcp__claude_streaming_file_writer_tools__write_file: n<html lang
...（省略部分流式增量输出）...
⏳ [Streaming] mcp__claude_streaming_file_writer_tools__write_file: }

✅ [Tool Call Complete] mcp__claude_streaming_file_writer_tools__write_file
   Arguments: {'path': 'index.html', 'content': '<!DOCTYPE html>\n<html lang="en">...'}

📄 [Simulated File Write]
   Path: index.html
   Content (336 chars):
----------------------------------------
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Simple HTML Page</title>
</head>
<body>
    <h1>Welcome to My Simple HTML Page</h1>
    <p>This is a simple paragraph to demonstrate the structure of an HTML page.</p>
</body>
</html>
----------------------------------------

📊 [Tool Result] {'result': "{'success': True, 'path': 'index.html', 'bytes_written': 336, 'message': 'Successfully wrote 336 characters to index.html'}"}
我已成功创建了一个简单的 HTML 页面，文件名为 `index.html`。
------------------------------------------------------------

============================================================
🆔 Session ID: aa7446f5...
📝 User: 请帮我查看 index.html 文件的信息。
============================================================

🤖 Processing...

✅ [Tool Call Complete] mcp__claude_streaming_file_writer_tools__get_file_info
   Arguments: {'path': 'index.html'}

📋 [Get File Info] path=index.html

📊 [Tool Result] {'result': "{'success': True, 'path': 'index.html', 'exists': True, 'size': 1024, 'type': 'text/plain', 'message': 'File info retrieved for index.html'}"}
文件 `index.html` 的信息如下：

- **路径**: `index.html`
- **存在**: 是
- **大小**: 1024 字节
- **类型**: `text/plain`
------------------------------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **流式工具事件正确**：第 1 轮调用 `write_file` 时可见 `⏳ [Streaming]` 增量输出，参数实时推送
- **非流式工具无流式事件**：第 2 轮调用 `get_file_info` 时无 `⏳ [Streaming]` 事件，参数完成后直接显示 `✅ [Tool Call Complete]`
- **工具路由正确**：创建文件请求路由到 `write_file`，查询文件信息请求路由到 `get_file_info`
- **工具结果被正确消费**：回复内容与工具返回数据一致，并能组织为可读答案

说明：该示例每轮使用新的 `session_id`，因此主要验证的是选择性流式工具调用与事件分类处理，不强调跨轮记忆一致性。

## 适用场景建议

- 快速验证 ClaudeAgent 流式工具调用主链路：适合使用本示例
- 验证 `StreamingFunctionTool` 与 `FunctionTool` 混合使用的选择性过滤行为：适合使用本示例
- 需要测试 LlmAgent 流式工具调用：建议使用 `examples/llmagent_with_streaming_tool`
- 需要测试普通（非流式）工具调用：建议使用 `examples/claude_agent` 或 `examples/llmagent`
