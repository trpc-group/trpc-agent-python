# trpc-agent-python Cancel 与 Steering 机制设计

本文描述 trpc-agent-python 的 run 控制面：取消（cancel）和插话（steering）。控制状态由 `trpc_agent_sdk/cancel/_public.py` 里的 `_manager` 持有，默认是进程内的 `_LocalManager`；多 worker 部署时，master 在 `server.serve()` 之前调用 `init_shared_mem()`，`_manager` 切换成 `_SharedManager`，底层使用 `/dev/shm/trpc-agent-py/cancel`。

## 1. 概述

Cancel 和 Steering 都是“发给正在运行的 run 的控制信息”，语义不同：

| 控制信息 | 语义 | 数据结构 | 消费方式 |
|---|---|---|---|
| cancel | signal，最新值覆盖 | `asyncio.Event` + 共享内存 cancel 字节 | Agent 在 checkpoint 读取，或 await `get_cancel_event()` |
| steering | queue，按 FIFO 交付 | `deque[Content]` 或共享内存 record 队列 | Agent 在 LLM 请求前一次取空并合并；LlmAgent 以 hidden Event 写回 session |

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
                      └── _SharedManager
                            ├── _runs: SessionKey → _SharedRun
                            └── _mem: _SharedMem
```

`_manager` 在导入时是 `_LocalManager`；`init_shared_mem()` 成功后替换为 `_SharedManager(mem)`；`destroy_shared_mem()` 关闭并替换回 `_LocalManager`。运行期间不切换。

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

### 2.2 多 worker（共享内存）

```text
+------------------------------- 同一台机器 -------------------------------+
|                                                                        |
|  +--------------------------+          +--------------------------+    |
|  | Worker A（run owner）    |          | Worker B（cancel/steering）|   |
|  |                          |          |                          |    |
|  | Runner                   |          | 服务端取消/插话入口       |    |
|  |   └── _SharedManager     |          |   └── _SharedManager     |    |
|  |         ├── _runs        |          |         └── _mem          |    |
|  |         └── _mem --------+--+       |              +-----------+--+ |
|  +--------------------------+  |       +--------------+--------------+ |
|                                |                      |                |
|                                v                      v                |
|  +------------------------------------------------------------------+  |
|  | /dev/shm/trpc-agent-py/cancel                                    |  |
|  |                                                                  |  |
|  |  entry[hash(session)]                                            |  |
|  |    ├── owner_pid / generation / key_digest                       |  |
|  |    ├── cancel flag                                               |  |
|  |    └── steering records + count                                  |  |
|  +------------------------------------------------------------------+  |
+------------------------------------------------------------------------+
```

- master 在 `server.serve()` 之前调用 `init_shared_mem()`，创建 segment；fork 出来的 worker 继承同一个 `_SharedManager` / `_SharedMem`。当前只支持 fork，不支持 spawn/attach。
- owner 进程的 `_SharedRun.slot` 指向共享 entry；非 owner 进程通过 `_SharedMem.find(key)` 定位同一个 entry。
- owner 进程首次调用 `get_cancel_event()` 时会为该 run 启动 watcher task；watcher 按 `cancel_poll_interval`（默认 0.1s）检查共享 cancel 字节，发现取消后 set 本地 `asyncio.Event`。非 owner 进程没有对应 run，不会启动 watcher。
- 跨 worker 的 cancel 写共享 cancel 字节；跨 worker 的 steering 写共享 record 队列。
- 非 owner 的 `cancel_run()` / `send_steering()` 在写入共享 entry 后立即返回；真正的执行和清理发生在 owner 进程。

用户用法：

```python
# master 进程，在 server.serve() 之前初始化
from trpc_agent_sdk.cancel import SharedMemoryConfig, init_shared_mem, destroy_shared_mem

init_shared_mem(
    SharedMemoryConfig(
        max_active_runs=256,
        steering_queue_size=4,
        max_steering_bytes=1024,
    )
)

try:
    server = trpc.new(config_path)
    register_service(SERVICE_NAME, create_service())
    server.serve()
finally:
    destroy_shared_mem()
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

### 2.3 控制状态读取

Agent 不接触 Manager，也不接触共享内存，只通过 `InvocationContext`：

```text
Agent checkpoint
      │
      ├── ctx.raise_if_cancelled()
      │        └── cancel.raise_if_cancelled(session_key)
      │                 └── _manager.raise_if_cancelled(key)
      │                          ├── _LocalManager: cancel_event.is_set()
      │                          └── _SharedManager: cancel_event 或 mem.is_cancelled(slot)
      │
      ├── ctx.get_cancel_event()
      │        └── cancel.get_cancel_event(session_key)
      │                 └── _manager.get_cancel_event(key)
      │                          └── _SharedManager: 首次调用启动 per-run watcher
      │
      └── ctx.consume_steering()
               └── cancel.consume_steering(session_key)
                        ├── _manager.drain_steering(key) -> List[Content]
                        ├── 合并为一个 Content（FIFO parts）
                        └── LlmAgent: mark + yield Event(visible=False)
```

