# trpc-agent-python Cancel 与 Steering 机制设计

本文描述 trpc-agent-python 的 run 控制面：取消（cancel）和插话（steering）。控制状态由 `trpc_agent_sdk/cancel/_public.py` 里的 `_manager` 持有，默认是进程内的 `_LocalManager`；多 worker 部署时，master 在 fork 之前调用 `init_unix_socket()`，`_manager` 切换成 `_UnixSocketManager`，通过 Unix Socket 访问 master 的权威 run 表。

## 1. 概述

Cancel 和 Steering 都是“发给正在运行的 run 的控制信息”，语义不同：

| 控制信息 | 语义 | 数据结构 | 消费方式 |
|---|---|---|---|
| cancel | signal，最新值覆盖 | `asyncio.Event` 或 master 的取消标志 | Agent 在 checkpoint 读取，或 await `get_cancel_event()` |
| steering | queue，按 FIFO 交付 | `deque[Content]` 或 master 的 steering 队列 | Agent 在 LLM 请求前一次取空并合并；LlmAgent 以 hidden Event 写回 session |

控制面只有一个入口：

```text
                    Runner / InvocationContext
                              │
                              ▼
                 trpc_agent_sdk.cancel 公开函数
                              │
                              ▼
                    _public._manager
                      ├── _LocalManager
                      │     └── _runs: SessionKey → _LocalRun
                      └── _UnixSocketManager
                            └── _runs: SessionKey → _ClientRun
                                  └── 每个 run 一条 UDS 连接
```

`_manager` 在导入时是 `_LocalManager`；`init_unix_socket()` 启动 master 的 `_UnixSocketServer` 并把 `_manager` 替换为 `_UnixSocketManager`；`release_unix_socket()` 停掉 server 并换回 `_LocalManager`。运行期间不切换。

## 2. 整体架构

### 2.1 单 worker（进程内存）

```text
+---------------------------- Worker 进程 ----------------------------+
|                                                                    |
|  Runner                                                            |
|    │  register_run / unregister_run                                |
|    ▼                                                               |
|  _LocalManager                                                     |
|    └── _runs: dict[SessionKey, _LocalRun]                          |
|            _LocalRun                                               |
|              ├── cancel_event: asyncio.Event                       |
|              ├── cleanup_event: asyncio.Event                      |
|              └── steering: deque[Content]                          |
|                                                                    |
|  Agent                                                             |
|    └── checkpoint                                                  |
|          ├── ctx.raise_if_cancelled()                              |
|          ├── ctx.get_cancel_event()                                |
|          └── ctx.consume_steering()                                |
+--------------------------------------------------------------------+
```

单 worker 模式下：

- `cancel_run()` set `cancel_event`，然后最多等 `cleanup_event` `timeout` 秒。
- `unregister_run()` 清空 steering、set `cleanup_event`，唤醒正在等清理的 `cancel_run()`。
- `send_steering()` append 到 `steering` deque。
- Agent 在 checkpoint 通过 `InvocationContext` 读取控制状态。

### 2.2 多 worker（Unix Socket）

```text
master process
+--------------------------------------------------------------+
| _UnixSocketServer                                            |
| _runs: SessionKey -> _ServerRun   <-- 权威 run 表            |
+--------------------------------------------------------------+
        ^                                      ^
        | run 连接（owner）                      | 一次性短连接（sender）
        |                                      |
+-------------------------------+      +-------------------------------+
| Worker A（run owner）         |      | Worker B（cancel/steering）   |
|   _UnixSocketManager          |      |   _UnixSocketManager          |
|   _runs: key -> _ClientRun    |      |   只发送 cancel / steering    |
+-------------------------------+      +-------------------------------+
```

- master 在 fork 之前调用 `init_unix_socket()`，启动 server thread；fork 出来的 worker 继承 `_UnixSocketManager`，各自维护本进程的 `_runs`。
- owner 进程每个 run 在 `register_run()` 时打开一条 UDS 连接，`unregister_run()` 时关闭；连接断开时 master 自动清理 run。
- cancel / steering 的发送方使用一次性短连接，不需要知道 owner 在哪个 worker。
- owner 在 `register_run()` 后为每个 run 启动一个 poller，按 `poll_interval` 发 `sync` RPC，把 cancel 标志和 steering 同步到本地。

用户用法：

```python
# master 进程，在 fork 之前初始化
from trpc_agent_sdk.cancel import UnixSocketConfig, init_unix_socket, release_unix_socket

init_unix_socket(UnixSocketConfig())

try:
    server = trpc.new(config_path)
    register_service(SERVICE_NAME, create_service())
    server.serve()
finally:
    release_unix_socket()
```

```python
# worker 内的 Runner
runner = Runner(
    app_name="my_app",
    agent=agent,
    session_service=session_service,
)

async for event in runner.run_async(
    user_id="user_1",
    session_id="session_1",
    new_message=Content(role="user", parts=[Part(text="check the weather")]),
):
    ...

accepted = await runner.cancel_run_async(user_id="user_1", session_id="session_1")

status = await runner.send_steering(
    user_id="user_1",
    session_id="session_1",
    content=Content(role="user", parts=[Part(text="also check Shanghai")]),
)
```

Unix Socket 的协议、时序和一致性边界见第 5 章。

### 2.3 控制状态读取

Agent 不接触 Manager，也不接触 UDS，只通过 `InvocationContext`：

```text
Agent checkpoint
      │
      ├── ctx.raise_if_cancelled()
      │        └── cancel.check_cancel(session_key) -> bool
      │                 └── _manager.check_cancel(key) -> bool
      │                          ├── _LocalManager: cancel_event.is_set()
      │                          └── _UnixSocketManager: 本地 run.event（poller 同步）
      │
      ├── ctx.get_cancel_event()
      │        └── cancel.get_cancel_event(session_key)
      │                 └── _manager.get_cancel_event(key)
      │                          └── UDS: 返回 run.event（poller 持续同步）
      │
      └── ctx.consume_steering()
               └── cancel.consume_steering(session_key)
                        ├── _manager.drain_steering(key) -> List[Content]
                        ├── 合并为一个 Content（FIFO parts）
                        └── LlmAgent: mark + yield Event(visible=False)
```

控制信息在 Agent 走到 checkpoint 时生效。cancel 优先于 steering：先 `ctx.raise_if_cancelled()`，再 `consume_steering()`。LlmAgent 把消费到的 steering 包装成 `visible=False` Event yield 给 Runner，由 Runner 持久化到 session。

## 3. 核心对象

### 3.1 模块布局

```text
trpc_agent_sdk/
└── cancel/
    ├── __init__.py          只做 re-export
    ├── _base.py             CancelManager 抽象基类
    ├── _public.py           _manager 单例 + 公开函数 + mark_steering_content
    ├── _local_manager.py    _LocalManager、_LocalRun
    ├── _types.py            SessionKey、Status
    ├── _cleanup.py          取消文案、skipped tool_result 补齐
    └── _unix_socket/
        ├── __init__.py      导出 UnixSocketConfig
        ├── _config.py       UnixSocketConfig
        ├── _protocol.py     帧编码、SessionKey 序列化
        ├── _server.py       _UnixSocketServer、_ServerRun
        └── _manager.py      _UnixSocketManager、_ClientRun
```

