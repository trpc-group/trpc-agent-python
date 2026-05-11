# MemPalace Memory Service 使用指南

本示例演示如何在 `trpc-agent` 中使用 `MempalaceMemoryService` 实现跨 session 的长期记忆存储与检索。

MemPalace 是一个本地优先的记忆系统，底层使用 ChromaDB 存储 drawer 文本、metadata 和向量索引。当前示例会把会话中的可见文本事件写入 MemPalace，并通过 `load_memory` 工具在后续 session 中检索出来。

## 关键特性

- 使用 `MempalaceMemoryService` 接入本地 MemPalace。
- 通过 `wing` 和 `room` 组织记忆。
- 默认按 `save_key = {app}/{user}` 维度实现跨 session 检索。
- 支持配置 MemPalace 存储路径。
- 支持 TTL 后台定时清理过期 drawer。
- 示例输出中会截断过长工具结果，避免 memory JSON 刷屏。

## 安装依赖

使用前需要安装本项目依赖和 MemPalace 可选依赖。

在项目根目录执行：

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[mempalace]"
```

如果你使用虚拟环境，请确保运行示例和执行 `mempalace search` 时使用的是同一个环境。

## 运行示例

在项目根目录执行：

```bash
cd examples/memory_service_with_mempalace
python3 run_agent.py
```

示例会连续运行三轮，每轮包含多个不同 session：

- 先询问是否记得姓名和喜欢的颜色。
- 再告诉 agent：姓名是 Alice，喜欢的颜色是 blue。
- 后续 session 再次询问时，agent 会通过 `load_memory` 检索长期记忆。

## 关键代码

`run_agent.py` 中创建 Memory Service：

```python
memory_service_config = MemoryServiceConfig(
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=20,
        cleanup_interval_seconds=20,
    ),
    enabled=True,
)

memory_service = MempalaceMemoryService(
    memory_service_config=memory_service_config,
    wing="trpc-agent",
    room="conversations",
    store_only_model_visible=True,
)
```

这里的含义是：

- `wing="trpc-agent"`：把示例记忆固定写入 `trpc-agent` 这个 wing。
- `room="conversations"`：把普通对话记忆写入 `conversations` room。
- `store_only_model_visible=True`：只存模型可见的事件。
- `ttl_seconds=20`：超过 20 秒的记忆会被后台 cleanup 删除。
- `cleanup_interval_seconds=20`：每 20 秒执行一次清理。

## MemPalace 层级映射

MemPalace 的主要存储层级是：

```text
Palace
  └── Wing
        └── Room
              └── Drawer
```

在当前示例中：

```text
Palace = MempalaceConfig.palace_path
Wing   = trpc-agent
Room   = conversations
Drawer = 单条 Event 文本
```

如果没有显式传入 `wing`，`MempalaceMemoryService` 会默认用 `session.save_key` 解析 wing。框架里的 `save_key` 通常是：

```text
{app}/{user}
```

这样可以做到同一个 app/user 下跨 session 查询记忆。

## 指定存储路径

MemPalace 的默认存储路径来自 `MempalaceConfig().palace_path`，通常是：

```text
~/.mempalace/palace
```

如果要指定路径，可以使用 MemPalace 自己支持的配置方式，例如环境变量：

```bash
export MEMPALACE_PALACE_PATH=/path/to/palace
```

也可以使用 MemPalace 配置文件 `~/.mempalace/config.json` 指定：

```json
{
  "palace_path": "/path/to/palace",
  "collection_name": "mempalace_drawers"
}
```

注意：`/path/to/palace` 指的是 MemPalace 数据目录，也就是包含 `chroma.sqlite3` 的目录，不是某个单独文件。

## 使用 CLI 查询指定路径

如果代码里指定或配置了自定义 palace 路径，使用 `mempalace search` 查询时也必须指定同一个路径：

```bash
mempalace --palace /path/to/palace search "user name"
```

如果还要限制到当前示例的 wing：

```bash
mempalace --palace /path/to/palace search "user name" \
  --wing trpc-agent
```

如果还要限制到 room：

```bash
mempalace --palace /path/to/palace search "user name" \
  --wing trpc-agent \
  --room conversations
```

如果没有指定自定义路径，也可以直接查询默认 palace：

```bash
mempalace search "user name" --wing trpc-agent --room conversations
```

## 删除记忆

MemPalace CLI 当前没有直接提供按 `wing` 或 `wing + room` 删除的命令。框架里已经在 `MempalaceMemoryService` 提供了删除方法：

```python
await memory_service.delete_memory(wing="trpc-agent")
await memory_service.delete_memory(wing="trpc-agent", room="conversations")
```

如果需要用命令行删除，可以写一个小脚本直接调用 MemPalace collection 的 `delete(where=...)`。

## 运行结果分析

### 1. 首次查询没有记忆

第一次运行开始时：

```text
load_memory({'query': 'user name'})
Tool Result: {"memories": []}
```

说明开始时 MemPalace 中没有可召回的姓名记忆，agent 正确回答“不知道用户姓名”。

### 2. 写入姓名后可以跨 session 召回

当用户输入：

```text
Hello! My name is Alice. Please remember my name.
```

后续再问：

```text
Now, do you still remember my name?
```

工具结果中出现：

```text
[2026-05-07T20:19:27.141759] user:
Hello! My name is Alice. Please remember my name.
```

agent 随后回答能记得姓名是 Alice。说明 MemPalace 已经把前一个 session 的用户消息写入，并在后续 session 中成功检索。

### 3. favorite color 也可以被召回

当用户输入：

```text
Hello! My favorite color is blue. Please remember my favorite color.
```

后续查询 `favorite color` 时，工具结果能召回对应文本，agent 回答喜欢的颜色是 blue。说明语义检索和跨 session 记忆对这个场景有效。

### 4. TTL 清理生效

输出中可以看到多次 cleanup 日志：

```text
MemPalace cleanup: deleted 195 expired memories
MemPalace cleanup: deleted 13 expired memories
MemPalace cleanup: deleted 5 expired memories
```

这说明示例中配置的 TTL 清理任务已经运行，并删除了超过 `ttl_seconds=20` 的过期记忆。

第三轮开始时：

```text
load_memory({'query': 'user name'})
Tool Result: {"memories": []}
```

这是符合预期的，因为 `main()` 在第二轮后等待了 30 秒，而 TTL 只有 20 秒，旧记忆已经被后台清理。

### 5. 结果中的现象说明

输出里有时 agent 会说“我没有主动保存记忆的工具”。这是模型对工具能力的表述不够准确。实际框架是在每轮结束后由 `Runner` 调用 `memory_service.store_session()` 自动写入记忆，并不是通过一个显式的 `save_memory` 工具保存。

因此判断是否符合要求时，应看后续 `load_memory` 是否能召回历史内容，而不是看模型是否声称自己调用了保存工具。

## 结论

`out.txt` 体现了本示例的核心目标：

- 初始无记忆时，查询返回空。
- 用户提供姓名或偏好后，后续 session 可以通过 MemPalace 召回。
- 记忆按 `wing=trpc-agent`、`room=conversations` 写入。
- TTL 到期后，旧记忆会被定时清理。
- CLI 查询自定义路径时，需要使用 `mempalace --palace /path/to/palace search "query"`。

所以该运行结果符合 MemPalace memory service 示例的预期。
