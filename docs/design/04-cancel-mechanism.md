# trpc-agent-python Agent Cancel 机制设计

> **结论摘要**：Cancel 采用协作式取消：Agent 在 checkpoint 调 `ctx.raise_if_cancelled()`，Runner 通过 `_RunCancellationManager` 的本地 `asyncio.Event` 传递取消信号，命中后抛 `RunCancelledException`，Runner 捕获后做会话清理并产出 `AgentCancelledEvent`。单节点部署下，这套机制已经完整；多节点部署下，cancel 请求可能落到非运行节点，因此增加 `__trpc_agent_cancel` 共享标志：`run_async` 开始写 `False`、结束删除，`cancel_run_async` 检查标志存在后写 `True`，运行节点的 watchdog 读到 `True` 就设置本地 Event。分布式部署下所有 Runner 统一开启 `enable_distributed_cancel`，信号最终仍转换成同一个本地 Event，checkpoint、会话清理和事件链路完全复用。

## 1. 背景与目标

### 1.1 Cancel 要解决的问题

Agent 的一轮执行可能很长：LLM 流式输出持续几十秒，工具调用可能等待外部服务，TeamAgent 还要在 Leader 和 Member 之间来回切换。用户看到输出不符合预期时，通常希望：

1. 立刻停止当前执行，不再继续消耗 token 和工具调用；
2. 保留已经输出的 partial text，方便下一轮指出哪里不满意；
3. 清理没有完成的 function call，避免下一轮会话出现“只有 call 没有 response”的脏数据；
4. 通过一个明确的事件告诉客户端“这一轮是被取消的”，而不是连接断开或普通错误。

Cancel 机制就是为这个场景设计的。它不强行杀线程或杀进程，而是在 Agent 的安全点（checkpoint）检查取消信号；命中后抛 `RunCancelledException`，由 Runner 统一做后置处理。

用户使用指南见 `docs/mkdocs/zh/cancel.md`；本文侧重机制设计，把单节点和分布式两种形态讲清楚。

### 1.2 两种部署形态

| 形态 | 取消信号存放 | 适用场景 | 关键限制 |
|---|---|---|---|
| 单节点 | 进程内 `_RunCancellationManager` 的 `asyncio.Event` | 单实例部署、SSE/WebSocket 长连接、连接断开触发取消 | cancel 请求必须落到运行 Agent 的同一节点 |
| 多节点 | 共享 `SessionService` 的 `__trpc_agent_cancel` 标志 | Redis / Redis Cluster / SQL 共享会话存储的多实例部署 | 需要轮询，取消延迟受 `cancel_poll_interval` 影响 |

单节点机制是基础；分布式机制只解决“信号怎么从 A 节点送到 B 节点”，不改变取消本身的执行路径。

### 1.3 目标

- **协作式取消**：在 checkpoint 停止，不破坏 Agent 的会话状态。
- **保留部分结果**：LLM 流式文本、已完成的工具结果尽量保留。
- **会话自洽**：清理未完成的 function call，追加取消提示。
- **Agent 无感接入**：框架内置 Agent 已经埋好 checkpoint；自定义 Agent 只需调用 `ctx.raise_if_cancelled()`。
- **多节点可取消**：任意节点收到 cancel 请求都能送到运行节点，不再依赖 A2A hash 寻址。
- **默认不影响单节点**：`enable_distributed_cancel=False` 时保持现有行为。
- **分布式部署统一配置**：使用分布式取消时，执行侧和取消侧的 Runner 都开启 `enable_distributed_cancel`；设计只讨论这个场景。

### 1.4 非目标

- 不强行中断正在执行的工具调用；工具结束后在下一个 checkpoint 生效。
- 不支持同一个 session 上多个并发活跃 run；现有 Manager 也是按 session 跟踪单 run。
- 不做“精确取消用户点击时那一轮”的 per-turn fencing；cancel 的语义是“取消当前活跃的那一轮”。
- 不引入 Redis Pub/Sub、etcd、消息队列等新依赖；v1 用轮询。
- 不替代服务层的认证、鉴权和任务状态机。
- 不做按用户、按 app 的批量取消。

## 2. 单节点 Cancel 机制

### 2.1 总体架构

单节点 cancel 由四个部分组成：

