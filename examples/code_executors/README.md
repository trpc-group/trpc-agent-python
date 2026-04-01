# 🚀 代码执行器 Quickstart ↔ tRPC-Agent-Python

基于 `tRPC-Agent-Python` 内置代码执行器的快速入门示例，演示如何让 Agent 生成并执行 Python 代码来完成计算与数据处理任务。
本文档完全基于示例目录 `examples/code_executors/` 讲解。

## ✨ 能力概览

- 🐍 **Python 代码执行**：Agent 自动生成 Python 代码并执行，返回运行结果
- 🔧 **双执行器可选**：内置 `UnsafeLocalCodeExecutor`（本地，无需 Docker）与 `ContainerCodeExecutor`（容器隔离，需 Docker）
- 🧮 **数学计算**：支持算术运算、数据处理、函数定义与执行等场景
- 🌊 **流式输出**：支持流式事件回调，实时展示代码生成与执行过程
- ⚙️ **开箱即用**：最少配置即可运行，适合快速验证与二次开发

---

## 🛞 前置准备

- Python 3.10+
- 已安装 `trpc-agent` 包
- 若使用 `ContainerCodeExecutor`，需安装 Docker 并确保 Docker daemon 正在运行
- 模型服务可访问（配置 API Key、Base URL、Model Name）

---

## 🚀 快速开始

### 1) 安装依赖

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

若需使用容器执行器，请确保本机已安装并启动 Docker。

### 2) 配置环境变量

参考 `examples/code_executors/.env`：

```bash
TRPC_AGENT_API_KEY=your-model-api-key
TRPC_AGENT_BASE_URL=http://your-model-endpoint
TRPC_AGENT_MODEL_NAME=your-model-name
```

示例入口会自动加载 `.env`（`run_agent.py`）：

```python
from dotenv import load_dotenv
load_dotenv()
```

### 3) 运行示例

```bash
python3 examples/code_executors/run_agent.py
```

示例会依次发起三段请求：
- 算术运算：`Calculate 15 + 27 * 3`
- 列表处理：生成 1~10 的列表并计算平方和
- 函数执行：编写并执行计算 5 阶乘的函数

---

## ⚙️ 关键配置项

### UnsafeLocalCodeExecutor

在当前进程上下文中直接执行代码，适合本地调试。

| 配置项 | 类型 | 说明 | 示例值 |
|---|---|---|---|
| `timeout` | `float` | 单次执行超时（秒），`0` 表示不限制 | `10` |

### ContainerCodeExecutor

在 Docker 容器中隔离执行代码，适合生产环境。

| 配置项 | 类型 | 说明 | 示例值 |
|---|---|---|---|
| `image` | `str` | Docker 镜像名称 | `"python:3-slim"` |
| `error_retry_attempts` | `int` | 执行失败时的重试次数 | `1` |

切换执行器类型只需修改 `agent/agent.py` 中的调用参数：

```python
# 本地执行（默认）
executor = _create_code_executor(code_executor_type="unsafe_local")

# 容器执行
executor = _create_code_executor(code_executor_type="container")
```

---

## 📝 按示例讲解执行流程

### A. Agent 如何接入代码执行器

`agent/agent.py` 构造执行器并创建 Agent：

```python
executor = _create_code_executor()   # 默认使用 UnsafeLocalCodeExecutor
agent = LlmAgent(
    name="code_assistant",
    description="代码执行助手",
    model=_create_model(),
    instruction=INSTRUCTION,
    code_executor=executor,          # 挂载代码执行器
)
```

`agent/prompts.py` 定义 Agent 指令，引导模型用 ` ```python ` 代码块输出可执行代码。

### B. run_agent.py 做了什么

1. 加载 `.env` 环境变量
2. 创建 `Runner` 与 `InMemorySessionService`
3. 依次发送三条 demo query，每条使用独立 session
4. 流式打印事件：代码块（`executable_code`）、执行结果（`code_execution_result`）、工具调用（`function_call`）

### C. 一次代码执行的完整链路

```
用户提问 → 模型生成 Python 代码块 → CodeExecutor 执行代码 → 返回 stdout → 模型整理结果 → 回复用户
```

---
