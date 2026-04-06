# 流式工具参数演示示例

本示例演示单次对话中工具调用参数如何以流式方式逐步到达，并完成写文件类工具执行。

## 关键特性

- 单会话下单次用户请求触发流式工具参数
- 控制台可观察参数 JSON 分片与工具完成事件
- 工具执行结果以结构化字段回显（如写入字节数）

## Agent 层级结构说明

```text
root_agent (LlmAgent)
└── tools: write_file（StreamingFunctionTool）
```

关键文件：

- [examples/llmagent_with_streaming_tool_simple/run_agent.py](./run_agent.py)
- [examples/llmagent_with_streaming_tool_simple/.env](./.env)

## 关键代码解释

- 使用 `Runner` 驱动 Agent，用户消息请求创建 HTML 文件
- 模型生成工具参数时触发流式事件，最终合并执行模拟写文件

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/llmagent_with_streaming_tool_simple/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_streaming_tool_simple
python3 run_agent.py
```

## 运行结果（实测）


```text
Streaming Tool Call Arguments Demo
📝 User: 请帮我创建一个简单的 HTML 页面，文件名为 index.html...
⏳ [Streaming] ...-write_file: {"
⏳ [Streaming] ...-write_file: path":"index
...
✅ [Tool Call Complete] write_file
📊 [Tool Result] {'success': True, 'path': 'index.html', 'bytes_written': 360, ...}
[END] llmagent_with_streaming_tool_simple (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 出现流式参数片段、`Tool Call Complete` 与 `Tool Result` 成功字段
- `exit_code=0`，`error.txt` 为空

## 适用场景建议

- 需要在 UI 或日志中实时展示“模型正在填写工具参数”的产品原型
- 调试大参数工具（长文本、代码）时的流式消费验证