| 组件 | 位置 | 职责 |
|---|---|---|
| `_RunCancellationManager` | `cancel/_cancel.py:29` | 进程级单例，维护 `SessionKey -> asyncio.Event` 和等待清理的 `cleanup_event` |
| `Runner.run_async()` | `runners.py:365` | 每轮执行开始时 `register_run()`，结束时 `cleanup_run()` |
| `Runner.cancel_run_async()` | `runners.py:301` | 设置本地 Event，等待 `cleanup_event` 或超时 |
| `InvocationContext.raise_if_cancelled()` | `context/_invocation_context.py:381` | Agent 在 checkpoint 检查本地 Event，命中后抛 `RunCancelledException` |

`SessionKey` 是 `(app_name, user_id, session_id)` 三元组。Manager 内部有两个字典：

```text
_cancelled:      SessionKey -> asyncio.Event   # 取消信号
_cleanup_events: SessionKey -> asyncio.Event   # 通知 cancel_run_async 清理完成
```

整体架构：

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Runner / _RunCancellationManager（同一进程）                          │
│                                                                     │
│  run_async()                              cancel_run_async()       │
│      │                                          │                   │
│      │ register_run(SessionKey)                 │ cancel_run(...)    │
│      ▼                                          ▼                   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ _RunCancellationManager                                      │   │
│  │                                                              │   │
│  │ _cancelled:      SessionKey -> asyncio.Event                 │   │
│  │ _cleanup_events: SessionKey -> asyncio.Event                 │   │
│  └──────────────────────────────────────────────────────────────┘   │
│      │                                          │                   │
│      │ Agent 在 checkpoint 查 Event              │ 等待 cleanup_event │
│      ▼                                          ▼                   │
│  raise_if_cancelled()                     cleanup_run() 信号         │
│      │                                                              │
│      ▼                                                              │
│  RunCancelledException                                              │
│      │                                                              │
│      ▼                                                              │
│  会话清理 + AgentCancelledEvent                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Run 生命周期与取消时序

一次 run 的生命周期：

```text
run_async() 开始
    │
    ├─ 创建/加载 session
    ├─ 追加 user message
    ├─ register_run() ──► 创建本地 Event
    │
    ▼
Agent Loop
    │
    ├─ checkpoint：raise_if_cancelled()
    │       │
    │       ├─ Event 未 set → 继续
    │       └─ Event set    → RunCancelledException
    │
    ├─ 正常结束 ──────────────────────────────┐
    │                                        │
    └─ RunCancelledException ──► 取消清理 ───┤
                                             ▼
                                        finally:
                                           cleanup_run()
```

取消时序：

```text
 Client              Runner.cancel_run_async()          Agent Loop
   │                          │                             │
   │ cancel_run_async(...)    │                             │
   ├─────────────────────────►│                             │
   │                          │ cancel_run(SessionKey)      │
   │                          ├──────────────┐              │
   │                          │ Event.set()  │              │
   │                          │◄─────────────┘              │
   │                          │                             │
   │                          │ 等待 cleanup_event           │
   │                          │                             │
   │                          │        checkpoint 检查到 Event
   │                          │                  │           │
   │                          │                  ▼           │
   │                          │          RunCancelledException
   │                          │                  │           │
   │                          │                  ▼           │
   │                          │          会话清理 + AgentCancelledEvent
   │                          │                  │           │
   │                          │◄─────────────────┘           │
   │                          │ cleanup_run() signals        │
   │◄─────────────────────────┤ 返回 True                    │
   │                          │                             │
```

`cancel_run_async()` 的返回值：

- `True`：本地找到了活跃 run，Event 已设置（等待清理超时也返回 `True`，只打 warning）。
- `False`：本地没有找到活跃 run。
- 它不保证 Agent 已经在 `timeout` 内停止；最终是否取消成功以 `AgentCancelledEvent` 为准。

### 2.3 Checkpoint：Agent 在哪里响应取消

Cancel 是协作式的。框架内置 Agent 在关键位置调 `ctx.raise_if_cancelled()`：

| Agent | Checkpoint 位置 |
|---|---|
| `LlmAgent` | 每轮循环开始、LLM 调用前、流式输出每个 chunk 后、工具执行前后 |
| `LangGraphAgent` | 图节点执行前后、流式输出期间 |
| `ClaudeAgent` | Claude SDK 流式输出期间；取消时终止子进程 |
| `TrpcRemoteA2aAgent` | HTTP 流式输出期间；同时等待本地 cancel Event |
| `ChainAgent` | 子 Agent 抛出 `RunCancelledException` 后向上传播 |
| `ParallelAgent` | 任一子 Agent 抛异常后取消其它 task |
| `CycleAgent` | 子 Agent 抛出后向上传播 |
| `TeamAgent` | Leader 规划、Member 执行期间均可取消 |
| `dsl/graph` | 节点执行、边遍历前后 |