控制信息在 Agent 走到 checkpoint 时生效。cancel 优先于 steering：先 `raise_if_cancelled()`，再 `consume_steering()`。LlmAgent 把消费到的 steering 包装成 `visible=False` Event yield 给 Runner，由 Runner 持久化到 session。

### 2.4 共享内存开销

```text
header（64 字节）
  +
max_active_runs × entry_size
  =
256 × 4.2 KiB
  ≈ 1.03 MiB
```

默认配置下整块 segment 约 1.03 MiB，按 `max_active_runs` 预分配；单个 entry 约 4.2 KiB。相对于 worker 进程的常驻内存可以忽略，容量由 `SharedMemoryConfig` 配置。

## 3. 核心对象

### 3.1 模块布局

```text
trpc_agent_sdk/
└── cancel/
    ├── __init__.py          只做 re-export
    ├── _public.py           _manager 单例 + 公开函数 + mark_steering_content
    ├── _local_manager.py    _LocalManager、_LocalRun
    ├── _shared_manager.py   _SharedManager、_SharedRun
    ├── _shared_mem.py       _SharedMem、_Slot、mmap 段管理
    ├── _types.py            SessionKey、SteeringStatus、SharedMemoryConfig
    └── _cleanup.py          取消文案、skipped tool_result 补齐
```

公开接口：

```python
from trpc_agent_sdk.cancel import (
    SessionKey,
    SharedMemoryConfig,
    SteeringStatus,
    cancel_run,
    consume_steering,
    destroy_shared_mem,
    get_cancel_event,
    init_shared_mem,
    mark_steering_content,
    raise_if_cancelled,
    register_run,
    send_steering,
    unregister_run,
)
```

`register_run` / `unregister_run` 是 Runner 专用的框架生命周期接口，Agent 代码不应该直接调用。

| 接口 | 使用方 | 作用 |
|---|---|---|
| `register_run(key)` | Runner | 占用一个 run；冲突或表满时抛 `RuntimeError` |
| `unregister_run(key)` | Runner | 幂等收尾，释放 run |
| `cancel_run(key, timeout)` | Runner / 服务端 | 请求取消；local 等待 cleanup，shared 发布后立即返回 |
| `send_steering(key, content)` | Runner / 服务端 | 写入一条 steering |
| `consume_steering(key)` | `InvocationContext` | 取空 steering 并合并成一个 `Content` |
| `mark_steering_content(content)` | Agent 实现 | 给 steering `Content` 加 prefix/suffix marker，外层再包装为 hidden Event |
| `raise_if_cancelled(key)` | `InvocationContext` | checkpoint 检查并抛异常 |
| `get_cancel_event(key)` | `InvocationContext` | 返回 owner 进程内的取消 Event；首次调用启动 per-run watcher |
| `init_shared_mem(config)` / `destroy_shared_mem()` | 进程启动/关闭 | 共享内存生命周期 |

### 3.2 对象职责

```text
                 +-----------------------------------+
                 |          _public._manager         |
                 |  _LocalManager | _SharedManager   |
                 +----------------+------------------+
                                  |
                +-----------------+-----------------+
                |                                   |
     +----------v----------+             +----------v----------+
     |    _LocalManager    |             |    _SharedManager   |
     |  _runs: Dict[key,   |             |  _runs: Dict[key,   |
     |          _LocalRun] |             |          _SharedRun]|
     |                     |             |  _mem: _SharedMem   |
     +----------+----------+             +----------+----------+
                |                                   |
     +----------v----------+             +----------v----------+
     |      _LocalRun      |             |      _SharedRun     |
     |  cancel_event       |             |  slot: _Slot        |
     |  cleanup_event      |             |  cancel_event       |
     |  steering: deque    |             |  watch_task         |
     +---------------------+             +----------+----------+
     +---------------------+                        |
                                         +----------v----------+
                                         |      _SharedMem     |
                                         |  mmap segment       |
                                         |  entry table        |
                                         |  fcntl + threading  |
                                         +---------------------+
```

- `_LocalManager` 和 `_SharedManager` 没有共同基类，公开方法集合保持一致：`register_run`、`unregister_run`、`cancel_run`、`send_steering`、`drain_steering`、`raise_if_cancelled`、`get_cancel_event`；`_SharedManager` 额外持有 `_SharedMem` 并提供 `close`。
- `_LocalRun` 只有 `key`、`cancel_event`、`cleanup_event`、`steering`。
- `_SharedRun` 持有 `key`、`slot`、`cancel_event`、`watch_task`；没有 `cleanup_event`，shared 模式下 `cancel_run` 不等待清理。
- `_SharedMem` 只做字节层的事：entry 分配、cancel 字节、steering record、`fcntl` 锁；不感知 `asyncio.Event`，也不持有 run 生命周期。

### 3.3 控制流程