公开接口：

```python
from trpc_agent_sdk.cancel import (
    CancelManager,
    SessionKey,
    Status,
    UnixSocketConfig,
    cancel_run,
    check_cancel,
    consume_steering,
    get_cancel_event,
    init_unix_socket,
    mark_steering_content,
    register_run,
    release_unix_socket,
    send_steering,
    set_cancel_manager,
    unregister_run,
)
```

`register_run` / `unregister_run` 是 Runner 专用的框架生命周期接口，Agent 代码不应该直接调用。

| 接口 | 使用方 | 作用 |
|---|---|---|
| `register_run(key)` | Runner | 占用一个 run；同一 session 已有活跃 run 时抛 `RuntimeError` |
| `unregister_run(key)` | Runner | 幂等收尾，释放 run |
| `cancel_run(key, timeout)` | Runner / 服务端 | 请求取消并返回 `Status`；local 等待 cleanup，UDS 发布后返回 |
| `send_steering(key, content)` | Runner / 服务端 | 写入一条 steering |
| `consume_steering(key)` | `InvocationContext` | 取空 steering 并合并成一个 `Content` |
| `mark_steering_content(content)` | Agent 实现 | 给 steering `Content` 加 prefix/suffix marker，外层再包装为 hidden Event |
| `check_cancel(key)` | `InvocationContext`（经 `_public`） | 返回是否已取消，不抛异常 |
| `get_cancel_event(key)` | `InvocationContext` | 返回本进程内该 run 的取消 Event；UDS 下由 per-run poller 持续同步 |
| `set_cancel_manager(manager)` | 应用启动 | 安装自定义 `CancelManager`；整个进程生命周期只调用一次 |
| `init_unix_socket(config)` / `release_unix_socket()` | master 启动/关闭 | Unix Socket 控制面生命周期 |

`ctx.raise_if_cancelled()` 是 Agent 使用的方法：它调用 `cancel.check_cancel()`，返回 `True` 时抛 `RunCancelledException`；`CancelManager` 本身不负责抛异常。

### 3.2 对象职责

```text
                 +-----------------------------------+
                 |          _public._manager         |
                 |           CancelManager           |
                 +----------------+------------------+
                                  |
                +-----------------+-----------------+
                |                                   |
     +----------v----------+             +----------v----------+
     |    _LocalManager    |             | _UnixSocketManager  |
     |  _runs: Dict[key,   |             |  _runs: Dict[key,   |
     |          _LocalRun] |             |          _ClientRun]|
     +----------+----------+             +----------+----------+
                |                                   |
     +----------v----------+             +----------v----------+
     |      _LocalRun      |             |      _ClientRun     |
     |  cancel_event       |             |  key / reader       |
     |  cleanup_event      |             |  writer / event     |
     |  steering: deque    |             |  steering: deque    |
     +---------------------+             |  poller             |
                                         +---------------------+
```

- `_LocalManager` 和 `_UnixSocketManager` 都继承 `CancelManager`，公开方法集合一致：`register_run`、`unregister_run`、`cancel_run`、`send_steering`、`drain_steering`、`check_cancel`、`get_cancel_event`。
- `_LocalRun` 只有 `key`、`cancel_event`、`cleanup_event`、`steering`。
- `_ClientRun` 持有 `key`、读写 UDS 的 `reader` / `writer`、取消 `event`、本地 steering 队列和 per-run poller task。
- `_UnixSocketServer` 只在 master 进程存在，持有权威 run 表；worker 通过各 run 的连接访问它。

### 3.3 控制流程

```text
cancel_run(key)
    │
    ├── 本进程有 run
    │      ├── _LocalManager: cancel_event.set()
    │      │                   await cleanup_event.wait()（最多 timeout）
    │      └── _UnixSocketManager: event.set()
    │                          一次性短连接发 send_cancel
    │
    └── 本进程没有 run
           └── 一次性短连接发 send_cancel

send_steering(key, content)
    │
    ├── content 为空 ──► Status(ok=False, error_msg="steering content is empty")
    │
    ├── 本进程有 run：
    │      ├── _LocalManager: append 到 deque
    │      └── _UnixSocketManager: append 到 _ClientRun.steering
    │
    └── 本进程没有 run：
           └── 一次性短连接发 send_steering

consume_steering(key)
    │
    ├── drain_steering(key) -> List[Content]
    ├── 空 ──► None
    └── 按 FIFO 合并 parts -> Content(role="user")
```

### 3.4 自定义 CancelManager

`_public._manager` 默认是 `_LocalManager`。应用可以在启动阶段调用 `set_cancel_manager()` 安装自己的 backend：

```python
from trpc_agent_sdk.cancel import CancelManager, set_cancel_manager


class MyCancelManager(CancelManager):
    ...


set_cancel_manager(MyCancelManager())
```

```text
进程启动
  │
  ├── 不设置 ──────────────► _LocalManager
  │
  ├── set_cancel_manager(m) ► 用户实现
  │
  └── init_unix_socket(cfg) ► _UnixSocketManager
```

`cancel_run` 和 `send_steering` 都返回 `Status(ok: bool, error_msg: str)`：成功时 `ok=True`；没有活跃 run、空内容或 backend 失败时 `ok=False`，`error_msg` 由 manager 填写。`Status` 实现 `__bool__`，`bool(status)` 等价于 `status.ok`，调用方可以直接 `if status:`，也可以显式检查 `status.ok`。

约束：

- `set_cancel_manager()` 在 fork 和第一次 `register_run()` 之前调用；整个进程生命周期只调用一次，没有 reset，也不恢复 previous manager。
- manager 实例会被 fork 继承；不要在 `__init__` 里创建 socket、连接或线程，进程相关资源应在 `register_run()` 或首次使用时懒创建。
- `set_cancel_manager()` 和 `init_unix_socket()` 二选一；框架不校验混用。
- 自定义 manager 必须实现 `_base.py` 中七个 async 方法，并遵守对应 docstring 里的契约。

## 4. Runner 生命周期

```text
Runner.run_async()                  cancel 模块                 manager
      │                                │                          │
      │ register_run(key)              │                          │
      ├───────────────────────────────►├─────────────────────────►│ 创建 run
      │                                │                          │ _runs[key] = run
      │ ctx.session_key = key          │                          │
      │ agent loop ...                 │                          │
      │                                │                          │
      │ 取消分支：                     │                          │
      │ unregister_run(key)            │                          │
      ├───────────────────────────────►├─────────────────────────►│ pop run
      │ yield AgentCancelledEvent      │                          │ close 连接
      │                                │                          │
      │ finally：                      │                          │
      │ unregister_run(key)            │                          │ 已 pop，直接返回
      └───────────────────────────────►└─────────────────────────►│
```