LlmAgent 的 checkpoint 分布：

```text
while 还有下一轮：
    │
    ├─ CHECKPOINT 1：循环开始
    │
    ├─ 构建 LLM request
    ├─ CHECKPOINT 2：LLM 调用前
    │
    ├─ 调用 LLM，流式输出
    │      │
    │      └─ 每处理一个 event ──► CHECKPOINT 3
    │
    ├─ CHECKPOINT 4：工具执行前
    │
    ├─ 执行工具
    │      │
    │      └─ 每个工具结束后 ──► CHECKPOINT 5
    │
    └─ CHECKPOINT 6：工具执行后、下一轮前
```

自定义 Agent 接入 cancel 很简单：在安全点调用 `await ctx.raise_if_cancelled()`。如果自定义 Agent 有并发等待（比如等 HTTP 流），还可以用 `ctx.get_cancel_event()` 拿到本地 Event，和等待 task 一起 `asyncio.wait()`。

**工具执行期间的语义**：工具本身不会被强制中断。取消信号在工具结束后到达下一个 checkpoint 生效；本次工具的结果会保留，后续未执行的工具调用会被跳过并从会话中清理。`ClaudeAgent` 是例外：它使用子进程模式，取消时会终止子进程。

### 2.4 取消后的会话清理

`Runner.run_async()` 捕获 `RunCancelledException` 后，会调用 `handle_cancellation_session_cleanup()`（`cancel/_session_utils.py:68`）修复会话；修复完成后，Runner 再 yield `AgentCancelledEvent` 通知客户端。

会话清理分两种场景：

**场景 A：LLM 流式输出期间取消**

```text
已输出： "人工智能的发展可以分为三个阶段……"
           │
           ▼
取消事件： "人工智能的发展可以分为三个阶段……\n\n
           Detect user cancel the agent execution."
```

- Runner 在流式过程中把每个 partial event 的文本累积到 `temp_text_parts`；partial event 本身不写入 session。
- 取消时把累计文本拼上 `_CANCELING_SUFFIX`，作为一个完整 Event 写入 session。
- 下一轮对话，模型能看到用户打断前已经生成的内容。

**场景 B：工具执行期间或两轮之间取消**

```text
Agent 计划： [tool_1] [tool_2]
                │
                ├─ tool_1 执行完成，结果保留
                ├─ 取消信号到达
                ├─ tool_2 不再执行
                └─ 会话中移除 tool_2 的 function_call
                   追加 "Detect user cancel the agent execution."
```

- `cleanup_incomplete_function_calls()`（`cancel/_session_utils.py:24`）收集所有 `function_response.id`，把没有对应 response 的 `function_call` 从事件里删掉。当前 `handle_cancellation_session_cleanup()` 调用它时不传 `invocation_id`，所以会扫描整个 session。
- 追加取消提示 Event，让模型知道这一轮被用户打断了。
- 下一轮模型能感知 tool_2 没有调用，可能会重新调用。

清理完成后，Runner 还会：

- `session_service.update_session(session)` 持久化会话；
- `trace_cancellation(...)` 记录 telemetry；
- yield `AgentCancelledEvent(invocation_id, author, reason, branch)`，其中 `error_code="run_cancelled"`，`error_message` 是取消原因。

### 2.5 服务层如何触发取消

**A2A 服务**

```text
Client                  TrpcA2aAgentService              Runner
  │                            │                          │
  │ cancel_task                │                          │
  ├───────────────────────────►│                          │
  │                            │ cancel_run_async(        │
  │                            │   user_id, session_id,   │
  │                            │   timeout=...)           │
  │                            ├─────────────────────────►│
  │                            │                          │ 设置 Event
  │                            │◄─────────────────────────┤ 返回 True/False
  │◄───────────────────────────┤                          │
```

- A2A executor 的 `cancel()` 调 `runner.cancel_run_async(user_id, session_id, timeout=cancel_wait_timeout)`。
- `cancel_wait_timeout` 默认 `1.0` 秒。
- 单节点部署下，`cancel_task` 必须路由到运行 Agent 的节点，所以需要 hash 寻址。

**AG-UI 服务**

