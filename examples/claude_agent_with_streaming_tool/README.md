# ClaudeAgent 选择性流式工具调用示例

本示例演示了 ClaudeAgent 的**选择性流式工具**支持，现在与 LlmAgent 行为完全对齐：

- **StreamingFunctionTool**：接收实时流式参数事件
- **普通 FunctionTool**：不接收流式事件，参数完成后才显示

## 功能特点

- **选择性流式**：只有 `is_streaming=True` 的工具才接收流式事件
- **与 LlmAgent 对齐**：行为完全一致
- **混合使用**：同一个 Agent 中可以同时使用流式和非流式工具
- **统一事件处理**：通过 Runner 层消费 streaming 事件

## 核心概念

### 选择性流式工具

ClaudeAgent 现在支持选择性流式，与 LlmAgent 行为一致：

```python
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.tools import StreamingFunctionTool, FunctionTool

# 流式工具 - 参数实时流式传输
write_file_tool = StreamingFunctionTool(write_file)  # is_streaming=True

# 普通工具 - 参数完成后才显示
get_file_info_tool = FunctionTool(get_file_info)     # is_streaming=False

agent = ClaudeAgent(
    name="claude_streaming_agent",
    model=model,
    tools=[
        write_file_tool,      # 会收到 ⏳ [Streaming] 事件
        get_file_info_tool,   # 不会收到流式事件
    ],
    enable_session=True,
)
```

### 事件处理

流式工具调用事件通过 Runner.run_async() 消费：

```python
from trpc_agent_sdk.models import constants as const

async for event in runner.run_async(...):
    if event.is_streaming_tool_call():
        # 只有 StreamingFunctionTool 的工具才会到达这里
        for part in event.content.parts:
            if part.function_call:
                delta = part.function_call.args.get(const.TOOL_STREAMING_ARGS, "")
                if delta:
                    print(f"流式增量: {delta}")
```

### ClaudeAgent vs LlmAgent 流式工具调用

| 特性 | LlmAgent | ClaudeAgent |
|------|----------|-------------|
| 支持 StreamingFunctionTool | ✅ | ✅ |
| 选择性流式（只对 is_streaming=True 的工具） | ✅ | ✅ |
| Runner 层事件处理 | ✅ | ✅ |
| tool_streaming_args | ✅ | ✅ |
| is_streaming_tool_call() | ✅ | ✅ |

## 环境要求

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e '.[agent-claude]'
```

## 运行示例

1. 设置环境变量（在 `.env` 文件中或通过 export）：

```bash
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=https://your-api-endpoint
TRPC_AGENT_MODEL_NAME=your-model-name
```

2. 运行示例：

```bash
cd examples/claude_agent_with_streaming_tool/
python3 run_agent.py
```

## 预期输出

```
╔══════════════════════════════════════════════════════════════╗
║   ClaudeAgent Selective Streaming Tool Call Demo             ║
╠══════════════════════════════════════════════════════════════╣
║  - write_file (StreamingFunctionTool): Shows ⏳ events       ║
║  - get_file_info (FunctionTool): No streaming events         ║
╚══════════════════════════════════════════════════════════════╝

============================================================
🆔 Session ID: abc12345...
📝 User: 请帮我创建一个简单的 HTML 页面...
============================================================

🤖 Processing...

⏳ [Streaming] write_file: {"path": "index.html", "content": "<!DOCTYPE html>...
⏳ [Streaming] write_file: <html>...

✅ [Tool Call Complete] write_file
   Arguments: {'path': 'index.html', 'content': '...'}

📄 [Simulated File Write]
   Path: index.html
   Content (xxx chars):
...

📊 [Tool Result] {'success': True, 'path': 'index.html', ...}

💬 我已经帮您创建了一个简单的 HTML 页面...

------------------------------------------------------------

============================================================
🆔 Session ID: def67890...
📝 User: 请帮我查看 index.html 文件的信息。
============================================================

🤖 Processing...

✅ [Tool Call Complete] get_file_info    <-- 注意：没有流式事件！
   Arguments: {'path': 'index.html'}

📋 [Get File Info] path=index.html

📊 [Tool Result] {'success': True, 'path': 'index.html', ...}

💬 index.html 文件的信息如下...

🧹 Claude environment cleaned up
```

## 实现原理

ClaudeAgent 的选择性流式工具调用支持通过以下方式实现：

1. **工具检测**：在 `_run_async_impl` 开始时检测 `is_streaming=True` 的工具
2. **StreamEvent 处理**：监听 Claude SDK 的 `content_block_start` 和 `content_block_delta` 事件
3. **选择性过滤**：只有在 `_streaming_tool_names` 中的工具才发射流式事件
4. **事件发射**：构建带有 `tool_streaming_args` 的 Event
5. **Runner 层消费**：用户在 Runner.run_async() 中统一消费 streaming 事件

```
Claude SDK StreamEvent
    │
    ├──► content_block_start (type=tool_use)
    │         └──► 记录工具信息 (id, name)
    │
    └──► content_block_delta (type=input_json_delta)
              │
              ├──► 检查 tool_name in _streaming_tool_names?
              │         │
              │         ├── Yes ──► 发射 Event with {tool_streaming_args}
              │         │                 │
              │         │                 └──► Runner 层消费 streaming 事件
              │         │
              │         └── No ──► 跳过（不发射流式事件）
```

## 应用场景

1. **代码生成**：实时预览 LLM 正在生成的代码
2. **文档写作**：显示文档内容的生成进度
3. **长文本处理**：减少用户等待焦虑
4. **错误提前发现**：用户可以在生成过程中发现问题并取消

## 相关文档

- [流式工具调用文档](../../docs/tools/stream_tools.md)
- [ClaudeAgent 文档](../../docs/agents/claude_agent.md)
- [LlmAgent 流式工具示例](../llmagent_with_streaming_tool/)
