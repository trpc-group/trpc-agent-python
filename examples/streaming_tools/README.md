# 流式工具示例

本示例演示 `StreamingFunctionTool` 在工具参数生成过程中流式增量到达（配合 `TOOL_STREAMING_ARGS`），并与普通 `FunctionTool` 一起挂载在单智能体上，完成「边生成边写入」类任务。

## 关键特性

- `event.is_streaming_tool_call()` 分支打印生成进度（字符数）
- `write_file` 以流式参数接收大段代码内容；`get_file_info` 为非流式查询
- `Runner` + 会话用于一次演示查询

## Agent 层级结构说明

- 根节点：`LlmAgent`（`streaming_tool_demo_agent`），工具：`StreamingFunctionTool(write_file)`、`FunctionTool(get_file_info)`
- 无子 Agent

## 关键代码解释

- `run_agent.py`：对流式工具调用累加 `delta`，打印 `⏳ Generated N chars...`；完成后打印工具结果
- `agent/agent.py`：注册 `StreamingFunctionTool` 与 `FunctionTool`
- `agent/tools.py`：实现 `write_file`、`get_file_info` 的具体逻辑

## 环境与运行

- Python 3.10+；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`（可用 `.env`）

```bash
cd examples/streaming_tools
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] streaming_tools
📝 User: 请帮我创建一个 Python 脚本 hello.py，实现简单的计算器功能
⏳ Generated 2 chars...
...
✅ Code generation complete!
📊 [Tool Result: {'success': True, 'path': 'hello.py', 'size': 837}]
...
[END] streaming_tools (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；流式阶段有连续进度输出，最终 `write_file` 返回成功且路径与大小与日志一致，说明流式工具管线工作正常。

## 适用场景建议

- 生成长代码、长 JSON、大段模板等需要降低首字节延迟或展示进度时使用 `StreamingFunctionTool`
- 与小型校验类工具并用时，保持非流式 `FunctionTool` 即可