```text
Client                  AgUiAgent                       Runner / Manager
  │ abortRun()                 │                          │
  │ 关闭 SSE 连接               │                          │
  ├───────────────────────────►│                          │
  │                            │ 检测到连接断开             │
  │                            │ cancel.cancel_run(...)   │
  │                            ├─────────────────────────►│
  │                            │ 等待 cleanup_event        │
  │                            │ 清理 pending tool calls   │
  │                            │ 取消 execution task       │
  │◄───────────────────────────┤                          │
```

- AG-UI 协议本身没有 cancel 接口，客户端通过 `abortRun()` 关闭 SSE 连接。
- 服务端检测到断开后调用 `AgUiAgent.cancel_run()`，它直接使用 `cancel.cancel_run()` 和 `cleanup_event`。
- `AgUiAgent.cancel_run()` 还会清理 session 里的 `pending_tool_calls`，并取消 `_active_executions` 里对应的 execution task。
- `cancel_wait_timeout` 默认 `3.0` 秒。
- 连接和 run 天然在同一个进程，所以 AG-UI 不需要分布式机制。

**自定义服务**

两种方式：

1. 连接断开触发取消（推荐）：SSE/WebSocket 断开时调用 `runner.cancel_run_async` 或 `cancel.cancel_run`。
2. 显式取消接口：业务提供一个 cancel API，内部调 `runner.cancel_run_async`。

### 2.6 单节点机制的边界

- `_RunCancellationManager` 是进程内字典，`asyncio.Event` 不能跨进程。
- cancel 请求落到非运行节点时，`cancel_run()` 找不到 `SessionKey`，`cancel_run_async()` 返回 `False`。
- 调用方无法区分“没有活跃 run”和“run 在别的节点”。
- A2A 多节点部署必须配 hash 寻址，节点漂移或扩缩容时运维成本高。
- AG-UI 的 SSE 连接和 run 在同一进程，不受影响；自定义服务如果采用连接断开触发取消，也通常不受影响。

跨节点取消需要把信号放到共享存储里，这就是第 3 节的分布式机制。

## 3. 分布式 Cancel 机制

### 3.1 设计原则与总体架构

分布式机制只解决“信号怎么从取消节点送到运行节点”，取消的执行路径完全复用第 2 节：

1. **一个 False/True 标志**：`__trpc_agent_cancel` 不存在表示 idle，`False` 表示有 run 在跑，`True` 表示已请求取消。
2. **运行侧负责登记和清理**：`run_async` 开始写 `False`，结束删除；每次执行都重新写 `False`，覆盖上一轮异常退出留下的 stale 值。
3. **取消侧只做“存在则置 True”**：`cancel_run_async` 检查标志是否存在；存在就写 `True`，不存在就返回 `False`。
4. **watchdog 与 checkpoint 解耦**：checkpoint 仍然只查本地内存 Event；共享 flag 的读取放在独立协程里，按 `cancel_poll_interval` 节流。
5. **分布式部署统一开启**：执行侧和取消侧的 Runner 都设置 `enable_distributed_cancel=True`，不需要在分布式场景里讨论开关关闭的分支。
6. **同 session 单 run、turn 串行**：和现有 `_RunCancellationManager` 的假设保持一致；同一 session 的下一轮 run 在上一轮 `finally` 完成后才开始。
7. **复用现有 state 接口**：`__trpc_agent_cancel` 直接放在 session state 里，读写沿用 `SessionService.get_session()` / `update_session_state()`，不新增接口。

总体架构：

```text
┌──────────────────────────────┐
│       Client / 调用方         │
└──────────────┬───────────────┘
               │ cancel_run_async(...)
               ▼
┌─────────────────────────────────────────────────────────────┐
│ Node A：取消接入节点（可以是任意节点）                         │
│                                                             │
│  Runner.cancel_run_async()                                  │
│      │                                                      │
│      ├── 本地 Manager 命中 ──────────────► 设置本地 Event     │
│      │                                                      │
│      └── 本地未命中 ──► 读 __trpc_agent_cancel                │
│                          存在 ──► 写 True                       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Shared SessionService（Redis / Redis Cluster / SQL）         │
│                                                             │
│  __trpc_agent_cancel = False / True / 不存在                        │
└──────────────────────────────┬──────────────────────────────┘
                               │ watchdog 轮询
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Node B：实际运行 Agent 的节点                                 │
│                                                             │
│  Runner.run_async()                                         │
│      │                                                      │
│      ├── register_run() ──► 本地 Event                      │
│      ├── 写 __trpc_agent_cancel = False                      │
│      ├── 启动 _DistributedCancelWatcher                      │
│      │        │                                             │
│      │        └── 读到 True ──► 设置本地 Event                  │
│      │                                                      │
│      └── ctx.raise_if_cancelled()                           │
│               │                                             │
│               ▼                                             │
│         RunCancelledException                               │
│               │                                             │
│               ▼                                             │
│         既有取消清理：partial text / 未完成 tool call /       │
│         AgentCancelledEvent                                 │
│                                                             │
│  finally：停 watchdog，删除 __trpc_agent_cancel              │
└─────────────────────────────────────────────────────────────┘
```

