# 流式工具综合回归示例

本示例演示 `StreamingFunctionTool` 在多场景下的行为：同步/异步函数、FunctionTool 包装、自定义流式 `BaseTool`、`ToolSet` 与混合工具配置等，用于验证流式参数事件与最终执行结果。

## 关键特性

- 覆盖多组独立测试用例，每组对应一种工具形态或组合方式
- 校验流式工具调用过程中参数分片输出（`[Streaming]`）与工具完成事件
- 与真实 LLM 交互驱动工具选择与参数生成

## Agent 层级结构说明

本例以单 `LlmAgent` 为主，按测试用例切换工具集；无固定多 Agent 编排。

```text
per-test LlmAgent
└── tools: 随用例切换（write_file / async_write_file / ToolSet / 自定义流式工具等）
```

关键文件：

- [examples/llmagent_with_streaming_tool_complex/run_agent.py](./run_agent.py)
- [examples/llmagent_with_streaming_tool_complex/.env](./.env)

## 关键代码解释

- 用例循环内为不同场景注册 `StreamingFunctionTool` 或混合 `ToolSet`，并打印流式片段
- 通过 `Runner` + `InMemorySessionService` 执行，观察工具参数逐步到达与执行完成

## 环境与运行

### 环境要求

- Python 3.10+（推荐 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/llmagent_with_streaming_tool_complex/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_streaming_tool_complex
python3 run_agent.py
```

## 运行结果（实测）


```text
[START] llmagent_with_streaming_tool_complex
Comprehensive Streaming Tool Test Suite
🧪 TEST: Test 1: Sync function -> StreamingFunctionTool
  ⏳ [Streaming] write_file: {"
  ⏳ [Streaming] write_file: path":"
  ⏳ [Streaming] write_file: test.txt","
  ✅ [Tool Complete] write_file
  📊 [Result] tool_type=sync_function
...
[END] llmagent_with_streaming_tool_complex (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 日志展示多组流式工具测试与 `[Streaming]` 分片输出
- 收尾为 `exit_code=0`，与本批 `error.txt` 为空一致

## 适用场景建议

- 需要验证流式工具参数管线是否按模型输出逐步触发的场景
- 需要将普通函数、`FunctionTool`、自定义工具统一接入流式能力的集成测试