- `register_run()` 在 session 用户消息写入之前调用；被拒绝的并发 run 不会留下用户消息。
- 初始化阶段 `register_run()` 失败（同一 session 已有活跃 run，或 UDS 连接/register RPC 失败）会抛 `RuntimeError`；Runner 记录 trace 后原样向上抛，不进入 agent 执行。
- 取消分支先 `unregister_run()` 再 yield `AgentCancelledEvent`：调用方看到取消事件时，run 槽位已经释放。
- `unregister_run()` 幂等，取消分支和 `finally` 各调一次不会出问题。
- Runner 用 `run_registered` 标记是否注册成功，避免初始化失败时误调 `unregister_run()`。
- Runner 对非 partial 事件统一先 `append_event(session)`，再根据 `visible` 决定是否 yield 给调用方；LlmAgent 的 steering Event 也走这条路径，所以它只落 session，不出现在客户端事件流。

### 4.1 消费 AgentCancelledEvent

取消分支 yield `AgentCancelledEvent` 后，消费方要把生成器读到自然结束（`continue`）或显式 `await agen.aclose()`，不要用 `break` / `return` 把生成器丢下。生成器被 asyncgen finalizer 在另一个 task/Context 里关闭时，OTel 的 contextvar token 无法在该 Context 里 reset，日志会打印 `Failed to detach context`；取消收尾本身不受影响。

```text
消费方看到 AgentCancelledEvent
  ├─ continue / aclose() ──► 当前 task 的 Context 执行 finally ──► detach 正常
  └─ break / return ───────► 生成器悬挂 ──► 另一个 Context finalize ──► detach warning
```

## 5. Unix Socket 多 worker 部署

Unix Socket 控制面面向 fork 多 worker 部署。`init_unix_socket(config)` 在 master 启动 `_UnixSocketServer`，同时创建一个 `_UnixSocketManager` 并挂到 `_public._manager`。fork 之后，每个 worker 继承 manager 的一份副本，用自己副本里的 `_runs` 管理本 worker 的 run。

`_UnixSocketServer` 只属于 master，持有权威 run 表；`_UnixSocketManager` 由 worker 使用，持有本进程的本地 run 表。两者不互相引用，也不共享 `_runs`。每个 run 在 `register_run()` 时打开一条独立 UDS 连接，`unregister_run()` 时关闭；同时启动一个 per-run poller，按 `poll_interval` 把 master 的 cancel 标志和 steering 同步到本地。Agent 的 checkpoint 只读本地状态，不再逐次发 RPC。

运行期假设：

- master 正常运行期间不退出；
- worker 退出时，master 按失败策略停掉其余 worker，调用一次 `release_unix_socket()` 后退出；
- 同一个 run 只有 poller 会发 `sync`，checkpoint 只读本地状态，不引入 per-run lock；
- 不做 worker 重连、不做 re-register、不做跨 worker 生命周期的状态恢复。

### 5.1 职责与整体架构

模块布局：

```text
trpc_agent_sdk/cancel/_unix_socket/
├── __init__.py
├── _config.py       # UnixSocketConfig
├── _protocol.py     # key 转换、frame 编解码和读写
├── _server.py       # _ServerRun / _UnixSocketServer
└── _manager.py      # _ClientRun / _UnixSocketManager
```

`_protocol.py` 只放 client / server 共用的协议细节；`_server.py` 和 `_manager.py` 都不再暴露 frame 工具函数。

`_UnixSocketManager` 是 `CancelManager` 的内置实现；`init_unix_socket()` 和 `set_cancel_manager()` 二选一，不要混用。

```text
master process
+--------------------------------------------------------------+
| _UnixSocketServer                                            |
|   _runs: SessionKey -> _ServerRun   <-- 权威 run 表          |
|   listening socket / server thread / event loop              |
+--------------------------------------------------------------+

worker process A
+--------------------------------------------------------------+
| _UnixSocketManager                                           |
|   _config                                                    |
|   _runs: SessionKey -> _ClientRun   <-- 本 worker 的 run 表  |
+--------------------------------------------------------------+

worker process B
+--------------------------------------------------------------+
| _UnixSocketManager                                           |
|   _config                                                    |
|   _runs: SessionKey -> _ClientRun   <-- 本 worker 的 run 表  |
+--------------------------------------------------------------+
```

`_public` 里的状态：

```text
_public._manager        # UDS 模式下指向 _UnixSocketManager
_public._unix_server    # master 的 _UnixSocketServer
```

- `init_unix_socket()` 启动 server，并创建 manager；manager 不持有 server，server 也不引用 manager。
- master 侧的 manager 只是给 fork 后 worker 继承的模板；master 正常不用它管理 run。
- 每个 worker 继承 manager 后，`_runs` 初始为空；`register_run()` 时才创建 `_ClientRun`。
- master 的 server `_runs` 也初始为空；worker 通过 `register` RPC 在 server 侧创建 `_ServerRun`。

同一个 run 的 `check_cancel` / `consume_steering` 只读本地状态，per-run poller 是这条连接唯一的 RPC 客户端，因此 `_ClientRun` 不需要 lock。

### 5.2 `_ServerRun` / `_ClientRun`

server 和 manager 各自定义自己的 run 数据结构，不用基类，避免两侧共享用不到的字段：

```python
@dataclass
class _ServerRun:
    cancelled: bool
    owner: asyncio.StreamWriter
    steering: Deque[str] = field(default_factory=deque)

@dataclass
class _ClientRun:
    key: SessionKey
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    event: asyncio.Event = field(default_factory=asyncio.Event)
    steering: Deque[Content] = field(default_factory=deque)
    poller: Optional[asyncio.Task] = None
```

`_ServerRun` 字段：

| 字段 | 用途 |
|---|---|
| `cancelled` | 权威 cancel 标志 |
| `owner` | owner 连接的 writer，用于路由和清理 |
| `steering` | master 的 steering 队列 |

`_ClientRun` 字段：

| 字段 | 用途 |
|---|---|
| `key` | run 的 key |
| `reader` | 读 master 的 sync 响应 |
| `writer` | 发 register / sync 请求 |
| `event` | 取消信号；本地 cancel 和 poller 同步结果都写这里 |
| `steering` | 本地 steering 队列；本地 send 和 poller 同步都写这里 |
| `poller` | per-run syncer task，按 `poll_interval` 同步 master 状态 |

`_ServerRun` 不需要 reader：server 的 `_handle_connection(reader, writer)` 已经持有 reader 和 writer，读请求、写响应都由 handler 完成。`_ServerRun` 只需要 `owner`，用于判断请求是不是来自 run 的 owner，以及连接关闭时清理对应 run。

`_ClientRun` 需要 reader 和 writer：worker 没有外部 handler，它自己就是这条 RPC 连接的客户端，需要写请求、读响应。

### 5.3 server 和 manager 的状态

