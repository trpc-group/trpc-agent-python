# 流式工具调用示例 (Streaming Tool Call Demo)

本示例演示了如何使用 `StreamingFunctionTool` 实现流式工具调用参数的实时接收和展示。

## 功能特点

- **实时参数流式传输**：LLM 生成工具参数时，可以实时接收部分参数
- **统一事件处理**：通过 Runner 层消费 streaming 事件
- **进度展示**：实时显示工具参数的生成进度

## 核心概念

### StreamingFunctionTool

`StreamingFunctionTool` 是 `FunctionTool` 的标记类扩展，用于告诉框架为该工具启用流式参数：

```python
from trpc_agent_sdk.tools import StreamingFunctionTool

# 创建流式工具 - 无需回调，通过 Runner 层消费事件
write_file_tool = StreamingFunctionTool(write_file)
```

### LlmAgent 配置

当 `tools` 列表中包含 `StreamingFunctionTool` 时，框架会**自动启用**流式工具调用参数功能：

```python
agent = LlmAgent(
    name="streaming_agent",
    model=model,
    tools=[write_file_tool],  # 包含 StreamingFunctionTool，自动启用 stream_tool_call_args
)
```

如需显式控制，可手动设置 `stream_tool_call_args=True` 或 `stream_tool_call_args=False`。

### 事件处理

流式工具调用事件通过 Runner.run_async() 消费：

```python
from trpc_agent_sdk.models import constants as const

async for event in runner.run_async(...):
    if event.is_streaming_tool_call():
        # 处理流式工具调用事件（delta 模式）
        for part in event.content.parts:
            if part.function_call:
                delta = part.function_call.args.get(const.TOOL_STREAMING_ARGS, "")
                if delta:
                    print(f"流式增量: {delta}")
```

## 环境要求

- Python 3.10+（推荐 3.12）
- trpc-agent 框架

## 运行示例

1. 设置环境变量（在 `.env` 文件中或通过 export）：

```bash
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=https://your-api-endpoint
TRPC_AGENT_MODEL_NAME=your-model-name
```

2. 运行示例：

```bash
cd examples/llmagent_with_streaming_tool/
python3 run_agent.py
```

## 预期输出

```
╔══════════════════════════════════════════════════════════════╗
║           Streaming Tool Call Arguments Demo                  ║
╚══════════════════════════════════════════════════════════════╝

============================================================
🆔 Session ID: abc12345...
📝 User: 请帮我创建一个简单的 HTML 页面...
============================================================

🤖 Processing...

⏳ [Streaming] write_file: {"path": "index.html", "content": "<!DOCTYPE html>...
⏳ [Streaming] write_file: <html>...
⏳ [Streaming] write_file: <head>...

✅ [Tool Call Complete] write_file
   Arguments: {'path': 'index.html', 'content': '...'}

📄 [Simulated File Write]
   Path: index.html
   Content (xxx chars):
----------------------------------------
<!DOCTYPE html>
<html>
...
</html>
----------------------------------------

📊 [Tool Result] {'success': True, 'path': 'index.html', ...}

💬 我已经帮您创建了一个简单的 HTML 页面...
```

## 应用场景

1. **代码生成**：实时预览 LLM 正在生成的代码
2. **文档写作**：显示文档内容的生成进度
3. **长文本处理**：减少用户等待焦虑
4. **错误提前发现**：用户可以在生成过程中发现问题并取消

## 相关文档

- [流式工具调用文档](../../docs/tools/stream_tools.md)