两个关键点：

- Node A 不需要知道 run 在哪个节点。它只读 session state 里的 `__trpc_agent_cancel`，存在就写 `True`。
- Node B 不需要感知请求从哪来。watchdog 看到 `True` 后，把共享信号转换成自己的本地 Event，后面的流程和单节点 `cancel_run_async()` 完全一样。

### 3.2 公共 API 与部署约定

`Runner` 增加两个构造参数，`cancel_run_async()` 的签名保持不变：

```python
class Runner:
    def __init__(
        self,
        *,
        app_name: str,
        agent: BaseAgent,
        session_service: BaseSessionService,
        ...,
        enable_distributed_cancel: bool = False,
        cancel_poll_interval: float = 1.0,
    ) -> None:
        ...

    async def cancel_run_async(
        self,
        user_id: str,
        session_id: str,
        timeout: float = 1.0,
    ) -> bool:
        ...
```

| 参数 | 位置 | 默认值 | 作用 |
|---|---|---|---|
| `enable_distributed_cancel` | `Runner.__init__` | `False` | 分布式取消开关。分布式部署下执行侧和取消侧都设为 `True` |
| `cancel_poll_interval` | `Runner.__init__` | `1.0` 秒 | watchdog 轮询共享 state 的间隔，需大于 0；开启分布式取消时传非法值直接抛 `ValueError` |

分布式部署下，所有 Runner（包括执行 `run_async` 的节点和只处理 `cancel_run_async` 的节点）都统一设置：

```python
Runner(
    ...,
    enable_distributed_cancel=True,
    cancel_poll_interval=1.0,
)
```

设计部分（3.3–3.6）只讨论这个场景。关闭开关时保持原有单节点行为：不写 flag、不启动 watchdog、`cancel_run_async` 只查本地 Manager（见 4.1）。

**返回值语义**

- `True`：本地 cancel 信号已设置，或者共享 `__trpc_agent_cancel` 存在并被置为 `True`。
- `False`：本地没有活跃 run，并且共享 state 里也没有 `__trpc_agent_cancel`，请求没有被接受。
- `True` 只表示请求已被接受。本地路径会在 `timeout` 内等待清理完成；分布式路径写完共享 flag 就立即返回，不等待运行节点消费。最终是否取消成功以 `AgentCancelledEvent` 或 run 结束为准。

### 3.3 取消状态：一个 False/True 标志

取消协议只使用一个 session-scoped key：

```python
_TRPC_AGENT_CANCEL_KEY = "__trpc_agent_cancel"
```

| 值 | 含义 |
|---|---|
| 不存在 | 当前没有活跃 run，或 run 已经结束 |
| `False` | 有 run 在跑，尚未请求取消 |
| `True` | 已请求取消，等待运行节点的 watchdog 消费 |

状态机：

```text
key 不存在
    │
    │ run_async 开始：写 False
    ▼
   [0] running
    │
    │ cancel_run_async：读到存在，写 True
    ▼
   [1] cancel requested
    │
    │ run 结束（finally）：删除
    ▼
key 不存在
```

**为什么用一个 False/True 标志就够**

- 现有 `_RunCancellationManager` 本来就是按 session 跟踪单 run，cancel 的语义是“取消这个 session 当前活跃的那一轮”。
- 运行侧每次 `run_async` 开始都重新写 `False`，所以上一轮异常退出留下的 stale 值会在下一轮开始时被覆盖。
- 运行侧结束时删除标志，正常路径下 key 只在 run 生命周期内存在。
- 取消侧只做“存在则置 True”，不需要理解 run 的细节；运行侧的 watchdog 只做“读到 True 就取消”。
- 不需要 invocation_id、node_id 或双 key。cancel 请求落到哪个节点都一样。

**需要接受的语义**

这个标志不带“目标 run”的信息。如果 cancel 请求跨过了 turn 边界，会取消到当时活跃的那一轮：