```text
master
_UnixSocketServer
  _runs: Dict[SessionKey, _ServerRun]
    K -> _ServerRun(
           cancelled=False,
           owner=conn_writer,
           steering=deque(),
         )

worker A
_UnixSocketManager
  _config: UnixSocketConfig
  _runs: Dict[SessionKey, _ClientRun]
    K -> _ClientRun(
           key=K,
           reader=run_reader,
           writer=run_writer,
           event=Event(),
           steering=deque(),
           poller=<sync task>,
         )
```

`_UnixSocketManager._runs` 和 `_UnixSocketServer._runs` 是两份独立的数据：

- server 的 `_runs` 是权威表，决定 cancel 和 steering；
- worker 的 `_runs` 只包含本进程 register 过、还没 unregister_run 的 run；
- 两边的 run 对象不共享状态，也不互相引用。

`_UnixSocketManager` 在 fork 后由每个 worker 各自继承一份副本；`_ClientRun.reader` / `writer` 只在这个 worker 的 event loop 里使用。

### 5.4 RPC 接口

| RPC | 请求 | 响应 | 调用方 |
|---|---|---|---|
| `register` | `key` | `ok` / `duplicate` | run 连接 |
| `unregister` | `key` | `ok` | 任意短连接 |
| `sync` | `key` | `{cancelled, steering[]}` | run 连接（poller） |
| `send_cancel` | `key` | `{ok, error_msg}` | 任意短连接 |
| `send_steering` | `key, payload` | `{ok, error_msg}` | 任意短连接 |

行为：

- `register`：一个连接只注册一个 key；重复 key 返回 `duplicate`；成功后 server 在 `_runs[key]` 里保存 `_ServerRun(cancelled=False, owner=writer, steering=deque())`。
- `unregister`：按 key 删除 `_ServerRun`；幂等，run 不存在也返回 `ok`。允许任意短连接调用，保证同一个 key 的连续 turn 不会命中尚未清理的 run。
- `sync`：run 不存在、owner 不匹配或已取消，返回 `{cancelled: true, steering: []}`；否则取走并清空 `run.steering`，返回 `{cancelled: false, steering: [...]}`。
- `send_cancel`：任意连接都能发；run 不存在返回 `{ok: false, error_msg: "no active run"}`；存在则置 `run.cancelled = true` 并返回 `{ok: true, error_msg: ""}`。
- `send_steering`：run 不存在或已取消返回 `{ok: false, error_msg: "no active run"}`；否则 append 并返回 `{ok: true, error_msg: ""}`。

公共 API 和 manager 的对应关系：

```text
Runner/Agent                     _UnixSocketManager                              RPC / 本地
  register_run(key)          ->  创建 _ClientRun，连接，register，启动 poller     ->  register
  unregister_run(key)        ->  pop 本地 run；短连接发 unregister；取消 poller，关闭连接 ->  unregister
  check_cancel(key)          ->  只看 run.event -> bool                           ->  -
  consume_steering(key)      ->  从 run.steering 本地取空                          ->  -
  get_cancel_event(key)      ->  返回 run.event                                    ->  -
  cancel_run(key)            ->  本进程 run 先 set event；短连接发送                ->  send_cancel
  send_steering(key, content)->  本进程 run 直接入本地队列；否则短连接              ->  send_steering
```

三种 manager 的接口全部是 async，`_public` 直接 await，不再兼容同步实现。

### 5.5 核心时序

register / checkpoint / sync / unregister_run：

```text
Runner                  _ClientRun                  master server
  |                         |                              |
  | register_run(K) ------->| connect -------------------->|
  |                         | register(K) --------------->|
  |                         |                              | _runs[K] =
  |                         |                              |   _ServerRun(...)
  |                         |<---------- ok ---------------|
  |                         | start poller task            |
  |<-------- ok ------------|                              |
  |                         |                              |
  | check_cancel(K) ------->| 本地 run.event -> bool        |
  | consume_steering(K) --->| 本地 run.steering             |
  |                         |                              |
  |                         | every poll_interval:         |
  |                         | sync(K) -------------------->|
  |                         |<---- {cancelled, steering} --|
  |                         | event.set / deque.extend     |
  |                         |                              |
  | unregister_run(K) ----->| 短连接 unregister(K) ------>| _runs.pop(K)
  |                         | cancel poller, close ------>| EOF
  |                         |                              | _drop_connection (幂等)
```

- `register_run` 先 connect，再 register；master 返回 `ok` 后 worker 才把 `_ClientRun` 放进自己的 `_runs`，并为这个 run 启动 poller。
- poller 先 `sleep(poll_interval)` 再发 `sync`；短于 poll interval 的 run 一次 sync 都不会发。
- `check_cancel` / `consume_steering` 只读本地的 `event` / `steering`，checkpoint 主链路不发 RPC。
- `sync` 由 master 取走并清空 steering 队列，同时返回权威 cancel 标志；worker 把结果写入本地 event / deque。
- `unregister_run` 先 pop `_runs[key]`，用一次性短连接发 `unregister` 让 master 同步删除权威 run；随后取消 poller、关闭连接。这样同一个 key 的连续 turn 重新 register 时不会命中残留 run；EOF 清理仍然保留，作为兜底。

cancel：

```text
worker B（非 owner）              server run K              owner run A
  |                                   |                         |
  | connect + send_cancel(K) -------->| cancelled = true        |
  |<--- {ok: true, error_msg: ""} ----|                         |
  | close                             |                         |
  |                                   |<-- sync(K) -------------|
  |                                   |--- cancelled=true ---->|
  |                                   |                         | event.set(); RunCancelledException
```

- 非 owner 的 `cancel_run` 使用一次性短连接，发完就关；owner 在下一个 poll interval 的 `sync` 里看到 cancel。
- worker 侧 owner 自己调 `cancel_run` 时，如果本进程 `_runs[key]` 存在，先 `run.event.set()`，让 checkpoint 和 `get_cancel_event` 立刻看到；同时发一次 `send_cancel`，同步 master 的权威状态。
- master 侧 `cancel_run` 只发一次性短连接，由 server thread 更新 `_ServerRun`，不在主线程直接改 run。
- `ctx.raise_if_cancelled()` 调 `check_cancel` 看本地 `run.event`；即使短连接失败，本地取消也不会漏掉。

steering：

```text
worker B（sender）                server run K              owner run A
  |                                   |                         |
  | connect + send_steering(K,S) ---->| run.steering.append(S)  |
  |<------------ ok=true ------------|                         |
  | close                             |                         |
  |                                   |<-- sync(K) -------------|
  |                                   |-- 返回 [S]，清空队列 -->|
  |                                   |                         | deque.append(S)
  |                                   |                         | consume_steering -> [S]
```

- 跨 worker 的 steering 先进入 master 的 `_ServerRun.steering`，owner 的 poller 在下一次 `sync` 时把它搬到本地 deque。
- owner 自己调用 `send_steering` 时直接 append 到 `_ClientRun.steering`，不发 RPC。
- `sync` 响应丢失时，这一批 steering 丢失；语义是 at-most-once。
- run 已取消后 `send_steering` 返回 `Status(ok=False, error_msg="no active run")`。