```text
cancel_run(key)
    │
    ├── 本进程有 run
    │      ├── _LocalManager: cancel_event.set()
    │      │                   await cleanup_event.wait()（最多 timeout）
    │      └── _SharedManager: cancel_event.set()
    │                          publish_cancel(slot)
    │                          立即返回
    │
    └── 本进程没有 run
           ├── _SharedMem.find(key)
           ├── publish_cancel(slot)
           └── 发布后立即返回

send_steering(key, content)
    │
    ├── content 为空 ──► UNSUPPORTED_CONTENT
    │
    ├── 本进程有 run ──► enqueue
    ├── 本进程没有 run ──► find(key) + enqueue
    └── 找不到 run ──► NO_ACTIVE_RUN

consume_steering(key)
    │
    ├── drain_steering(key) -> List[Content]
    ├── 空 ──► None
    └── 按 FIFO 合并 parts -> Content(role="user")
```

## 4. Runner 生命周期

```text
Runner.run_async()                  cancel 模块                 manager
      │                                │                          │
      │ register_run(key)              │                          │
      ├───────────────────────────────►├─────────────────────────►│ allocate slot
      │                                │                          │ _runs[key] = run
      │ ctx.session_key = key          │                          │
      │ agent loop ...                 │                          │
      │                                │                          │
      │ 取消分支：                     │                          │
      │ unregister_run(key)            │                          │
      ├───────────────────────────────►├─────────────────────────►│ pop run
      │ yield AgentCancelledEvent      │                          │ release slot
      │                                │                          │
      │ finally：                      │                          │
      │ unregister_run(key)            │                          │ 已 pop，直接返回
      └───────────────────────────────►└─────────────────────────►│
```

- `register_run()` 在 session 用户消息写入之前调用；被拒绝的并发 run 不会留下用户消息。
- 初始化阶段 `register_run()` 失败（同一 session 已有活跃 run，或共享表满）会抛 `RuntimeError`；Runner 记录 trace 后原样向上抛，不进入 agent 执行。
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

## 5. 共享内存 entry

### 5.1 Segment 与 entry

共享内存用文件承载的 mmap，位置固定在：

```text
/dev/shm/trpc-agent-py/cancel
```

```text
+----------------------------------------------------------------------+
| Header (64B)                                                         |
|                                                                      |
| magic (8B) | owner_pid (u32) | created_at_ms (u64) | reserved        |
+--------------+--------------+--------------+------------------------+
| Entry 0      | Entry 1      | Entry 2      | ...                    |
+--------------+--------------+--------------+------------------------+
```

header 只保存标识这块 segment 的元信息（magic、创建者 pid、创建时间），`SharedMemoryConfig` 不持久化：worker 通过 fork 继承内存中的 config 对象，`init_shared_mem()` 每次都会重建 segment。

一个 entry 对应一个活跃 run。定位方式：

```text
digest = blake2b(app_name + "\0" + user_id + "\0" + session_id, digest_size=16)
index  = int(digest) % max_active_runs

从 index 开始线性探测：
  entry.state != RUNNING 或 entry.key_digest 匹配 ──► 候选
  owner_pid 不存活                                  ──► 可回收
```

### 5.2 Entry 结构

```text
+----------------------------------------------------------------------+
| state             u8      FREE / RUNNING                             |
| generation        u64     每次 allocate 递增                          |
| owner_pid         u32     owner 进程号                                |
| key_digest        u128    blake2b-128                                 |
| cancel_requested  u8      0 / 1                                       |
| steering_count    u32     当前积压条数                                |
| reserved          20B                                                 |
|                                                                      |
| record[0]: u32 len | payload <= max_steering_bytes                    |
| record[1]: u32 len | payload <= max_steering_bytes                    |
| ... 共 steering_queue_size 条                                         |
+----------------------------------------------------------------------+
```

| 字段 | 作用 |
|---|---|
| `state` | FREE / RUNNING |
| `generation` | 每次 allocate 递增；release 只清理自己那一代，防止 ABA |
| `owner_pid` | owner 进程号，allocate 时用于回收已退出进程留下的 entry |
| `key_digest` | 128-bit blake2b；entry 定长 |
| `cancel_requested` | cancel signal |
| `steering_count` | 当前积压条数；入队写到 `record[count]`，消费后归零 |
| `record` | `u32 length + payload`；payload 是 `Content.model_dump_json(exclude_none=True)` |

容量默认值：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `max_active_runs` | 256 | 同机并发 run 上限 |
| `steering_queue_size` | 4 | 每个 run 最多积压 4 条插话 |
| `max_steering_bytes` | 1024 | 单条 steering payload 上限 |
| `entry_size` | ~4.2 KiB | meta + 4 × (4B + 1KB) |
| 总内存 | ~1.03 MiB | 256 × 4.2 KiB |

### 5.3 状态机

```text
             allocate
   FREE ─────────────────► RUNNING
     ▲                       │
     │                       │ publish_cancel()
     │                       ▼
     │                  cancel_requested = 1
     │                       │
     │                       │ release()（generation 匹配）
     │                       ▼
     └──────────────────── FREE

   RUNNING ── owner 进程退出 ──► 下一次 allocate 时回收
```

