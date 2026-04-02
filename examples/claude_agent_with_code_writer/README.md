# Claude Agent Code Writer 示例

本示例演示如何基于 `ClaudeAgent` 构建一个代码生成助手，利用 Claude-Code 内置工具（Read、Write、Edit 等）根据用户描述自动生成 Python 代码并写入文件。

## 关键特性

- **Claude-Code 内置工具**：使用 Read、Write、Edit、Glob、Grep、TodoWrite 等内置工具，无需自定义工具即可完成文件读写与代码检索
- **模型配置外部化**：通过环境变量配置模型，支持任意 OpenAI 兼容 API
- **Proxy Server 架构**：自动启动 Anthropic Proxy 子进程，将 Claude-Code 的模型请求转发到用户配置的模型服务
- **自主纠错能力**：Agent 遇到写入失败等错误时会自动调整策略重试

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
code_writing_agent (ClaudeAgent)
├── model: OpenAIModel (通过 Anthropic Proxy 转发)
├── tools (Claude-Code 内置):
│   ├── Read
│   ├── Write
│   ├── Edit
│   ├── Glob
│   ├── Grep
│   └── TodoWrite
└── session: InMemorySessionService
```

关键文件：

- [examples/claude_agent_with_code_writer/agent/agent.py](./agent/agent.py)：构建 `ClaudeAgent`、配置内置工具白名单、启动/销毁 Proxy 环境
- [examples/claude_agent_with_code_writer/agent/prompts.py](./agent/prompts.py)：提示词模板
- [examples/claude_agent_with_code_writer/agent/config.py](./agent/config.py)：环境变量读取
- [examples/claude_agent_with_code_writer/run_agent.py](./run_agent.py)：测试入口，执行代码生成对话

## 关键代码解释

这一节用于快速定位"Proxy 环境管理、Agent 组装、事件流处理"三条核心链路。

### 1) Proxy 环境与 Agent 组装（`agent/agent.py`）

- 使用 `setup_claude_env` 启动 Anthropic Proxy 子进程，将 Claude-Code 的模型请求转发至用户配置的 OpenAI 兼容 API
- 通过 `ClaudeAgentOptions(allowed_tools=...)` 配置内置工具白名单（Read、Write、Edit、Glob、Grep、TodoWrite）
- Agent 销毁时调用 `destroy_claude_env` 停止 Proxy 子进程，释放端口资源

### 2) 提示词配置（`agent/prompts.py`）

- 使用简洁的系统提示词 `INSTRUCTION` 定义 Agent 角色为代码编写助手
- 由 `ClaudeAgent` 内部结合 Claude-Code 的工具能力完成代码生成与文件写入

### 3) 流式事件处理与可观测输出（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `function_call`（工具调用）
  - `function_response`（工具返回）
- 资源清理链路：`runner.close()` → `agent.destroy()` → `cleanup_claude()`

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e ".[agent-claude]"
```

安装 Claude Code CLI：

```bash
npm install -g @anthropic-ai/claude-code
```

### 环境变量要求

在 [examples/claude_agent_with_code_writer/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/claude_agent_with_code_writer
python3 run_agent.py
```

## 运行结果（实测）

```text
[2026-04-02 20:34:06][INFO] Proxy server proxy process started (PID: 71296)
[2026-04-02 20:34:06][INFO] Proxy server is ready at http://0.0.0.0:8082
[2026-04-02 20:34:06][INFO] ClaudeAgent event loop thread started
🆔 Session ID: 015b1c3e...
📝 User: Write a Python function that calculates the Fibonacci sequence up to n terms, save it to 'fibonacci.py'.
🤖 Assistant:
🔧 [Tool Call: Write({"file_path": "fibonacci.py", "content": "def fibonacci(n):\n    ..."})]
📊 [Tool Result: Write({"result": "File created successfully at: fibonacci.py"})]
The Python function to calculate the Fibonacci sequence up to `n` terms has been saved to `fibonacci.py`. Here's a summary of the function:

- **Function Name**: `fibonacci(n)`
- **Input**: An integer `n` representing the number of terms.
- **Output**: A list containing the Fibonacci sequence up to `n` terms.
- **Example Usage**: The script includes an example that prints the Fibonacci sequence for `n = 10`.

You can run the script directly to see the output or import the function in another Python script.
----------------------------------------
[2026-04-02 20:34:22][INFO] ClaudeAgent event loop thread stopped
[2026-04-02 20:34:22][INFO] ClaudeAgent thread terminated successfully
[2026-04-02 20:34:22][INFO] Proxy process already stopped.
🧹 Claude environment cleaned up
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：Agent 正确选用 `Write` 工具将生成的代码写入指定文件
- **代码质量良好**：生成的函数包含完整的 docstring、参数校验与示例用法
- **Proxy 转发正常**：Claude-Code 的模型请求通过 Anthropic Proxy 成功转发到用户配置的模型服务
- **资源清理完整**：Agent 运行结束后 Proxy 子进程正确停止，端口释放

## 适用场景建议

- 快速验证 ClaudeAgent + Claude-Code 内置工具链路：适合使用本示例
- 验证 Anthropic Proxy 转发到自定义 OpenAI 兼容模型服务：适合使用本示例
- 需要自定义工具（FunctionTool）而非内置工具：建议使用 `examples/claude_agent`