per-run poller：

```text
owner run A                       master
  |                                  |
  | register_run(K) -> start poller  |
  |                                  |
  | poller every poll_interval:      |
  |   sync(K) ---------------------->|
  |<------ {cancelled, steering[]} --|
  |   ├─ cancelled -> event.set(); return
  |   └─ steering  -> local deque.extend
  |                                  |
  | unregister_run / event set -> cancel poller
```

- 每个 run 在 `register_run()` 后都会启动 poller；`get_cancel_event()` 只返回本地 event，不再按需启动 watcher。
- 跨 worker 的 cancel / steering 延迟上限是一个 `poll_interval`；本进程内发送 cancel / steering 是即时的。
- poller 是 run 连接上唯一的 RPC 客户端，checkpoint 不发 RPC，因此不需要 per-run lock。
- poller 失败时 `run.event.set()` 并关闭这个 run 的连接；只有这个 run 受影响。

连接失败：

```text
_ClientRun 的 RPC 失败（connect / 超时 / EOF / 帧错误）
  ├─ 关闭这个 run 的连接
  ├─ run.event.set()
  └─ 后续 ctx.raise_if_cancelled() 抛 RunCancelledException；consume_steering 返回空

sender 的一次性短连接失败
  ├─ cancel_run 返回 Status；本进程有 run 时为 ok=True
  └─ send_steering 返回 Status(ok=False, error_msg=...)
```

- 不做重连。
- 一个 run 的连接失败不会影响同 worker 的其他 run。
- 一次性 sender 失败不会改变任何 run 的状态。

worker 退出：

```text
worker A 进程死
  └─ 该 worker 的所有 run 连接关闭

master _UnixSocketServer
  ├─ 每个连接的 handler 读到 EOF
  ├─ 删除 _runs 中 owner == 该连接的 _ServerRun
  └─ 其他 worker 的连接和 run 不受影响
```

### 5.6 生命周期与 fork

```text
master
  |
  ├─ init_unix_socket(config)
  │    ├─ _public._unix_server = _UnixSocketServer(config)
  │    ├─ _public._unix_server.start()
  │    └─ _public._manager = _UnixSocketManager(config)   # _runs = {}
  │
  ├─ fork worker A / B ...
  │    └─ child: 继承 _public._manager 的副本
  │         ├─ _runs = {}
  │         └─ 第一次控制面调用:
  │              ├─ connect
  │              ├─ RPC register
  │              └─ _runs[key] = _ClientRun(...)
  │
  └─ release_unix_socket()          # master only
       ├─ _public._unix_server.stop()
       ├─ 清空 server._runs
       ├─ unlink socket_path
       ├─ _public._unix_server = None
       └─ _public._manager = _LocalManager()
```

- manager 在 `init_unix_socket()` 时创建；worker 不重新创建 manager，而是使用 fork 继承的副本。
- manager 在 init 时 `_runs` 为空，worker 继承后直接使用；run 连接都在 fork 之后、`register_run()` 时创建，不继承父进程的 connected fd。
- `release_unix_socket()` 在 worker 全部停止后调用，master manager 没有 active run，因此只停 server、重置全局状态，不需要 close manager。
- run 结束时关闭连接；worker 进程退出时，内核关闭所有剩余连接，master 通过 EOF 清理。

### 5.7 配置与一致性边界

```python
@dataclass(frozen=True)
class UnixSocketConfig:
    socket_path: str = "/tmp/trpc-agent-py/cancel.sock"
    poll_interval: float = 0.1
    timeout: float = 3.0
```

| 配置项 | 作用 |
|---|---|
| `socket_path` | UDS 路径 |
| `poll_interval` | per-run poller 同步 master 状态的周期（秒） |
| `timeout` | connect、请求/响应读写和 server 写响应的超时（秒） |

RPC 超时由 `UnixSocketConfig.timeout` 控制，默认 3.0 秒。run 连接由 per-run poller 按 `poll_interval` 定期使用，不依赖 checkpoint 触发请求。

一致性边界：

- `init_unix_socket` 把 `_public._manager` 切到 `_UnixSocketManager`；`release_unix_socket` 切回 `_LocalManager`；worker 使用 fork 继承的 manager 副本；
- 初始化调用只做切换，不校验重复调用和模式互斥，由调用方保证顺序；
- `register_run` 连接失败或 register 返回 `duplicate` 时，关闭连接并抛 `RuntimeError`，run 不会开始；
- `unregister_run` 先 pop 本地 `_ClientRun`，发一次性短连接的 `unregister`，再取消 poller、关闭连接；master 的 EOF 清理仍然保留，作为兜底；
- `sync` 只允许 owner 连接调用；owner 不匹配或 run 不存在时返回 `cancelled=true` 和空 steering；
- 一个 run 连接只注册一个 key；sender 短连接不属于任何 run；
- `check_cancel` / `consume_steering` 只读本地 `event` / `steering`，不发 RPC；
- `get_cancel_event` 不发起 RPC，只返回 `run.event`；poller 负责同步；
- cancel 是 latched signal：master 置位后不会自动清除，直到 run 连接关闭；
- steering 是 at-most-once：master 在 `sync` 时 drain，响应丢失则这一批消息丢失；
- run 取消后 `send_steering` 返回 `Status(ok=False, error_msg="no active run")`；
- `_ClientRun` 的 RPC 失败按 run fail-closed；sender 短连接失败只影响当前调用；
- 同一时刻只有 poller 使用 run 连接，不需要 per-run lock；
- 不做重连、不做 re-register；
- `register_run` / `unregister_run` / `cancel_run` / `send_steering` / `check_cancel` / `consume_steering` / `get_cancel_event` 都是 async；
- 不设置 per-worker active run 上限；连接数随 active run 增长，由部署侧控制并发；
- 只支持 fork 部署；`release_unix_socket()` 只在 master 调用。

### 5.8 性能与取舍

本机基准（跨进程，4 字节长度 + JSON，20k 次）：

```text
run 连接上的 sync RPC   ~46.4µs / 次
一次性短连接            ~46.5µs / 次
```

checkpoint 主链路不再发 RPC，控制面开销变成每个 run 的固定 polling：

```text
每个 run: 1 / poll_interval 次 sync ≈ 46µs / poll_interval

100 个 active run, poll_interval=0.1s
  ≈ 1000 sync/s × 46µs ≈ 46ms/s ≈ 4.6% 单核 / worker

100 个 active run, poll_interval=0.5s
  ≈ 200 sync/s × 46µs ≈ 9.2ms/s ≈ 0.9% 单核 / worker
```

连接和 task 数量：

```text
run 连接                    1 / active run
master handler task         1 / active run
per-run poller task         1 / active run
一次性 sender 连接          1 / 次 cancel 或 steering，用完即关
checkpoint RPC              0
```

取舍：