- `allocate()` 在 FREE entry 或 owner 已退出的 RUNNING entry 上分配，`generation += 1`，重置 cancel flag 和 steering count。
- `publish_cancel()` 写 `cancel_requested=1`，entry 保持 RUNNING。
- `enqueue()` 操作 record 队列，entry 保持 RUNNING。
- `release()` 在 digest + generation 匹配时清空 entry，置 FREE。
- 下一次 `allocate()` 遇到同 key 的 RUNNING entry 时，通过 `owner_pid` 判断原 owner 是否还活着：活着则抛 `RuntimeError`，已退出则回收。

### 5.4 锁与并发

共享内存是 mmap 出来的普通文件，跨进程读写不能只靠 `threading.Lock`。这里用两层锁：

- `fcntl.lockf`：POSIX record lock，锁住文件描述符上的一个字节区间，负责跨进程互斥。
- `threading.Lock`：POSIX 锁是进程级的，同进程的多个线程不会互相阻塞，所以进程内再包一层。

entry 的字节布局和锁位置：

```text
entry 的 mmap 布局

+---------------------------------------------------------------+
| meta (64B)                                                    |
| 0     1..7  8..15     16..19   20..35   36    37..39  40..43  |
| state   pad generation owner_pid digest cancel   pad   count  |
+---------------------------------------------------------------+
| record[0] | record[1] | ...                                   |
+---------------------------------------------------------------+

lock_alloc:     segment 偏移 0 的 1 个字节
lock_entry(i):  segment 偏移 HEADER_SIZE + i * entry_size 的 1 个字节
```

图中的 `cancel` / `count` 对应字段 `cancel_requested` / `steering_count`。

锁的获取顺序：

```python
lock_alloc():
    with self._alloc_lock:                    # 同进程
        fcntl.lockf(self._fd, LOCK_EX, 1, 0)  # 跨进程
            ...
        fcntl.lockf(self._fd, LOCK_UN, 1, 0)

lock_entry(i):
    with self._entry_locks[i]:                # 同进程
        fcntl.lockf(self._fd, LOCK_EX, 1, off)
            ...
        fcntl.lockf(self._fd, LOCK_UN, 1, off)
```

锁只保护很短的内存操作，不覆盖 LLM 调用或工具执行。`allocate()` 的顺序是 `lock_alloc -> lock_entry`，其他路径只拿 `lock_entry`，不存在反向获取，不会死锁。

#### 各操作的锁交互

| 操作 | entry 锁 | 读/写内容 | 说明 |
|---|---|---|---|
| `publish_cancel` | 有 | `cancel_requested=1` | 校验 generation/digest 后写入 |
| `is_cancelled` | 无 | `state/generation/digest/cancel_requested` | lock-free，见下 |
| `enqueue` | 有 | `steering_count`、record payload、`count+1` | 保证记录和 count 一致 |
| `drain` | 有 | `steering_count`、records、`count=0` | 一次取空 |
| `find` | 扫描无锁，确认有锁 | entry meta | 先过滤候选，再加锁复核 |
| `release` | 有 | `state=FREE` | 只清理同 generation |
| `allocate` | alloc + entry | `state=RUNNING` 等 | 先锁分配区，再锁目标 entry |

cancel 写和 steering 读写的时序：

```text
publish_cancel(slot)                     enqueue(slot, content)
      │                                        │
      ├── lock_entry(i)                        ├── lock_entry(i)
      ├── 校验 slot                            ├── 校验 slot
      ├── cancel_requested = 1                 ├── count < queue_size ?
      └── unlock                               ├── record[count] = payload
                                               ├── count += 1
                                               └── unlock

drain(slot)
      │
      ├── lock_entry(i)
      ├── 校验 slot
      ├── 读 count 和 records
      ├── count = 0
      └── unlock
```

为什么 steering 必须加锁：`steering_count` 和 records 是多个字段。如果写者在“写记录”和“加 count”之间被打断，读者可能看到 `count+1` 但记录还没写完。锁把这两步合成一个临界区。

#### cancel 读为什么不加锁

`is_cancelled(slot)` 是 agent 每个 checkpoint 都会调的热路径，加 `fcntl` 会多一次系统调用。它的读法是：

```python
view = self.entry_view(slot.entry_index)
state, generation, _owner_pid, digest, cancel, _count = _ENTRY_META_STRUCT.unpack_from(view, 0)
if not self._matches(state, generation, digest, slot):
    return True
return bool(cancel)
```

- `cancel` 在 run 生命周期内只会从 0 变 1，不会被清回 0。
- 读到旧的 0，最坏是晚一个 checkpoint 发现取消；读到 1 就是取消。
- 如果 entry 已经被 release 或复用，`state/generation/digest` 对不上，直接返回 `True`，保守地当作已取消。
- 因此不需要锁；这是用“单调 flag + generation 校验”换掉每次 checkpoint 的加锁开销。

#### find 的 double-check

```text
find(key):
    for entry in table:
        read meta without lock              # 快速过滤，不加锁扫全表
        if digest matches and RUNNING and owner alive:
            lock_entry(i)                   # 只锁候选 entry
            re-read meta
            if still matches:
                return slot
            unlock
    return None
```