```text
时间 ──────────────────────────────────────────────────────────►

R1:  [running]──────[end, delete]
cancel for R1:            ● 请求延迟
R2:                          [start, write 0]────[running]
cancel for R1:                                      ● 读到 False，写 True
R2:                                                  watchdog 看到 True → 被取消
```

如果产品能接受“取消的是 cancel 生效时正在跑的那一轮”，这个方案就够用。要求“只取消用户点击时看到的那一轮”，需要把目标 invocation_id 写进标志；这属于后续演进，不在 v1 范围内。

**为什么不用 `temp:` 前缀**

`temp:` 被 `BaseSessionService` 在持久化前剥掉，只存在于当前 invocation 的内存里，跨节点不可见，正好和需求相反。`__trpc_agent_cancel` 是不带作用域前缀的普通 session state key，作为内部保留命名空间。

### 3.4 运行侧：注册 flag、watchdog、清理

**启动**

`run_async()` 在现有的 `register_run()` 之后启动分布式跟踪。位置在 `runners.py:471` 附近：

```python
session_key = await cancel.register_run(
    app_name=self.app_name,
    user_id=user_id,
    session_id=session_id,
)
invocation_context.session_key = session_key

distributed_tracker = None
if self._enable_distributed_cancel:
    distributed_tracker = await start_distributed_cancel(
        session_service=self.session_service,
        session=session,
        session_key=session_key,
        poll_interval=self._cancel_poll_interval,
    )
```

`start_distributed_cancel()` 的职责：

1. 用 `session_service.update_session_state(session, {_TRPC_AGENT_CANCEL_KEY: False})` 写 `__trpc_agent_cancel = False`。
2. 创建并启动 `_DistributedCancelWatcher`。
3. 写 flag 失败时只记 warning，返回 `None`，让 run 按本地取消模式继续跑。分布式取消是增强能力，不应该把主流程拖垮。
4. 如果 flag 写成功但 watchdog 启动失败，best-effort 清掉刚写的 flag，再返回 `None`。

**watchdog**

watchdog 是一个独立的 `asyncio.Task`，不阻塞 Agent 循环：

```python
class _DistributedCancelWatcher:
    async def _run(self) -> None:
        delay = self._poll_interval
        while True:
            await asyncio.sleep(delay * random.uniform(0.9, 1.1))

            try:
                session = await self._session_service.get_session(
                    app_name=self._app_name,
                    user_id=self._user_id,
                    session_id=self._session_id,
                )
                delay = self._poll_interval
            except Exception:
                logger.warning(
                    "Distributed cancel poll failed (session=%s)",
                    self._session_id, exc_info=True)
                delay = min(delay * 2, MAX_POLL_INTERVAL)  # 退避，最高 10s
                continue

            if session is not None and session.state.get(_TRPC_AGENT_CANCEL_KEY):
                await cancel.mark_cancelled(self._session_key)
                return
```

几个细节：

- **首次 tick 也带抖动**：避免大量 run 的 watchdog 在同一秒集中读 Redis。
- **读失败不终止 run**：指数退避后继续；只有连续失败才在日志里升级级别。
- **只认 truthy 值**：`False` 和“key 不存在”都表示继续执行；watchdog 用 `if flag:` 判断，不做严格的类型校验。
- **`mark_cancelled()` 对已清理的 session 是 no-op**：如果 run 刚好结束、`cleanup_run()` 已经移除了本地 Event，晚到的 watchdog tick 不应该重建注册，也不应该抛异常。

**checkpoint 不变**

`ctx.raise_if_cancelled()` 仍然只查本地 `_RunCancellationManager`：

```text
LLM chunk / tool call / agent 边界
        │
        ▼
ctx.raise_if_cancelled()  ──► 本地 Event 是否 set？
        │                              │
        │ 否                            │ 是
        ▼                              ▼
继续执行                     RunCancelledException
```

不把共享 state 的读取塞进 checkpoint，原因有两个：

1. 流式场景下 checkpoint 很密，每次读 Redis/SQL 会把网络延迟带进 token 流。
2. `get_cancel_event()` 的消费方（比如 `TrpcRemoteA2aAgent`）本来就在等本地 Event；watchdog 设置 Event 后，它们会被立刻唤醒，不需要感知 state。

**清理**

在 `run_async()` 的 `finally` 里，先停 watchdog，再删除 flag，最后做本地 `cleanup_run()`：

```python
finally:
    if distributed_tracker is not None:
        await distributed_tracker.aclose()
    await cancel.cleanup_run(
        app_name=self.app_name,
        user_id=user_id,
        session_id=session_id,
    )
```