- checkpoint 不再发 RPC；短于 `poll_interval` 的 run 只有 connect / register / close 的固定成本。
- 长 run 的代价是每个 run 固定 `1/poll_interval` 次 sync；并发高时 master 的 event loop 需要处理这些周期请求。默认 0.1s 在几十到几百 active run 的量级下成本可控。
- 跨 worker 的 cancel / steering 延迟上限是一个 `poll_interval`；本进程内发送 cancel / steering 是即时的。
- active run 数达到几百以上时，每 run 一条连接仍会增加 fd、handler task 和 poller task，需要在部署侧控制单 worker 的并发 run 数。
- 如果固定 polling 在高并发下成为瓶颈，可以把 `sync` 升级成 long-poll（master 最多挂起 `poll_interval`，状态变化立即返回），接口不变。

## 6. 取消设计

### 6.1 取消信号

```text
cancel_run(key)
      │
      ├── 本进程有 run
      │      ├── _LocalManager: cancel_event.set()
      │      │                   await cleanup_event.wait()（最多 timeout）
      │      └── _UnixSocketManager: event.set()
      │                          一次性短连接发 send_cancel
      │
      └── 本进程没有 run
             └── 一次性短连接发 send_cancel
```

- local 模式 `cancel_run` 会等 `cleanup_event`。
- UDS 模式只发布 cancel，不等 owner 清理；`timeout` 参数保留但忽略。
- 重复取消没有副作用；取消请求以 session 为粒度。

### 6.2 单 worker 取消

```text
Runner.cancel_run_async()
        │
        ▼
cancel.cancel_run(key, timeout)
        │
        ▼
_LocalManager.cancel_run()
        │
        ├── run.cancel_event.set()
        ├── await run.cleanup_event.wait()
        │
        ▼
Agent 在下一个 checkpoint:
  ctx.raise_if_cancelled()
        │
        ▼
  raise RunCancelledException
        │
        ▼
Runner except RunCancelledException
        │
        ├── 补 skipped tool_result
        ├── 落取消标记
        ├── unregister_run(key) ──► cleanup_event.set()
        └── yield AgentCancelledEvent
```

`cancel_run_async()` 默认最多等 1 秒；run 在超时前完成清理就立即返回，超时后返回 `True` 并记录 warning。`timeout` 由调用方通过 `Runner.cancel_run_async(timeout=...)` 传入，最终透传给 `CancelManager.cancel_run`；A2A 和 AG-UI 分别使用自己的 `cancel_wait_timeout` 配置。`Runner.cancel_run_async()` 对用户保持返回 `bool`；manager 层的 `cancel.cancel_run()` 返回 `Status`，失败详情由 Runner 记录到日志。

### 6.3 多 worker 取消

```text
Worker A（owner）              master server                Worker B（cancel）
    │                              │                              │
    │ register_run(key)            │                              │
    │   └── run 连接 + poller ─────►│ _runs[key] = _ServerRun      │
    │                              │                              │
    │                              │ cancelled=true ◄─────────────│ cancel_run(key)
    │                              │                              │ 发布后立即返回
    │ poller: sync RPC ───────────►│ 读 cancelled                 │
    │   event.set()                │                              │
    │                              │                              │
    │ await event.wait()           │                              │
    │   → 返回                     │                              │
    │ raise_if_cancelled()         │                              │
    │ raise RunCancelledException  │                              │
    │ unregister_run(key) ────────►│ EOF -> 删除 run              │
```

- owner 调用 `cancel_run()`：set 本地 `event`，同时用一次性短连接发 `send_cancel`，然后立即返回。
- 非 owner 调用 `cancel_run()`：直接发 `send_cancel`，立即返回；owner 在下一个 poll interval 的 `sync` 里看到取消。
- 每个 run 在 `register_run()` 后都会启动 poller；`get_cancel_event()` 只返回本地 event，不再懒创建 watcher。
- 没有跨进程的 cleanup Event；UDS 模式下 `cancel_run` 不等待清理。
- checkpoint 只读本地 `event`，不发 RPC；跨 worker 取消的感知延迟上限是一个 `poll_interval`。

### 6.4 取消的生效点

| 位置 | 行为 |
|---|---|
| 每个 LLM 流式事件后 | `ctx.raise_if_cancelled()` |
| 每轮 LLM 调用前 | `ctx.raise_if_cancelled()` |
| 工具 batch 开始前 | 检查 cancel；已取消则全部补 skipped result |
| 顺序工具之间 | 检查 cancel；当前工具跑完，后面的补 skipped result |
| 并行工具 batch | 已经在跑的调用跑完；batch 结束后再检查 cancel |
| 工具 batch 结束后 | 检查 cancel；结束当前 run |

### 6.5 未执行工具：补 skipped tool_result

取消后，已经发出但还没执行的 tool_call 补一条结果：

```text
assistant(tool_calls: A, B, C)
tool(A result)
tool(B: [Tool execution cancelled — B was skipped. Run cancelled.])
tool(C: [Tool execution cancelled — C was skipped. Run cancelled.])
user: Detect user cancel the agent execution.
```

`complete_incomplete_function_calls()` 的规则：

- 处理当前 `invocation_id` 的 tool_call。
- 已有 function_response 的 call 跳过。
- 每个缺失的 call 补一条 `Part.from_function_response(...)`，`response` 带 `status="cancelled"` 和 message。
- 一个 event 可以装多个 skipped response；序列化时拆成多条 tool message，顺序与 tool_calls 一致。
- 补完再 append cancellation message，最后 yield `AgentCancelledEvent`。

为什么放在 Runner 层：

```text
LlmAgent ─────────┐
LangGraphAgent ───┤
GraphAgent ───────┼──► Runner except RunCancelledException
ClaudeAgent ──────┘          │
                             ├── complete_incomplete_function_calls()
                             ├── append cancellation message
                             └── yield AgentCancelledEvent
```

不同 Agent 的循环结构不一样，逐个改容易漏。Runner 能看到本次 invocation 的完整 `session.events`，统一补齐最可靠；Agent 侧负责在 checkpoint 停止启动新工具。

## 7. Steering 设计

### 7.1 队列语义

Steering 是 FIFO queue：

```text
send_steering(msg1) ──► [msg1]
send_steering(msg2) ──► [msg1, msg2]
send_steering(msg3) ──► [msg1, msg2, msg3]

consume_steering()  ──► Content(parts=[msg1.parts, msg2.parts, msg3.parts])
```

- 空 Content（`content is None` 或 `not content.parts`）返回 `Status(ok=False, error_msg="steering content is empty")`。
- text、`inline_data`、`file_data`、`function_response` 等 part 都原样接收。
- `Status(ok=True)` 表示消息已经入队；模型在下一次 LLM 请求前读取。

### 7.2 写入、消费与持久化

