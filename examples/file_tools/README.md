# File Tools 文件操作能力示例

本示例演示如何基于 `LlmAgent` 构建一个文件操作助手，并验证 `Read / Write / Edit / Grep / Bash / Glob` 六大文件工具的核心调用链路是否正常工作。

## 关键特性

- **多工具协同能力**：一次性挂载 `ReadTool`、`WriteTool`、`EditTool`、`GrepTool`、`BashTool`、`GlobTool` 六个文件操作工具，覆盖读写、编辑、搜索、命令执行与文件发现
- **工作目录隔离**：所有工具共享同一 `cwd` 参数，文件操作限定在指定工作目录内，避免污染宿主环境
- **流式事件处理**：通过 `runner.run_async(...)` 消费事件流，实时打印工具调用与工具返回
- **自动化测试流程**：程序自动创建测试文件、执行 4 轮典型文件操作对话，并在结束后清理工作目录
- **多轮场景覆盖**：覆盖"读取文件、追加内容、正则搜索、文件发现"四类典型操作

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
file_assistant (LlmAgent)
├── model: OpenAIModel
├── tools:
│   ├── ReadTool(cwd)      — 读取文件内容（带行号）
│   ├── WriteTool(cwd)     — 写入或追加文件
│   ├── EditTool(cwd)      — 替换文件中的文本块
│   ├── GrepTool(cwd)      — 正则搜索文件内容
│   ├── BashTool(cwd)      — 执行 Shell 命令
│   └── GlobTool(cwd)      — 按 Glob 模式发现文件
└── session: InMemorySessionService
```

关键文件：

- [examples/file_tools/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`、挂载六个文件工具、设置工作目录
- [examples/file_tools/agent/prompts.py](./agent/prompts.py)：提示词模板，定义工具使用指南与最佳实践
- [examples/file_tools/agent/config.py](./agent/config.py)：环境变量读取
- [examples/file_tools/run_agent.py](./run_agent.py)：测试入口，执行 4 轮文件操作对话

## 关键代码解释

这一节用于快速定位"工具挂载、工作目录隔离、事件输出"三条核心链路。

### 1) Agent 组装与工具挂载（`agent/agent.py`）

- 使用 `LlmAgent` 组装文件助手，分别实例化 `ReadTool`、`WriteTool`、`EditTool`、`GrepTool`、`BashTool`、`GlobTool`
- 所有工具通过 `cwd=work_dir` 参数共享同一工作目录，确保文件操作的路径隔离
- 若未指定 `work_dir`，默认使用系统临时目录 `$TMPDIR/file_tools_demo`

### 2) 提示词与工具使用指南（`agent/prompts.py`）

- 提示词中明确列举了 6 个可用工具及其用途
- 包含工具使用准则（如"先 Read 再 Edit"、"先 Grep 再修改"等最佳实践）
- 引导模型在执行文件操作时遵循合理的操作顺序

### 3) 测试流程与事件处理（`run_agent.py`）

- 程序启动时在临时目录创建 `test.txt` 和 `config.ini` 两个测试文件
- 依次发送 4 个测试 query：读取文件、追加内容、正则搜索、Glob 文件发现
- 使用 `runner.run_async(...)` 消费事件流，区分并打印：
  - `function_call`（工具调用名称与参数）
  - `function_response`（工具返回结果）
- 执行结束后打印最终文件内容，并自动清理工作目录

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

在 [examples/file_tools/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/file_tools
python3 run_agent.py
```

## 运行结果（实测）

```text
📁 Working directory: /var/folders/_k/n6qn1g751bx9zm2ql16q28580000gn/T/file_tools_demo
✅ Created test files: test.txt, config.ini
🆔 Session ID: adcdc233...
📝 User: Read the content of test.txt
🤖 Assistant:
🔧 [Invoke Tool: Read({'path': 'test.txt'})]
📊 [Tool Result: {'success': True, 'content': '1 | Hello, World!\n2 | This is a test file.\n3 | Line 3', ...}]
The content of `test.txt` is as follows:

1 | Hello, World!
2 | This is a test file.
3 | Line 3
----------------------------------------
🆔 Session ID: a11d493a...
📝 User: Add a new line 'Line 4' to test.txt
🤖 Assistant:
🔧 [Invoke Tool: Write({'path': 'test.txt', 'content': 'Line 4', 'append': True})]
📊 [Tool Result: {'success': True, 'action': 'appended to', 'bytes_written': 6, ...}]
The line "Line 4" has been successfully appended to the file `test.txt`.
----------------------------------------
🆔 Session ID: 3fb49f40...
📝 User: Search for 'test' in all files in the current directory
🤖 Assistant:
🔧 [Invoke Tool: Grep({'pattern': 'test', 'path': '.', 'case_sensitive': False})]
📊 [Tool Result: {'success': True, 'total_matches': 1, 'matches': [('test.txt', [(2, 'This is a test file.')])], ...}]
The search for the term "test" found one match:
  File: test.txt — Line 2: This is a test file.
----------------------------------------
🆔 Session ID: 36afb585...
📝 User: Find all .txt files in the current directory
🤖 Assistant:
🔧 [Invoke Tool: Glob({'pattern': '*.txt'})]
📊 [Tool Result: {'success': True, 'matches': ['test.txt'], 'count': 1, ...}]
There is one `.txt` file found in the current directory: test.txt
----------------------------------------

📄 Final file contents:

--- test.txt ---
Hello, World!
This is a test file.
Line 3
Line 4

✅ File Tools demonstration completed!

🧹 Cleaning up working directory: /var/folders/_k/n6qn1g751bx9zm2ql16q28580000gn/T/file_tools_demo
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **工具路由正确**：读取文件调用 `Read`，追加内容调用 `Write`，文本搜索调用 `Grep`，文件发现调用 `Glob`，每轮均选中了正确的工具
- **工具参数正确**：`Write` 使用 `append=True` 追加而非覆盖，`Grep` 传入 `case_sensitive=False` 实现不区分大小写搜索
- **工具结果被正确消费**：回复内容与工具返回数据一致，能将结构化结果组织为可读答案
- **文件状态一致**：最终文件内容验证了 `Write` 追加操作确实生效（`Line 4` 出现在末尾）
- **能力覆盖完整**：4 轮测试分别覆盖"读取、写入、搜索、发现"四类核心文件操作场景

说明：该示例每轮使用新的 `session_id`，因此主要验证的是工具调用与回复质量，不强调跨轮记忆一致性。

## 适用场景建议

- 快速验证 File Tools 六大工具的调用链路：适合使用本示例
- 验证工作目录隔离与文件操作安全性：适合使用本示例
- 需要测试多 Agent 协作或复杂工作流：建议使用 `examples/dsl` 下的相关示例
