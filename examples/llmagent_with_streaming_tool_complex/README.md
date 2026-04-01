# Comprehensive Streaming Tool Test

这个示例用于全面测试流式工具调用的各种场景。

## 测试内容

| 测试场景 | 描述 | 预期行为 |
|---------|------|---------|
| Test 1 | 同步函数 -> StreamingFunctionTool | ✅ 应产生流式事件 |
| Test 2 | 异步函数 -> StreamingFunctionTool | ✅ 应产生流式事件 |
| Test 3 | FunctionTool -> StreamingFunctionTool | ✅ 应产生流式事件 |
| Test 4 | 自定义 BaseTool (is_streaming=True) | ✅ 应产生流式事件 |
| Test 5 | ToolSet 中的流式工具 | ✅ 应产生流式事件 |
| Test 6 | StreamingFunctionTool 包装普通函数 | ✅ 应产生流式事件 |
| Test 7 | 非流式工具对比 | ❌ 不应产生流式事件 |
| Test 8 | 混合配置测试 | 流式工具应产生事件，非流式不应产生 |

## 工具配置方式

### 1. 同步函数转换

```python
def write_file(path: str, content: str) -> dict:
    ...

tool = StreamingFunctionTool(write_file)
```

### 2. 异步函数转换

```python
async def async_write_file(path: str, content: str) -> dict:
    ...

tool = StreamingFunctionTool(async_write_file)
```

### 3. FunctionTool 转换

```python
function_tool = FunctionTool(my_func)
streaming_tool = StreamingFunctionTool(function_tool)
```

### 4. 自定义 BaseTool

```python
class CustomStreamingTool(BaseTool):
    @property
    def is_streaming(self) -> bool:
        return True
    
    async def _run_async_impl(self, *, tool_context, args):
        ...
```

### 5. ToolSet 中定义

```python
class MyToolSet(BaseToolSet):
    def __init__(self):
        self._tools = [StreamingFunctionTool(my_func)]
    
    async def get_tools(self, invocation_context=None):
        return self._tools
```

### 6. 直接包装普通函数

```python
def my_tool(arg: str) -> dict:
    ...

streaming_tool = StreamingFunctionTool(my_tool)
```

## 运行测试

```bash
cd examples/llmagent_with_streaming_tool_complex
python run_agent.py
```

## 环境变量

在 `.env` 文件中配置：

```
TRPC_AGENT_API_KEY=your_api_key
TRPC_AGENT_BASE_URL=your_base_url
TRPC_AGENT_MODEL_NAME=your_model_name
```

## 验证流式事件

测试会统计每个工具产生的流式事件数量：
- 流式工具应该有 `streaming_event_count > 0`
- 非流式工具应该有 `streaming_event_count = 0`

流式事件可通过 `event.is_streaming_tool_call()` 检测：

```python
async for event in runner.run_async(...):
    if event.is_streaming_tool_call():
        # 处理流式工具调用事件
        for part in event.content.parts:
            if part.function_call:
                delta = part.function_call.args.get("tool_streaming_args", "")
                print(f"Streaming: {delta}")
```

## 测试运行示例

以下是实际测试运行的关键日志输出：

### 流式事件输出示例

**Test 1: 同步函数 -> StreamingFunctionTool (write_file)**

```
⏳ [Streaming] write_file: {"path": 
⏳ [Streaming] write_file: "test.txt
⏳ [Streaming] write_file: ", "content": "
⏳ [Streaming] write_file: 春风吹拂
⏳ [Streaming] write_file: 柳丝长，
⏳ [Streaming] write_file: \n桃花满园
⏳ [Streaming] write_file: 散幽香。
...
✅ [Tool Complete] write_file
📄 [Sync Write File]
   Path: test.txt
   Content length: 35 chars
📊 [Result] tool_type=sync_function
```

**Test 2: 异步函数 -> StreamingFunctionTool (async_write_file)**

```
⏳ [Streaming] async_write_file: {"path": 
⏳ [Streaming] async_write_file: "async_test.py", 
⏳ [Streaming] async_write_file: "content": "#!/usr/bin/env python3
⏳ [Streaming] async_write_file: \ndef main():
...
✅ [Tool Complete] async_write_file
📄 [Async Write File]
   Path: async_test.py
   Content length: 163 chars
📊 [Result] tool_type=async_function
```

**Test 3: FunctionTool -> StreamingFunctionTool (append_file)**