`aclose()` 内部：

```python
async def aclose(self) -> None:
    self._stopped = True
    if self._task is not None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    try:
        await self._session_service.update_session_state(
            self._session,
            {_TRPC_AGENT_CANCEL_KEY: None},  # None 表示逻辑删除
        )
    except Exception:
        logger.warning(
            "Failed to clear distributed cancel flag (session=%s)",
            self._session_id, exc_info=True)
```

删除动作要包在 `try/except` 里。会话服务暂时不可用时，不能阻塞本地 `cleanup_run()`，否则本地 Event 的 waiter 会一直等不到清理完成。

**串行执行的前提**

`run_async` 结束时会无条件删除 flag。如果 R2 在 R1 的 `finally` 之前就启动，R1 的删除可能把 R2 刚写的 `False` 删掉：

```text
R1 finally: 删除 flag
R2 start:   写 False
R1 finally: 删除 flag   ← 如果交错，会把 R2 的登记删掉
```

现有 `_RunCancellationManager` 也是按 session 跟踪单 Event，同一 session 的并发 run 不是支持场景。应用层需要保证同一 session 的下一轮 run 在上一轮完成后再开始。

### 3.5 取消侧：本地快路径与共享 flag

`cancel_run_async()` 的判断顺序：

```text
cancel_run_async(...)
        │
        ├── 本地 Manager 命中 ──► 设置本地 Event，最多等 cleanup_event 一个 timeout ──► True
        │
        └── 本地未命中
                │
                ├── enable_distributed_cancel=False ──► False
                │
                └── request_distributed_cancel()
                        ├── flag 不存在 ──► False
                        ├── flag == True ──► True
                        └── flag == False ──► 写 True，立即返回 True
```

| 场景 | 本地 Manager | 共享 flag | 行为 |
|---|---|---|---|
| 同节点运行 | 命中 | — | 设置本地 Event，最多等 `cleanup_event` 一个 `timeout` |
| 跨节点运行 | 未命中 | `False` | 写 `True`，**立即返回** `True` |
| 已经请求过取消 | 未命中 | `True` | 不重复写，**立即返回** `True` |
| 没有活跃 run | 未命中 | 不存在 | 返回 `False` |
| 写入后 owner 尚未消费 | 未命中 | `True` | 请求已接受；何时生效由 owner watchdog 决定 |

**跨节点时序**

```text
 Client              Node A (cancel)              SessionService              Node B (run)
   │                       │                            │                         │
   │ cancel(...)           │                            │                         │
   ├──────────────────────►│                            │                         │
   │                       │                            │                         │
   │                       │ 1. 本地 Manager 命中？       │                         │
   │                       ├──────────┐                 │                         │
   │                       │ 否        │                 │                         │
   │                       │◄─────────┘                 │                         │
   │                       │                            │                         │
   │                       │ 2. get_session             │                         │
   │                       ├───────────────────────────►│                         │
   │                       │◄───────────────────────────┤ flag=False              │
   │                       │                            │                         │
   │                       │ 3. update_session_state    │                         │
   │                       │    写 True                 │                         │
   │                       ├───────────────────────────►│                         │
   │                       │                            │                         │
   │◄──────────────────────┤ 4. 立即返回 True            │                         │
   │                       │                            │                         │
   │                       │                            │ 5. watchdog 轮询         │
   │                       │                            │◄────────────────────────┤
   │                       │                            │    read flag            │
   │                       │                            ├────────────────────────►│
   │                       │                            │                         │ 6. 设置本地 Event
   │                       │                            │                         │ 7. raise_if_cancelled
   │                       │                            │                         │ 8. 会话清理
   │                       │                            │                         │
   │                       │                            │ 9. 删除 flag            │
   │                       │                            │◄────────────────────────┤
```

**取消侧伪代码**

```python
async def cancel_run_async(
    self,
    user_id: str,
    session_id: str,
    timeout: float = 1.0,
) -> bool:
    # 1. 本地快路径：请求恰好落在运行节点
    cleanup_event = await cancel.cancel_run(self.app_name, user_id, session_id)
    if cleanup_event is not None:
        await _wait_cleanup(cleanup_event, timeout)
        return True

    # 2. 分布式部署下，Runner 已开启 enable_distributed_cancel
    if not self._enable_distributed_cancel:
        return False

    # 3. 读 flag；不存在返回 False，True 直接接受，False 写 True
    #    写完立即返回，不等待 owner 的 watchdog
    return await cancel.request_distributed_cancel(
        session_service=self.session_service,
        app_name=self.app_name,
        user_id=user_id,
        session_id=session_id,
    )
```

