# MemPalace MCP 接入示例

本示例演示如何让 **tRPC-Agent** 通过 **MCP (Model Context Protocol)** 直接调用
[MemPalace](https://github.com/MemPalace/mempalace) 官方提供的 MCP 服务端，使用
其 30 个原生工具能力（palace 读写、知识图谱、agent diary、跨 wing 导航等）。

跟 [`examples/memory_service_with_mempalace`](../memory_service_with_mempalace)
的区别：

| 接入方式 | 谁触发记忆操作 | 适合场景 |
| --- | --- | --- |
| `MempalaceMemoryService`（隐式） | Runner 自动 store / 模型通过 `load_memory_tool` 检索 | 跨 session 长期记忆，对话自动归档 |
| **本示例：MCP toolset（显式）** | 模型按需调用 MemPalace MCP 工具 | 需要细粒度控制：搜索、归档、知识图谱、agent 日记 |

两种方式可以叠加使用，互不冲突。

---

## 工作原理

```
LlmAgent --stdio--> mempalace mcp (子进程, MCP server)
                       └─ ChromaDB (本地 palace)
                       └─ SQLite (knowledge graph)
```

- `MempalaceMCPToolset` 继承自 `trpc_agent_sdk.tools.MCPToolset`，用
  `StdioConnectionParams` 在启动时把 `mempalace mcp` 作为子进程拉起。
- 服务端通过 stdio 暴露 ~30 个 MCP 工具，trpc-agent 自动转换为 `LlmAgent` 的工具
  声明，模型即可用函数调用语法触发。
- 数据落在本地 MemPalace（默认 `~/.mempalace/palace`），全程零云端调用。

工具详细参数参考 MemPalace 官方文档：[MCP Tools Reference](https://mempalaceofficial.com/reference/mcp-tools)。

---

## 准备工作

### 1. 安装依赖

在仓库根目录：

```bash
pip install -e ".[mempalace]"
```

`mempalace` 包会带上 `mempalace` CLI 命令到当前 Python 环境的 PATH。

### 2. 初始化 palace（首次使用）

```bash
mempalace init
```

如需自定义存储路径，可以指定一个目录：

```bash
export MEMPALACE_PALACE_PATH=/absolute/path/to/palace
mempalace --palace "$MEMPALACE_PALACE_PATH" init
```

### 3. 配置模型 key

复制并填写 `.env`：

```env
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=your-base-url
TRPC_AGENT_MODEL_NAME=your-model-name
# MEMPALACE_PALACE_PATH=/absolute/path/to/palace  # 可选
```

---

## 启动 MemPalace MCP Server

> ⚠️ **重要**：`mempalace mcp`（带空格）**不是** MCP server，它只是打印设置帮助。  
> 真正的 server 入口是 `mempalace-mcp`（带连字符）或 `python -m mempalace.mcp_server`。

MemPalace MCP server 有 **3 种启动方式**，本示例使用第 1 种，**完全无需手动操作**：

### 方式 1：自动 stdio 子进程（本示例采用，推荐）

`MempalaceMCPToolset` 在 `LlmAgent` 启动时**自动**把 server 作为子进程拉起，通过
stdin/stdout 与之通信；`Runner` 关闭时子进程也跟着退出。**你什么都不用做，跑 `python3 run_agent.py` 即可。**

[`agent/tools.py`](agent/tools.py) 优先用模块直跑，避免依赖 CLI shim 的命名：

```python
# 等价于在 shell 里执行：
#   python -m mempalace.mcp_server [--palace /path/to/palace]
McpStdioServerParameters(
    command=sys.executable,
    args=["-m", "mempalace.mcp_server", *(["--palace", palace_path] if palace_path else [])],
    env=env,
)
```

如果你环境里只有 `mempalace-mcp` 这个二进制（没有装 Python 模块），上面的代码会自动回退到：

```python
McpStdioServerParameters(command="mempalace-mcp", args=[...], env=env)
```

### 方式 2：手动启动 stdio server（用于调试）

要确认 MemPalace MCP server 本身可用，先在终端单独跑一下：

```bash
# 推荐写法：直接跑模块
python -m mempalace.mcp_server

# 自定义 palace 路径：
python -m mempalace.mcp_server --palace /absolute/path/to/palace

# 如果你的环境装了 CLI shim 也可以：
mempalace-mcp
mempalace-mcp --palace /absolute/path/to/palace
```

server 启动后会在 stdout 上**安静地等待** JSON-RPC 消息——看不到任何 banner 才是对的，
stdio 协议要求 stdout 纯净，否则 MCP 客户端会无法解析。  
用 `Ctrl+C` 结束即可。

**如何区分**：

| 命令 | 行为 |
|---|---|
| `mempalace mcp`（带空格） | ❌ 只打印帮助文本，**不是** server |
| `mempalace-mcp`（带连字符） | ✅ 真正启动 stdio server |
| `python -m mempalace.mcp_server` | ✅ 真正启动 stdio server（最稳） |

### 方式 3：作为常驻 HTTP server（多 agent 共享同一 palace）

如果你希望多个 agent 共享同一个 MemPalace，可以让 MCP server 跑成 HTTP 服务（具体
CLI 选项请参考 MemPalace 官方文档当前版本：[mempalace mcp](https://mempalaceofficial.com/reference/cli)）。然后把
`MempalaceMCPToolset` 改为使用 `StreamableHTTPConnectionParams` 连接已存在的 server：

```python
from trpc_agent_sdk.tools import StreamableHTTPConnectionParams

self._connection_params = StreamableHTTPConnectionParams(
    url="http://localhost:8000/mcp",
    timeout=5,
    sse_read_timeout=60 * 5,
    terminate_on_close=False,   # 不关闭外部 server
)
```

参考 [`examples/mcp_tools/agent/tools.py`](../mcp_tools/agent/tools.py) 里
`SseMCPToolset` / `StreamableHttpMCPToolset` 的写法。

---

## 运行示例

```bash
cd examples/mempalace_mcp
python3 run_agent.py
```

示例会跑 7 轮独立 session，逐步触发以下 MCP 工具：

| 轮次 | 用户提问 | 触发的 MCP 工具（典型） |
| --- | --- | --- |
| 1 | 让 agent 给出 palace 总览 | `mempalace_status` |
| 2 | 让 agent 记住一条偏好 | `mempalace_add_drawer` |
| 3 | 问 agent 自己的工作习惯 | `mempalace_search` |
| 4 | 写入一条三元组事实 | `mempalace_kg_add` |
| 5 | 查询 Alice 的相关关系 | `mempalace_kg_query` |
| 6 | 让 agent 写日记 | `mempalace_diary_write` |
| 7 | 读回最近的日记 | `mempalace_diary_read` |

> 工具的实际调用顺序由模型决定，提示语只是引导。

---

## 关键代码

`agent/tools.py` —— 把 MemPalace MCP server 包装成 trpc-agent 的 `MCPToolset`：

```python
class MempalaceMCPToolset(MCPToolset):
    def __init__(self, palace_path=None, tool_filter=_DEFAULT_TOOL_FILTER):
        super().__init__()
        env = os.environ.copy()
        if palace_path:
            env["MEMPALACE_PALACE_PATH"] = palace_path
        self._connection_params = StdioConnectionParams(
            server_params=McpStdioServerParameters(
                command="mempalace",
                args=["mcp"],
                env=env,
            ),
            timeout=10,
        )
        if tool_filter is not None:
            self._tool_filter = tool_filter
```

`agent/agent.py` —— 把 toolset 挂到 `LlmAgent`：

```python
def create_agent() -> LlmAgent:
    palace_path = os.getenv("MEMPALACE_PALACE_PATH") or None
    return LlmAgent(
        name="mempalace_assistant",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[MempalaceMCPToolset(palace_path=palace_path)],
    )
```

---

## 自定义

### 暴露全部 30 个工具

默认只暴露 9 个高频工具以节省模型上下文。要解锁全部：

```python
MempalaceMCPToolset(tool_filter=None)
```

### 只暴露知识图谱相关工具

```python
MempalaceMCPToolset(
    tool_filter=[
        "mempalace_kg_add",
        "mempalace_kg_query",
        "mempalace_kg_invalidate",
        "mempalace_kg_timeline",
        "mempalace_kg_stats",
    ],
)
```

### 改用其他传输方式

如果你希望 MCP server 作为独立 HTTP 服务运行（而不是子进程），可以参考
[`examples/mcp_tools/agent/tools.py`](../mcp_tools/agent/tools.py) 里的
`SseConnectionParams` / `StreamableHTTPConnectionParams` 模式自行替换。

---

## 故障排查

- **`mempalace: command not found`**：未安装或装到了不同 Python 环境。  
  解决：`pip install -e ".[mempalace]"`，或确保运行 `python3 run_agent.py` 用的是
  同一个解释器。

- **想先确认 MCP server 本身能起来**：在终端单独跑一下
  ```bash
  python -m mempalace.mcp_server
  ```
  正常情况下进程会**挂起且不输出任何内容**（stdio 协议要求 stdout 纯净），
  `Ctrl+C` 退出即可。如果立刻报错或退出，说明 MemPalace 自身环境有问题（缺少
  模型文件、palace 未初始化等）。
  注意：**不要**用 `mempalace mcp`（带空格）做预检，那个命令只是打印帮助文本。

- **MCP 启动超时**：palace 第一次初始化、加载 embedding 模型会较慢。  
  解决：先在终端跑一次 `mempalace status` 让 embedding 模型预热，再启动 demo；或在
  `agent/tools.py` 里把 `StdioConnectionParams(timeout=10)` 调大。

- **工具被模型忽略 / 不调用**：模型可能更倾向于直接回答。  
  解决：在 `.env` 切换到更强的模型，或者在 prompt 里加更明确的工具触发暗示。

---

## 相关链接

- MemPalace 官方文档：[mempalaceofficial.com](https://mempalaceofficial.com/)
- MCP 工具完整列表：[MCP Tools Reference](https://mempalaceofficial.com/reference/mcp-tools)
- MemPalace 集成介绍（含隐式 Memory Service 路径）：[`docs/mkdocs/zh/mempalace.md`](../../docs/mkdocs/zh/mempalace.md)
- tRPC-Agent 通用 MCP 示例：[`examples/mcp_tools/`](../mcp_tools/)