```
⏳ [Streaming] append_file: {"path": "log.txt", "content": "[2025-06-17]
⏳ [Streaming] append_file:  测试状态：流式工具调用测试
...
✅ [Tool Complete] append_file
📄 [Append File]
   Path: log.txt
   Content length: 144 chars
📊 [Result] tool_type=function_tool_converted
```

**Test 4: 自定义 BaseTool (custom_write)**

```
⏳ [Streaming] custom_write: {"filename": "custom.json", "data": "{\n  \"app\": {
⏳ [Streaming] custom_write: \n    \"name\": \"MyApplication\",
...
✅ [Tool Complete] custom_write
📄 [Custom Streaming Write]
   Filename: custom.json
   Data length: 990 chars
📊 [Result] tool_type=custom_base_tool
```

**Test 5: ToolSet 中的流式工具 (_create_file)**

```
⏳ [Streaming] _create_file: {"path": "toolset_test.md", "content": "# ToolSet 功能介绍
⏳ [Streaming] _create_file: \n\n## 什么是 ToolSet？
...
✅ [Tool Complete] _create_file
📄 [ToolSet Create File]
   Path: toolset_test.md
   Content length: 1188 chars
📊 [Result] tool_type=toolset_streaming
```

**Test 6: StreamingFunctionTool 包装普通函数 (save_document)**

```
⏳ [Streaming] save_document: {"title": "测试报告", "body": "# 测试报告
⏳ [Streaming] save_document: \n\n## 测试日期\n2024年12月19日
...
✅ [Tool Complete] save_document
📄 [Save Document via StreamingFunctionTool]
   Title: 测试报告
   Body length: 330 chars
📊 [Result] tool_type=register_tool_decorator
```

**Test 7: 非流式工具对比 (get_file_info)**

```
✅ [Tool Complete] get_file_info
📊 [Get File Info] test.txt
📊 [Result] tool_type=non_streaming
```

> 注意：非流式工具没有 `⏳ [Streaming]` 事件输出。

### 测试结果总结

```
╔════════════════════════════════════════════════════════════════════╗
║                            TEST SUMMARY                            ║
╠════════════════════════════════════════════════════════════════════╣
║ Streaming Events by Tool:                                          ║
║   - _create_file: 211 streaming events                             ║
║   - append_file: 36 streaming events                               ║
║   - async_write_file: 24 streaming events                          ║
║   - custom_write: 120 streaming events                             ║
║   - save_document: 78 streaming events                             ║
║   - write_file: 120 streaming events                               ║
║                                                                    ║
║ Tool Executions:                                                   ║
║   - _create_file: 1 executions                                     ║
║   - append_file: 1 executions                                      ║
║   - async_write_file: 1 executions                                 ║
║   - custom_write: 1 executions                                     ║
║   - get_file_info: 2 executions                                    ║
║   - save_document: 1 executions                                    ║
║   - write_file: 2 executions                                       ║
╠════════════════════════════════════════════════════════════════════╣
║ Verification:                                                      ║
║   ✅ write_file: Streaming events detected                          ║
║   ✅ async_write_file: Streaming events detected                    ║
║   ✅ append_file: Streaming events detected                         ║
║   ✅ custom_write: Streaming events detected                        ║
║   ✅ _create_file: Streaming events detected                        ║
║   ✅ save_document: Streaming events detected                       ║
║   ✅ get_file_info: No streaming events (correct)                   ║
║   ⏭️  _read_file: Not called in tests                              ║
╠════════════════════════════════════════════════════════════════════╣
║                        🎉 ALL TESTS PASSED!                         ║
╚════════════════════════════════════════════════════════════════════╝
```

### 关键验证点

| 工具名称 | 工具类型 | 流式事件数 | 验证结果 |
|---------|---------|-----------|---------|
| write_file | sync_function | 120 | ✅ 正确产生流式事件 |
| async_write_file | async_function | 24 | ✅ 正确产生流式事件 |
| append_file | function_tool_converted | 36 | ✅ 正确产生流式事件 |
| custom_write | custom_base_tool | 120 | ✅ 正确产生流式事件 |
| _create_file | toolset_streaming | 211 | ✅ 正确产生流式事件 |
| save_document | register_tool_decorator | 78 | ✅ 正确产生流式事件 |
| get_file_info | non_streaming | 0 | ✅ 正确无流式事件 |