第一次无锁扫描负责过滤，第二次加锁复核负责排除“扫描后被 release / 复用”的 race。这样不用给整张表加锁。

#### 为什么 fcntl 和 threading.Lock 都要

POSIX `lockf` 锁属于进程，不属于线程：

- 同进程两个线程对同一区间加 `LOCK_EX`，内核认为锁已经属于该进程，不会阻塞第二个线程。
- 所以 `threading.Lock` 负责进程内互斥，`fcntl.lockf` 负责进程间互斥。

```text
进程 A 线程 1 ──┐
进程 A 线程 2 ──┼── threading.Lock ──┬── fcntl.lockf ──► 共享 entry
                                     │
进程 B ──────────────────────────────┘
```

- `fork` 后子进程继承 mmap 和 fd，但 POSIX record lock 不随 fork 继承；`_ensure_process_state()` 在 PID 变化时重建 `threading.Lock`，后续操作重新加 `fcntl` 锁。
- 进程退出时内核自动释放它持有的 `lockf` 锁，不会留下死锁文件。
- `lock_alloc` / `lock_entry` 用阻塞的 `LOCK_EX`；只有 master 选举锁用 `LOCK_EX | LOCK_NB`，配合 50ms 重试和 2s 超时。

其余约定：

- 序列化在锁外完成；锁内只做长度检查和 memcpy。
- 所有长度和 count 都做 bounds check；损坏的 record 丢弃并重置该 run 的 steering count。
- 读写都在 mmap 上完成，`/dev/shm` 本身是内存。
- segment 权限为 0600，同机同用户可见，适用于可信的同机部署。

### 5.5 init_shared_mem / destroy_shared_mem

```python
@dataclass(frozen=True)
class SharedMemoryConfig:
    max_active_runs: int = 256
    steering_queue_size: int = 4
    max_steering_bytes: int = 1024
    cancel_poll_interval: float = 0.1  # seconds; shared mode only


def init_shared_mem(config: SharedMemoryConfig | None = None) -> None: ...


def destroy_shared_mem() -> None: ...
```

`cancel_poll_interval` 是进程级运行时策略，只控制 owner 进程里 per-run watcher 的轮询周期。它不写入 segment header；worker 通过 fork 继承同一个 config 对象。

创建时序：

```text
master 进程
    │
    ├── init_shared_mem(config)
    │       │
    │       ├── open /dev/shm/trpc-agent-py/cancel.lock
    │       ├── lockf(LOCK_EX | LOCK_NB)
    │       │
    │       ├── 拿到锁：
    │       │      ├── unlink 旧的 segment
    │       │      ├── O_CREAT | O_EXCL 创建新文件
    │       │      ├── ftruncate(total_size)
    │       │      ├── mmap + 初始化 header
    │       │      └── 持有 lock fd
    │       │
    │       └── 没拿到锁：
    │              └── 重试 50ms，最多 2s，超时抛 RuntimeError
    │
    ├── server.serve()
    │       │
    │       ├── fork Worker A
    │       │      └── 继承 _SharedManager / _SharedMem / mmap
    │       │
    │       └── fork Worker B
    │              └── 继承 _SharedManager / _SharedMem / mmap
    │
    └── destroy_shared_mem()
            ├── _manager.close()           # release 所有 slot
            ├── _shared_mem.destroy_shared_mem(mem)
            │      ├── master: unlink segment
            │      └── 关闭 mmap / fd / master lock
            └── _manager = _LocalManager()
```

`init_shared_mem()` 的行为：

- 同一个进程重复调用，`_manager` 已经是 `_SharedManager` 且 mem 未关闭：
  - config 一致：直接返回。
  - config 不一致：抛 `ValueError`。
- 当前只支持 fork：master 在 `server.serve()` 之前初始化，worker 通过 fork 继承；spawn 方式和 attach 都不支持，也没有环境变量开关。
- 用 `fcntl.lockf` 对 `/dev/shm/trpc-agent-py/cancel.lock` 加排他锁。POSIX record lock 只跟踪持有它的进程，master 的锁随 master 生命周期存在。
- 拿不到锁时做短暂重试（默认 2 秒），覆盖旧 master 正在退出的窗口；超时后抛 `RuntimeError`。
- 拿到锁说明当前 host 没有存活的 master：先 `unlink` 旧 segment，再创建新文件。旧文件里的所有 entry 和 cancel/steering 状态全部丢弃。
- 创建时按 `max_active_runs / steering_queue_size / max_steering_bytes` 计算大小，写入 header，然后 `ftruncate`。
- segment 权限 0600。

`destroy_shared_mem()` 的行为：

- 如果 `_manager` 不是 `_SharedManager`，直接返回。
- `_manager.close()` release 所有 owner-side slot（shared 模式没有 cleanup Event）。
- `_shared_mem.destroy_shared_mem(mem)`：
  - master 进程 `unlink` segment，保留 lock file 供下次 init 复用同一 inode，释放 master 锁，关闭 mmap 和 fd。
  - worker 进程只关闭自己的 mmap / fd，不动 segment。
