# 代码执行器基础能力示例

本示例演示如何基于 `LlmAgent` 内置代码执行器快速构建一个代码执行助手，并验证 `Prompt + Code Execution + Session` 的核心链路是否正常工作。

## 关键特性

- **Python 代码执行**：Agent 自动生成 Python 代码并执行，返回运行结果
- **双执行器可选**：内置 `UnsafeLocalCodeExecutor`（本地，无需 Docker）与 `ContainerCodeExecutor`（容器隔离，需 Docker）
- **数学计算与数据处理**：支持算术运算、列表处理、函数定义与执行等场景
- **流式事件处理**：通过 `runner.run_async(...)` 处理 partial/full event，实时展示代码生成与执行过程
- **多轮测试覆盖**：同一程序内覆盖"算术运算 + 列表处理 + 函数执行"三类典型问法

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
code_assistant (LlmAgent)
├── model: OpenAIModel
├── code_executor:
│   ├── UnsafeLocalCodeExecutor (默认，本地执行，timeout=10)
│   └── ContainerCodeExecutor   (可选，容器隔离，image=python:3-slim)
└── session: InMemorySessionService (state 注入 user_name / user_city)
```

关键文件：

- `examples/code_executors/agent/agent.py`：构建 `LlmAgent`、创建代码执行器、设置生成参数
- `examples/code_executors/agent/prompts.py`：提示词模板，引导模型输出可执行代码块
- `examples/code_executors/agent/config.py`：环境变量读取
- `examples/code_executors/run_agent.py`：测试入口，执行 3 轮对话

## 关键代码解释

这一节用于快速定位"代码执行器创建、Agent 组装、事件输出"三条核心链路。

### 1) 代码执行器创建与 Agent 组装（`agent/agent.py`）

- 使用 `_create_code_executor()` 创建执行器，默认使用 `UnsafeLocalCodeExecutor(timeout=10)`
- 可通过参数切换为 `ContainerCodeExecutor(image="python:3-slim", error_retry_attempts=1)`
- 使用 `LlmAgent` 组装代码执行助手，通过 `code_executor=executor` 挂载执行器
- 使用统一的提示词模板 `INSTRUCTION`，引导模型以 ` ```python ` 代码块输出可执行代码

### 2) 提示词设计（`agent/prompts.py`）

- 提示词定义 Agent 的代码执行能力边界，引导模型在需要计算或数据处理时生成 Python 代码
- 要求模型使用 ` ```python ` 代码块输出代码，并通过 `print` 输出结果
- 执行完成后由模型整理结果并回复用户

### 3) 流式事件处理与可观测输出（`run_agent.py`）

- 使用 `runner.run_async(...)` 消费事件流
- `event.partial=True` 时打印文本分片
- 完整事件中区分并打印：
  - `executable_code`（生成的可执行代码）
  - `code_execution_result`（代码执行结果）
  - `function_call`（工具调用）

## 环境与运行

### 环境要求

- Python 3.12
- 若使用 `ContainerCodeExecutor`，需安装 Docker 并确保 Docker daemon 正在运行

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 `examples/code_executors/.env` 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/code_executors
python3 run_agent.py
```

## 运行结果（实测）

````text
============================================================
🚀 TRPC Agent Code Executor Quickstart Example
============================================================

🤖 Agent: code_assistant
🔧 Code Executor: UnsafeLocalCodeExecutor
🆔 Session ID: 4fd23a5a...
📝 User: Calculate 15 + 27 * 3
🤖 Assistant: ```python
result = 15 + 27 * 3
print(result)
```
💻 [Executable Code]
```python
result = 15 + 27 * 3
print(result)
```

✅ [Code Execution Result]
```
Code execution result:
96


```
96
----------------------------------------
🆔 Session ID: f503f54a...
📝 User: Generate a list of numbers from 1 to 10 and calculate the sum of their squares
🤖 Assistant: I'll generate a list of numbers from 1 to 10 and calculate the sum of their squares. Here's the code:

```python
numbers = list(range(1, 11))
squared_numbers = [x**2 for x in numbers]
sum_of_squares = sum(squared_numbers)

print("Numbers:", numbers)
print("Squared numbers:", squared_numbers)
print("Sum of squares:", sum_of_squares)
```

Numbers: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
Squared numbers: [1, 4, 9, 16, 25, 36, 49, 64, 81, 100]
Sum of squares: 385
💻 [Executable Code]
```python
numbers = list(range(1, 11))
squared_numbers = [x**2 for x in numbers]
sum_of_squares = sum(squared_numbers)

print("Numbers:", numbers)
print("Squared numbers:", squared_numbers)
print("Sum of squares:", sum_of_squares)
```

✅ [Code Execution Result]
```
Code execution result:
Numbers: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
Squared numbers: [1, 4, 9, 16, 25, 36, 49, 64, 81, 100]
Sum of squares: 385


```
The sum of squares from 1 to 10 is 385.
----------------------------------------
🆔 Session ID: 998d4c1b...
📝 User: Write a Python function to calculate the factorial of 5 and execute it
🤖 Assistant: ```python
def factorial(n):
    if n == 0 or n == 1:
        return 1
    else:
        return n * factorial(n - 1)

result = factorial(5)
print(result)
```
💻 [Executable Code]
```python
def factorial(n):
    if n == 0 or n == 1:
        return 1
    else:
        return n * factorial(n - 1)

result = factorial(5)
print(result)
```

✅ [Code Execution Result]
```
Code execution result:
120


```
The factorial of 5 is 120.
----------------------------------------
````

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **代码生成正确**：模型针对不同问题生成了正确的 Python 代码，语法无误且逻辑清晰
- **代码执行正确**：三轮执行结果均与预期一致（96、385、120）
- **执行结果被正确消费**：模型能够读取代码执行结果，并组织为可读的自然语言回复
- **能力覆盖完整**：3 轮测试分别覆盖"算术运算、列表数据处理、函数定义与执行"三类核心场景

说明：该示例每轮使用新的 `session_id`，因此主要验证的是代码生成与执行能力，不强调跨轮记忆一致性。

## 适用场景建议

- 快速验证单 Agent + Code Execution 主链路：适合使用本示例
- 需要本地调试且无 Docker 环境：使用默认的 `UnsafeLocalCodeExecutor`
- 需要安全隔离的代码执行环境：切换为 `ContainerCodeExecutor`（需 Docker）
- 需要测试多 Agent 分支隔离行为：建议使用 `examples/llmagent_with_branch_filtering`