```text
UI / Worker B                  master server                  Worker A（owner）
    │                              │                              │
    │ POST /steering ─────────────►│ send_steering RPC            │
    │ 202 Accepted                 │ _ServerRun.steering.append   │
    │                              │                              │
    │                              │◄──── poller: sync ───────────┤
    │                              │ return [payload]             │
    │                              │                              │ _ClientRun.steering.extend
    │                              │                              │
    │                              │      checkpoint: consume     │
    │                              │                              │ 本地 deque 取空
    │                              │                              │ merge + mark
    │                              │                              │ yield Event(visible=False)
    │                              │                              │ → Runner append_event
    │                              │                              │ → build_request 从 session 读取
```

单 worker 模式使用同样的调用链，`master server` 换成 `_LocalRun.steering` deque。

- local：`deque[Content]`，直接存对象。
- UDS：发送方把 `Content.model_dump_json(exclude_none=True)` 放进一次性短连接的 `send_steering` 请求；master 保存 payload 字符串，owner 的 poller 在 `sync` 时取回 payload 并还原到 `_ClientRun.steering`；owner 自己发送时直接 append 本地 deque。
- `consume_steering()` 在 public 层按 FIFO 把所有 `Content.parts` 合并成一个 `Content(role="user")`；没有消息返回 `None`。
- LlmAgent 把合并后的 Content 交给 `mark_steering_content()`，再包装成 `visible=False`、`author="user"` 的 Event yield 给 Runner。
- Runner 对非 partial 事件先 `append_event(session)`，再判断 `visible`；因此 steering Event 会写进 session，但不会 yield 给客户端。
- `visible=False` 只控制外部可见性，不控制模型可见性；Event 的 `model_flags` 默认仍是 model-visible，所以 history builder 会把它带进后续 LLM 请求。

### 7.3 注入点

在 `LlmAgent` 每轮循环开头、cancel 和 limit 检查之后、`build_request()` 之前：

```python
await ctx.raise_if_cancelled()

steering_content = await ctx.consume_steering()
if steering_content is not None:
    steering_event = Event(
        invocation_id=ctx.invocation_id,
        author="user",
        content=mark_steering_content(steering_content),
        branch=ctx.branch,
        visible=False,
    )
    yield steering_event
    accumulate_content(steering_event)

request = LlmRequest(model=model_instance.name)
await default_request_processor.build_request(request, self, ctx, override_messages=local_messages)
```

顺序很重要：cancel 优先。这一轮被取消时，steering 不会被消费。

- `yield` 之后 Runner 会把 Event 写进 session；LlmAgent 恢复执行时，`build_request()` 从 session 读到它。
- `accumulate_content(steering_event)` 只在 `override_messages` 模式（TeamAgent member）下真正起作用：该模式不走 session history，需要把 steering 同步到 `local_messages`。
- CHECKPOINT 2 仍然保留 `raise_if_cancelled()`，但不再消费 steering。

`mark_steering_content()` 是 `_public.py` 里的纯函数：

```python
def mark_steering_content(content: Content) -> Content:
    parts = [Part(text=_STEERING_PREFIX)]
    parts.extend(content.parts or [])
    parts.append(Part(text=_STEERING_SUFFIX))
    return content.model_copy(update={"parts": parts})
```

文本 marker：

```text
[USER STEERING — sent while the task was running]
also check Shanghai
[/USER STEERING]
```

- `mark_steering_content` 只改 `parts`，`role` 等字段原样保留。
- LlmAgent 不再直接 append `request.contents`；它 yield 一个 hidden Event，由 Runner 统一持久化。
- steering Event 不进入客户端事件流，但会进入模型历史。

### 7.4 请求组装

Steering 通过 session history 进入 LLM 请求：

```text
assistant(tool_calls: A, B)
tool(A result)
tool(B result)
user(steering marker, visible=False)   ← Runner append_event
        │
        ▼
RequestProcessor.build_request()
        │
        ├── assistant(tool_calls)
        ├── tool(A result)
        ├── tool(B result)
        └── user(steering marker)
        │
        ▼
LLM request
```

- steering Event 在 `build_request()` 之前写入 session，所以它排在 tool response 之后、下一次 assistant 输出之前。
- 多个 steering 已经由 `consume_steering()` 合并成一条 `Content`，marker 只包一层。
- tool response 和 steering 可能都是 `role="user"`；HistoryProcessor 会把连续的 user-role content 合并，所以请求里它们可能出现在同一条 user content 中，steering marker 仍然保留。
- steering 现在是真实的 session event，当前 run 的后续 LLM 请求、以及同一 session 的后续 turn 都会看到它；它不会出现在客户端事件流里。
- `override_messages` 模式下 `build_request` 不读 session，steering 通过 `accumulate_content` 进入 `local_messages`，效果一致。

### 7.5 与工具、取消的关系

- Steering 期间工具 batch 按原计划执行。batch 结束后，steering 在下一轮循环开头被消费，并以 hidden Event 写入 session，供下一次 LLM 请求使用。
- Steering 在 LLM 流式输出中到达时，当前流继续运行；流结束后，如果有 tool call，先跑工具，再在下一轮循环开头消费 steering；如果当前流就是最终回复，loop 退出前会再取一次 owner 本地队列，已同步到本地的 steering 补一轮 iteration 交付。
- Steering 和 cancel 同时到达时，cancel 优先：先 `raise_if_cancelled()`，本轮不消费 steering，cancel 时残留的消息直接丢弃。
- 跨 worker 下消息还在 master 的 `_ServerRun`、没同步到 owner 时 run 就结束，会随 `_ServerRun` 一起释放；这类 `poll_interval` 窗口内的消息不保证交付。

## 8. A2A / AG-UI 集成

A2A 和 AG-UI 都通过 `Runner` 的公开方法使用控制面，不直接调用 `_LocalManager` / `_UnixSocketManager`，也不直接读写 master 的 run 表。A2A v1 已按这个方向落地；AG-UI 的 cancel 已迁移到 `Runner`，steering 仍待迁移，下面分别说明。

### 8.1 A2A

A2A v1 的入口是 `TrpcA2aAgentService`。Service 负责 agent card 和每次请求创建 executor；executor 持有 `Runner`，cancel 和 execute 都落到 Runner 的公开方法上，不直接读写 master 的 run 表。

```text
┌────────────────────────────────────────┐
│ TrpcA2aAgentService                    │
│                                        │
│ initialize() -> build AgentCard        │
│                                        │
│ execute() / cancel()                   │
│   -> _create_executor()                │
│        -> Runner(app_name, agent,      │
│                  session_service)      │
└────────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────┐
│ TrpcA2aAgentExecutor                   │
│                                        │
│ execute() -> Runner.run_async()        │
│ cancel()  -> Runner.cancel_run_async() │
└────────────────────────────────────────┘
```

CancelTask：

```text
Client ──CancelTask(task_id)──► Worker B（cancel）
                                   │
                                   ├─ task.metadata -> app_name/user_id/session_id
                                   │    （不完整时走 user_id_extractor）
                                   └─ Runner.cancel_run_async()
                                          │
                                          ▼
                                   Worker A（owner run）
                                          ├─ checkpoint 抛 RunCancelledException
                                          ├─ 补 skipped tool_result / 落取消标记
                                          └─ yield AgentCancelledEvent
                                                  │
                                                  ▼
Client ◄── TaskStatus(canceled) ── executor 消费事件并 enqueue
```