- 最后 `_manager` 替换回 `_LocalManager`。

### 5.6 异常退出与重启

```text
master 异常退出（kill -9 / crash）
        │
        ▼
内核释放 lockf 锁
        │
        ▼
旧 segment 文件留在 /dev/shm
        │
        ▼
下次 init_shared_mem()
        ├── 拿到锁
        ├── unlink 旧 segment
        └── 创建全新的 segment
                │
                ▼
        所有 entry / cancel / steering 状态归零
```

- **重启直接重置**：新 master 拿到锁后 unlink 旧文件并创建新文件，header、entry、cancel flag 全部重新初始化。
- **旧 worker 的 mmap 隔离**：新 segment 使用新 inode；仍然存活的旧 worker 继续映射旧 inode，新旧两块内存各自独立。
- **worker 异常退出**：worker 的 entry 仍是 RUNNING，但 `owner_pid` 已经不存在。下一次同 key 的 `allocate()` 发现 owner 不存活，直接回收这个 entry。
- **init 过程中 crash**：lock 由内核释放；下次 init 重新 unlink 并创建。
- **destroy 未执行**：下次 init 走重置流程。

### 5.7 容量与固定位置

```text
entry_size = align64(
    entry_meta_size
    + steering_queue_size × (4B + max_steering_bytes)
)

segment_size = header_size + max_active_runs × entry_size
```

- `max_active_runs` 控制整机最大并发 run 数，entry 表满时 `register_run()` 抛 `RuntimeError`。
- `steering_queue_size` 控制每个 run 最多积压多少条插话，满了返回 `QUEUE_FULL`。
- `max_steering_bytes` 控制单条 steering 消息大小，超了返回 `MESSAGE_TOO_LARGE`。
- 固定位置 `/dev/shm/trpc-agent-py/cancel` 意味着容量按整机总并发 run 数规划；entry 的 key digest 包含 `app_name + user_id + session_id`，不同服务的 run 在逻辑上隔离。

## 6. 取消设计

### 6.1 取消信号

```text
cancel_run(key)
      │
      ├── 本进程是 owner
      │      ├── _LocalManager: cancel_event.set()
      │      └── _SharedManager: cancel_event.set()
      │                          cancel_requested = 1
      │
      └── 跨 worker
             └── cancel_requested = 1
```

- local 模式 `cancel_run` 会等 `cleanup_event`。
- shared 模式只发布 cancel，不等 owner 清理；`timeout` 参数保留但忽略。
- 重复取消没有副作用；后写的 `1` 覆盖先写的 `1`。取消请求以 session 为粒度。

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

`cancel_run_async()` 默认最多等 1 秒；run 在超时前完成清理就立即返回，超时后返回 `True` 并记录 warning。

### 6.3 多 worker 取消

```text
Worker A（owner）              Shared Memory                Worker B（cancel）
    │                              │                              │
    │ register_run(key)            │                              │
    │                              │                              │
    │ get_cancel_event(key)        │                              │
    │   └── watcher_task           │                              │
    │                              │ cancel_requested=1 ◄─────────│ cancel_run(key)
    │                              │                              │ 发布后立即返回
    │ watcher: poll interval       │                              │
    │   is_cancelled(slot) ───────►│ 读 cancel_requested          │
    │   cancel_event.set()         │                              │
    │                              │                              │
    │ await cancel_event.wait()    │                              │
    │   → 返回                     │                              │
    │ raise_if_cancelled()         │                              │
    │ raise RunCancelledException  │                              │
    │ unregister_run(key)          │                              │
```

- owner 调用 `cancel_run()`：set 本地 `cancel_event`，publish 共享 flag，然后立即返回。
- 非 owner 调用 `cancel_run()`：`find(key)` + publish，立即返回。
- owner 首次调用 `get_cancel_event()` 时懒创建一个 per-run watcher task；watcher 按 `cancel_poll_interval`（默认 0.1s）读共享 cancel 字节，发现取消后 set 本地 `cancel_event`。`unregister_run()` 和 `close()` 会取消该 task。
- 没有跨进程的 cleanup Event；shared 模式下 `cancel_run` 不等待清理。
- 未调用 `get_cancel_event()` 的 Agent 仍然依赖 checkpoint 的 `raise_if_cancelled()` 读取共享 flag；watcher 只服务需要 `await cancel_event.wait()` 的场景。

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

- shared 模式下，队列满时保留旧消息，拒绝新消息，返回 `QUEUE_FULL`；run 继续执行。
- shared 模式下，单条超过 `max_steering_bytes` 返回 `MESSAGE_TOO_LARGE`；run 继续执行。
- 空 Content（`content is None` 或 `not content.parts`）返回 `UNSUPPORTED_CONTENT`。
- text、`inline_data`、`file_data`、`function_response` 等 part 都原样接收。
- `ACCEPTED` 表示消息已经入队；模型在下一次 LLM 请求前读取。

### 7.2 写入、消费与持久化