**立即返回的含义**

分布式模式下 `timeout` 不生效：`cancel_run_async()` 只负责把共享 flag 置为 `True`，不等待 watchdog，也不等待 run 清理完成。cancel 请求的耗时只包含一次 `get_session()` 加一次 `update_session_state()`，不会被远端 run 的长度拖住。

调用方需要知道：

- `True` 只代表“请求已写入”，不代表 run 已经停止。要确认结果，观察 `AgentCancelledEvent` 或 run 的结束状态。
- 取消能否尽快生效，取决于 owner 的 `cancel_poll_interval` 和 run 当前所在的步骤。watchdog 命中后设置本地 Event，Agent 还要走到下一个 checkpoint 才会抛异常；正在执行的工具不会被强杀（见 3.2）。
- 不加分布式锁。cancel 写操作很少，两个并发 cancel 都写 `True` 是幂等的，last-writer-wins 足够。
- 不带 invocation_id，cancel 不区分目标 turn。调用方要避免把上一轮的 cancel 延迟到下一轮才发出；这种 stale cancel 会取消下一轮（见 3.3 的语义说明）。框架不做额外兜底。

### 3.6 分布式下的会话清理与事件

分布式信号最终只做一件事：调用 `cancel.mark_cancelled(session_key)` 设置本地 Event。之后的路径和单节点完全一致：

```text
共享 flag = True
    │
    ▼
watchdog 读到 True
    │
    ▼
cancel.mark_cancelled(session_key) ──► 本地 Event.set()
    │
    ▼
Agent 在下一个 checkpoint 调 raise_if_cancelled()
    │
    ▼
RunCancelledException
    │
    ▼
Runner 捕获：
  1. 收集 partial text / state
  2. handle_cancellation_session_cleanup()
  3. update_session()
  4. trace_cancellation()
  5. yield AgentCancelledEvent
    │
    ▼
finally:
  停 watchdog、删除共享 flag、cleanup_run()
```

因此不需要为分布式取消复制一套会话清理逻辑，也不需要修改 Agent 的 checkpoint。分布式部分只新增了“共享 flag → 本地 Event”的桥接。

一个简化代价：`__trpc_agent_cancel` 只存 False/True，不带 cancel 的来源和原因。`AgentCancelledEvent.error_message` 仍然是通用的取消消息。如果需要“谁发起的取消”“为什么取消”，需要把 flag 的值扩展成带元数据的结构，属于后续演进。

## 4. 使用方式

### 4.1 单节点

单节点 `InMemorySessionService` 不需要打开 `enable_distributed_cancel`，行为和现在完全一致：

```python
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService

runner = Runner(
    app_name="my_app",
    agent=agent,
    session_service=InMemorySessionService(),
)

await runner.cancel_run_async(user_id, session_id, timeout=1.0)
```

即使打开了 `enable_distributed_cancel`，InMemory 的 state 也只在同一进程内可见；跨进程仍然无法取消。它主要用来在测试里用一个共享的 `InMemorySessionService` 模拟两个节点。

### 4.2 多节点

所有节点使用同一个共享 `SessionService`，执行侧和取消侧的 Runner 都打开开关：

```python
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import RedisSessionService

session_service = RedisSessionService(
    db_url="redis://127.0.0.1:6379/0",
    is_async=True,
)

runner = Runner(
    app_name="my_app",
    agent=agent,
    session_service=session_service,
    enable_distributed_cancel=True,
    cancel_poll_interval=1.0,
)
```

Node B 正常执行：

```python
async def consume_events():
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        if isinstance(event, AgentCancelledEvent):
            print("cancelled:", event.error_message)
        else:
            handle(event)
```

Node A（或任意一个接入节点）取消：

```python
success = await runner.cancel_run_async(
    user_id=user_id,
    session_id=session_id,
)
```

取消节点也必须用同样的 `enable_distributed_cancel=True` 配置构造 Runner。分布式部署下这是统一配置，不需要考虑部分节点关闭的情况。

分布式模式下 `cancel_run_async()` 的 `timeout` 只作用于本地快路径：请求恰好落在运行节点时，最多等 `timeout` 让清理完成；落到其他节点时，写完 flag 立即返回，`timeout` 不生效。跨节点取消能在多快生效，取决于 owner 的 `cancel_poll_interval` 以及 run 当前所在的步骤。