- `execute()` 先把 `app_name/user_id/session_id` 写进 working status 的 metadata。a2a-sdk 的 `TaskManager` 会把这些字段合并到持久化 Task 上，`cancel()` 再从 `context.current_task.metadata` 取。
- metadata 不完整时，`cancel()` 回退到 `get_user_session_id(context, user_id_extractor)`。
- `cancel()` 只负责把取消信号交给 Runner。真正的 `canceled` 由 owner 的 `execute()` 写回：Runner yield `AgentCancelledEvent` 后，executor 把取消状态 enqueue 到原事件流，并把 Runner 生成器消费到结束（见 §4.1）。
- 没有活跃 run 时，`cancel()` 直接 enqueue 一条 canceled 状态，message 为 `No active task found to cancel`。
- 本地模式下 `cancel_wait_timeout` 控制等待清理的时间；UDS 模式下 cancel 通过 Unix Socket 发布请求，不等待 owner。

Steering 不属于 A2A 协议，`TrpcA2aAgentService` 不提供 `send_steering`。示例在 `create_a2a_application()` 返回的同一个 Starlette app 上注册应用层路由：

```text
root_agent + APP_NAME + session_service
        │
        ├─ TrpcA2aAgentService(app_name=APP_NAME, ...)
        │        └─ create_a2a_application(...)
        │
        └─ register_steering_route(app, path,
                                   agent=root_agent, ...)
                 │
                 ▼
        POST /agents/weather_agent/steering
         {user_id, session_id, content}
                 │
                 ├─ 闭包持有的 root_agent
                 ├─ Runner(app_name=APP_NAME, agent=root_agent,
                 │         session_service=session_service)
                 └─ Runner.send_steering(user_id, session_id, content)
```

- 路由 handler 通过闭包拿到对应 Agent，不维护全局 Agent registry，也不查询 TaskStore。
- A2A executor 和 steering handler 必须使用同一个 `app_name`、`user_id`、`session_id`，否则队列键对不上。
- A2A 适配层默认优先用 `call_context.user.user_name`，没有时用 `A2A_USER_<context_id>`；该示例里 `context_id` 就是 `session_id`。配置了 `user_id_extractor` 时以 extractor 的返回值为准。
- 示例路由可以用 `status.ok` 决定是返回 202 还是 400；失败时把 `status.error_msg` 放进响应体。需要更细的 HTTP 语义时，由业务根据 `error_msg` 自行映射。

### 8.2 AG-UI

AG-UI 的 cancel 已按 `Runner` 公开接口落地；下面的 `steer(thread_id, content)` 和全局 `/steering` 仍是目标接口。

```text
┌──────────────────────────────────────┐
│              AgUiAgent               │
│                                      │
│ _active_executions: thread_id -> ... │
│                                      │
│ run(input)                           │
│ cancel_run(session_id, app, user)    │
│ steer(thread_id, content)            │
└──────────────┬───────────────────────┘
               │
               ├── ExecutionState: thread_id, app_name, user_id, task, queue
               │
               └── Runner.cancel_run_async() / Runner.send_steering()
```

SSE 断连取消：

```text
Client                  Worker A
  │                        │
  │ SSE connection ───────►│
  │                        │
  │ close ────────────────►│ event_generator finally
  │                        │        │
  │                        │        ▼
  │                        │ AgUiAgent.cancel_run()
  │                        │        │
  │                        │        ▼
  │                        │ Runner.cancel_run_async()
  │                        │        │
  │                        │        └── 发 cancel 请求
  │                        │
  │                        └── Agent 在 checkpoint 完成清理
```

取消只通过 `Runner` 发信号；后台 task 等 Runner 消费到 `AgentCancelledEvent` 后自然结束，不用 `task.cancel()`。

`/steering` 由 `AgUiService` 注册全局 `POST /steering`，body 里的 `agent_uri` 用来分发；如果服务只有一个 agent，`agent_uri` 可以省略。


## 9. 一致性与边界

### 9.1 单 run 约束

一个 session 同时只有一个活跃 run。

- local 模式：`_LocalManager` 发现同 key 的活跃 run 时抛 `RuntimeError`。
- UDS 模式：master 的 `register` RPC 发现同 key 时返回 `duplicate`；manager 关闭连接并抛 `RuntimeError`。

run 结束时：

- local 模式：`unregister_run()` pop 本地 run，并 set `cleanup_event`。
- UDS 模式：`unregister_run()` pop 本地 `_ClientRun`，发一次性 `unregister` RPC 删除 master 的 `_ServerRun`，然后取消 poller、关闭连接。

这样 cancel 和 steering 的目标唯一。

### 9.2 单 worker 与多 worker 的差异

| 场景 | 控制状态 | cancel 写入 | steering 写入 |
|---|---|---|---|
| 单 worker | `_LocalManager` + `_LocalRun` | `cancel_event` | `deque[Content]` |
| 多 worker | `_UnixSocketManager` + `_ClientRun` + master `_UnixSocketServer` | owner `event` + `send_cancel` RPC；跨 worker 由 poller `sync` 同步 | 本地 `_ClientRun.steering`；跨 worker 由 poller 从 master 同步 |

两种模式下，Agent 的 checkpoint 都只通过 `InvocationContext` 访问控制面，读本地状态。多 worker 下每个 run 在 `register_run()` 后启动一个 per-run poller，按 `poll_interval`（默认 0.1s）发 `sync` RPC；`get_cancel_event()` 只返回本地 event。

### 9.3 交付语义

- cancel 是 best-effort signal；本进程内 cancel 立即 set 本地 event，跨 worker cancel 最多一个 `poll_interval` 后由 poller 同步，Agent 在下一个 checkpoint 停止。
- UDS 模式下 `cancel_run()` 发布后立即返回；owner 的 poller 在一个 `poll_interval` 内（默认 0.1s）把 master 的取消状态同步到本地 Event，随后 Agent 在下一个 checkpoint 退出。
- `get_cancel_event()` 不启动额外任务；poller 对每个 run 始终存在。
- steering 是 best-effort queue；`Status(ok=True)` 表示已入队，模型在下一次 LLM 请求前读取。跨 worker 投递延迟上限是一个 `poll_interval`。
- `consume_steering()` 把多条 steering 按 FIFO 合并成一条 user Content；LlmAgent 把它包装成 `visible=False` Event 写入 session，客户端事件流不可见，但模型历史可见。它会继续影响同一 session 的后续 LLM 请求。
- 最后一次 LLM 请求期间到达、且已同步到 owner 本地队列的 steering，loop 退出前会补一轮 iteration 交付；仍在 master 未同步的、或 cancel 时残留的，随 `_ClientRun` / `_ServerRun` 一起释放，不保证交付。