```text
UI / Worker B                Shared Memory                 Worker A（owner）
    │                            │                              │
    │ POST /steering ───────────►│ shared.enqueue(slot, msg)    │
    │ 202 Accepted               │                              │
    │                            │      checkpoint: drain       │
    │                            │◄─────────────────────────────┤
    │                            │ return [msg]                 │
    │                            │                              │ merge + mark
    │                            │                              │ yield Event(visible=False)
    │                            │                              │ → Runner append_event
    │                            │                              │ → build_request 从 session 读取
```

单 worker 模式使用同样的调用链，`Shared Memory` 换成 `_LocalRun.steering` deque。

- local：`deque[Content]`，直接存对象。
- shared：`enqueue` 把 `Content.model_dump_json(exclude_none=True)` 写进 record；`drain` 用 `Content.model_validate_json` 还原。
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
- Steering 在 LLM 流式输出中到达时，当前流继续运行；流结束后，如果有 tool call，先跑工具，再在下一轮循环开头消费 steering；如果当前流就是最终回复，run 结束时未消费的 steering 被丢弃。
- Steering 和 cancel 同时到达时，cancel 优先：先 `raise_if_cancelled()`，本轮不消费 steering。
- run 结束时未消费的 steering 在 `unregister_run` / 共享内存 `release` 时清掉。

## 8. A2A / AG-UI 集成

A2A 和 AG-UI 都通过 `Runner` 的公开方法使用控制面，不直接调用 `_CancelManager`，也不直接读写共享表。A2A v1 已按这个方向落地；AG-UI 的 cancel 已迁移到 `Runner`，steering 仍待迁移，下面分别说明。

### 8.1 A2A

A2A v1 的入口是 `TrpcA2aAgentService`。Service 负责 agent card 和每次请求创建 executor；executor 持有 `Runner`，cancel 和 execute 都落到 Runner 的公开方法上，不直接读写 cancel 共享表。

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
- 本地模式下 `cancel_wait_timeout` 控制等待清理的时间；shared 模式下 cancel 只发布 flag，不等待 owner。

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
- 状态码：`ACCEPTED` 202、`NO_ACTIVE_RUN` 404、`QUEUE_FULL` 409、`MESSAGE_TOO_LARGE` 413、`UNSUPPORTED_CONTENT` 415。

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
  │                        │        └── 写 cancel flag
  │                        │
  │                        └── Agent 在 checkpoint 完成清理
```

取消只通过 `Runner` 发信号；后台 task 等 Runner 消费到 `AgentCancelledEvent` 后自然结束，不用 `task.cancel()`。

`/steering` 由 `AgUiService` 注册全局 `POST /steering`，body 里的 `agent_uri` 用来分发；如果服务只有一个 agent，`agent_uri` 可以省略。


## 9. 一致性与边界

### 9.1 单 run 约束

一个 session 同时只有一个活跃 run。`register_run()` 发现同 key 的活跃 run 时，通过 `owner_pid` 判断 owner 是否还活着：活着则抛 `RuntimeError`，已退出则回收 entry。这样 cancel 和 steering 的目标唯一。

### 9.2 Generation 与 entry 回收

```text
run 1 allocate: generation = 7
run 1 release:  检查 generation == 7 → 清空 entry

run 2 allocate: generation = 8
run 1 迟到的 release: generation 7 != 8 → 跳过清理
```

`owner_pid` 已退出的 RUNNING entry 在下次 `allocate()` 时回收，回收在 allocate 流程完成。

master 异常退出时，`init_shared_mem()` 的 master 锁由内核释放；下一次启动拿到锁后 unlink 旧 segment，创建全新的 segment。所有旧 entry、cancel flag 和 steering 状态归零。

### 9.3 单 worker 与多 worker 的差异

| 场景 | 控制状态 | cancel 写入 | steering 写入 |
|---|---|---|---|
| 单 worker | `_LocalManager` + `_LocalRun` | `cancel_event` | `deque[Content]` |
| 多 worker | `_SharedManager` + `_SharedRun` + `_SharedMem` | owner `cancel_event` + 共享 cancel 字节；非 owner 只写共享字节 | 共享 record 队列 |

两种模式下，Agent 的 checkpoint 都只通过 `InvocationContext` 访问控制面。多 worker 下如果 Agent 调用 `get_cancel_event()`，owner 进程会额外启动一个 per-run watcher，把共享 cancel flag 在一个 `cancel_poll_interval` 内（默认 0.1s）同步到本地 `asyncio.Event`；不调用则仍然只靠 checkpoint。

### 9.4 容量

- entry 数量由 `SharedMemoryConfig.max_active_runs` 配置，默认 256；固定位置下按整机总并发 run 数规划。
- entry 表满时，`register_run()` 抛 `RuntimeError`，Runner 直接失败；应用可以调大 `max_active_runs` 后重启。
- steering 队列满时 `send_steering()` 返回 `QUEUE_FULL`，run 继续执行。
- 消息超过 `max_steering_bytes` 时 `send_steering()` 返回 `MESSAGE_TOO_LARGE`，run 继续执行。
- 共享内存区默认约 1.03 MiB，占 worker 进程内存很小。

### 9.5 交付语义

- cancel 是 best-effort signal；延迟取决于 Agent 到达下一个 checkpoint 的时间。
- shared 模式下 `cancel_run()` 发布后立即返回；owner 在下一个 checkpoint 读到共享 flag 后退出。
- `get_cancel_event()` 首次调用会为该 run 启动 watcher；共享 cancel flag 会在一个 `cancel_poll_interval` 内（默认 0.1s）同步到 owner 进程本地的 Event。未调用 `get_cancel_event()` 的 Agent 仍依赖 checkpoint。
- steering 是 best-effort queue；`ACCEPTED` 表示已入队，模型在下一次 LLM 请求前读取。
- `consume_steering()` 把多条 steering 按 FIFO 合并成一条 user Content；LlmAgent 把它包装成 `visible=False` Event 写入 session，客户端事件流不可见，但模型历史可见。它会继续影响同一 session 的后续 LLM 请求。
- run 结束时未消费的 steering 随 `unregister_run` / 共享内存 `release` 清掉。

## 10. 性能开销

共享内存路径只在 checkpoint 上读控制状态，不参与每个 token 的业务计算。下表是开发机上的一组微基准（Linux x86_64，CPython 3.11.6，单进程，默认配置），用来判断量级，不是 SLA：

| 路径 | 触发频率 | 单次耗时 |
|---|---|---|
| `is_cancelled`（共享内存） | 每个流式 chunk、工具调用前后 | ~1.35 µs |
| `_LocalManager.raise_if_cancelled` | 同上 | ~0.66 µs |
| `consume_steering`（空队列，public） | 每次 LLM 请求前 | ~6.0 µs |
| `send_steering` + `consume_steering`（owner） | 每条 steering | ~51 µs |
| `cancel_run`（owner，entry 命中） | 每次取消 | ~5.9 µs |
| `cancel_run`（非 owner，entry 命中） | 每次取消 | ~16 µs |
| `find`（256 entry 全表 miss） | 非 owner 冷路径 | ~260 µs |
| `register_run` + `unregister_run` | 每次 run 开始/结束 | ~0.29 ms |
| 共享内存占用（默认配置） | 固定 | ~1.03 MiB |

```text
热路径

  流式 chunk ──► raise_if_cancelled() ──► is_cancelled()      ~1.35 µs/次
  每次 LLM 请求 ──► consume_steering() ──► drain_steering()    ~6.0 µs/次（空队列）

冷路径

  每次 run 开始/结束 ──► register_run / unregister_run         ~0.29 ms/次
  非 owner cancel ──► find() 全表扫描 + publish_cancel         ~0.26 ms/次（miss）
  每条 steering ──► send_steering + consume_steering           ~51 µs/条
```

几个结论：

- `is_cancelled` 是无锁读：owner 直接读 mmap 里的 entry 元数据（取消位是其中一个字节），没有 syscall、没有跨进程锁、没有网络。1.35 µs 主要是 Python 的 `memoryview` 切片和 `struct.unpack_from` 开销。按一次响应 2000 个 chunk 计算，总共约 2.7 ms，相对 LLM 生成本身可以忽略。
- `consume_steering` 每次 LLM 请求前调用一次，空队列时也要拿一次 entry 锁；这条路径不在 chunk 热路径上。
- `send_steering` + `consume_steering` 的 ~51 µs 包含 `Content.model_dump_json(exclude_none=True)` 和 `model_validate_json`，每条 steering 只发生一次。
- `register_run` / `unregister_run` 的 ~0.29 ms 来自 entry 分配/释放时的全表扫描和 `fcntl` 锁；非 owner 的 `find` 最坏也是全表扫描（~260 µs）。它们分别是每次 run、每次取消触发一次，run 启动 QPS 在几十的量级时占比很低，QPS 上千时才需要关注。
- `get_cancel_event()` 的 per-run watcher 只在调用后存在，频率为 `1 / cancel_poll_interval`（默认 0.1s，即每个 watched run 10 次/秒）。单次 tick 的 `is_cancelled` 约 1.35 µs，但实际开销主要来自 asyncio task 唤醒：本机实测 256 个 watched run 在 0.1s 周期下约占 2% 单核，0.05s 周期约占 4%。调大 `cancel_poll_interval` 可以线性降低这部分开销。
- 跨 worker 链路只多一次共享 entry 写入；读取仍在 owner 进程本地完成，不引入 RPC。

如果需要进一步压缩：

- 把 `entry_view(index)` + `unpack_from(view, 0)` 改成 `unpack_from(base_view, 绝对偏移)`，省掉 memoryview 切片。早前版本上实测 cancel 检查（`is_cancelled`）可降到 ~0.5 µs，entry 全表扫描从 ~260 µs 降到 ~75 µs。
- 按实际并发调小 `max_active_runs`，扫描长度和内存占用一起下降。
- `max_steering_bytes` / `steering_queue_size` 决定 entry 大小；默认配置下单个 entry 约 4.2 KiB，整块 segment 约 1.03 MiB，按 `max_active_runs` 预分配。
