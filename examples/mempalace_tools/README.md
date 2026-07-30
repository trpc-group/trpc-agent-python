# MemPalace 工具集成示例

本示例演示如何把 `trpc_agent_sdk.tools.mempalace_tool` 中的工具直接注入 `LlmAgent`，让模型在对话中主动调用 MemPalace 完成长期记忆检索、写入、日记和知识图谱操作。

> 如果希望框架自动保存会话记忆，推荐使用 `MempalaceMemoryService`。本目录展示的是“工具式集成”：是否搜索、写入什么内容，都由模型通过工具调用完成。

## 关键特性

- **完整工具覆盖**：示例注入 `mempalace_tool.py` 中的 search、add drawer、diary、KG 全部工具。
- **本地持久化**：MemPalace 默认使用本地 palace 目录和 ChromaDB 保存向量与原文。
- **可指定存储路径**：通过 `MEMPALACE_PALACE_PATH` 指定 palace 目录，便于和 CLI 查询同一份数据。
- **跨 Session 记忆**：示例每轮使用不同 `session_id`，但 MemPalace 数据仍可被后续会话检索。
- **自动清理测试数据**：运行前后会删除示例 `wing/room` 下的数据，避免影响下一次测试。

## Agent 结构

```text
personal_assistant (LlmAgent)
├── model: OpenAIModel (config from .env)
└── tools:
    ├── MempalaceSearchTool (mempalace_search)
    ├── MempalaceAddDrawerTool (mempalace_add_drawer)
    ├── MempalaceDiaryWriteTool (mempalace_diary_write)
    ├── MempalaceDiaryReadTool (mempalace_diary_read)
    ├── MempalaceKGAddTool (mempalace_kg_add)
    ├── MempalaceKGQueryTool (mempalace_kg_query)
    ├── MempalaceKGInvalidateTool (mempalace_kg_invalidate)
    └── MempalaceKGTimelineTool (mempalace_kg_timeline)
        └── backend:
            └── MemPalace / ChromaDB local palace
```

关键文件：

- [agent/agent.py](./agent/agent.py)
- [agent/config.py](./agent/config.py)
- [agent/prompts.py](./agent/prompts.py)
- [run_agent.py](./run_agent.py)
- `trpc_agent_sdk/tools/mempalace_tool.py`

## 工具说明

| 工具类 | 工具名 | 作用 | 关键参数 | 示例触发场景 |
|---|---|---|---|---|
| `MempalaceSearchTool` | `mempalace_search` | 从 MemPalace 中按语义检索已保存的 drawer 内容。 | `query`、`limit`、`wing`、`room` | 用户问“你还记得我的名字吗？”时，先搜索 `user name`。 |
| `MempalaceAddDrawerTool` | `mempalace_add_drawer` | 将原文内容写入指定 `wing/room`，作为可检索的长期记忆。 | `wing`、`room`、`content`、`source_file` | 用户说“请记住我的名字是 Alice”时，写入用户画像房间。 |
| `MempalaceDiaryWriteTool` | `mempalace_diary_write` | 写入一条 agent 日记，适合记录运行观察、任务过程或阶段性总结。 | `entry`、`agent_name`、`topic`、`wing` | 用户要求“写一条今天测试工具的日记”。 |
| `MempalaceDiaryReadTool` | `mempalace_diary_read` | 读取指定 agent 最近的日记记录。 | `agent_name`、`last_n`、`wing` | 用户要求“读取最近几条日记”。 |
| `MempalaceKGAddTool` | `mempalace_kg_add` | 向知识图谱写入一条三元组事实，并可带有效期、置信度和来源。 | `subject`、`predicate`、`object`、`valid_from`、`valid_to`、`confidence` | 用户要求记录“Alice likes Italian food”。 |
| `MempalaceKGQueryTool` | `mempalace_kg_query` | 查询某个实体的知识图谱关系，支持按日期和方向过滤。 | `entity`、`as_of`、`direction` | 用户要求“查询 Alice 相关事实”。 |
| `MempalaceKGTimelineTool` | `mempalace_kg_timeline` | 按时间线读取知识图谱事实，可限定某个实体。 | `entity` | 用户要求“展示 Alice 的知识图谱时间线”。 |
| `MempalaceKGInvalidateTool` | `mempalace_kg_invalidate` | 将一条当前事实标记为失效，用于表达事实变化，而不是直接删除历史。 | `subject`、`predicate`、`object`、`ended` | 用户要求“把 Alice likes Italian food 标记为今天结束”。 |

## 安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate

pip3 install -e .
pip3 install mempalace
```

如果你的 MemPalace 安装需要额外向量依赖，请按 MemPalace 官方说明补装对应 embedding 或 Chroma 依赖。

## 环境变量

在 `examples/mempalace_tools/.env` 中配置，或通过 `export` 设置：

```bash
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=https://api.example.com/v1
TRPC_AGENT_MODEL_NAME=your-model-name

# Optional. If omitted, MemPalace uses its default palace path.
MEMPALACE_PALACE_PATH=/tmp/trpc-agent-mempalace-demo
MEMPALACE_KG_PATH=
MEMPALACE_WING=personal_assistant_alice
MEMPALACE_ROOM=user_profile
```

`wing` 和 `room` 是本示例给 MemPalace 的固定存储范围：

- `wing`：建议映射到应用或用户级作用域，例如 `app/user`、`personal_assistant_alice`。
- `room`：建议映射到记忆主题，例如 `user_profile`、`preferences`、`work_notes`。

## 运行

```bash
cd examples/mempalace_tools
python3 run_agent.py
```

示例分三个阶段执行。每条消息都会使用新的 `session_id`，用于验证不同 session 之间仍能通过 MemPalace 读到之前写入的数据。

第一阶段写入数据并立即用新 session 查询：

```text
Use mempalace_search to check whether you remember my name.
Use mempalace_add_drawer to remember that my name is Alice.
Use mempalace_add_drawer to remember that my favorite food is Italian food.
Use mempalace_search to recall my name and favorite food.
Use mempalace_diary_write to write a diary entry...
Use mempalace_diary_read to read the latest diary entries.
Use mempalace_kg_add to add this fact: Alice likes Italian food.
Use mempalace_kg_query to query facts about Alice.
Use mempalace_kg_timeline to show Alice's knowledge graph timeline.
```

第二阶段只读取，不再写入，用于验证数据已经落盘，并且新的 session 仍能读到上一阶段的数据：

```text
Use mempalace_search to recall my name and favorite food from the previous sessions.
Use mempalace_diary_read to read the latest diary entries from the previous sessions.
Use mempalace_kg_query to query facts about Alice from the previous sessions.
Use mempalace_kg_timeline to show Alice's knowledge graph timeline from the previous sessions.
```

第三阶段单独测试知识图谱失效能力。`mempalace_kg_invalidate` 会改变事实的当前状态，所以放在持久化读取验证之后执行，避免影响第二阶段判断：

```text
Use mempalace_kg_invalidate to mark the fact Alice likes Italian food as ended today.
Use mempalace_kg_query to query facts about Alice again after invalidation.
```

运行时可以从日志看到：

- 查询类问题会触发 `mempalace_search`。
- 用户要求记住稳定信息时会触发 `mempalace_add_drawer`。
- 日记类问题会触发 `mempalace_diary_write` / `mempalace_diary_read`。
- 知识图谱类问题会触发 `mempalace_kg_add` / `mempalace_kg_query` / `mempalace_kg_timeline` / `mempalace_kg_invalidate`。
- 每条消息都会换新的 `session_id`，但仍能从同一 MemPalace palace 中检索到之前写入的内容。
- 第二阶段只读不写，用于验证 MemPalace 数据已经落盘。
- 脚本开始和结束都会清理示例数据：drawer/diary 按 `MEMPALACE_WING`、`MEMPALACE_ROOM` 删除；KG 文件只会在设置了 `MEMPALACE_KG_PATH` 或 `MEMPALACE_PALACE_PATH` 时清理。

## 运行结果分析

以下分析基于 [out.txt](./out.txt) 中的实际输出。

| 阶段 | 验证目标 | 实际结果 | 是否符合预期 |
|---|---|---|---|
| 启动清理 | 运行前清理历史测试数据，避免影响本次结果。 | 首行出现 `Failed to clean MemPalace demo data: ~/.mempalace`，原因是首次运行时 palace 目录还不存在。 | 符合预期。首次运行没有历史数据可清理，不影响后续写入和查询。 |
| 第一阶段：首次搜索 | 测试 `mempalace_search` 在无记忆时的行为。 | 搜索 `name` 返回 `No palace found`，说明还没有已初始化/已写入的数据。 | 符合预期。此时尚未写入任何 drawer。 |
| 第一阶段：写入 drawer | 测试 `mempalace_add_drawer` 能写入用户画像。 | 分别写入 `User's name is Alice.` 和 `My favorite food is Italian food.`，工具返回 `success=True` 和对应 `drawer_id`。 | 符合预期。两个长期记忆都写入到 `personal_assistant_alice/user_profile`。 |
| 第一阶段：搜索 drawer | 测试不同 session 下能立即检索刚写入的 drawer。 | 搜索 `name favorite food` 返回 2 条结果，包含姓名和喜欢的食物。 | 符合预期。说明 drawer 写入后可以被语义检索命中。 |
| 第一阶段：写入日记 | 测试 `mempalace_diary_write`。 | 写入 `Alice tested the MemPalace tools example today.`，返回 `success=True` 和 `entry_id`。 | 符合预期。日记写入成功，并使用了配置的 `wing`。 |
| 第一阶段：读取日记 | 测试 `mempalace_diary_read`。 | 读取到 1 条日记，内容与刚写入的 entry 一致。 | 符合预期。说明 diary 写入和读取链路正常。 |
| 第一阶段：写入 KG 事实 | 测试 `mempalace_kg_add`。 | 写入 `Alice -> likes -> Italian food`，返回 `success=True` 和 `triple_id`。 | 符合预期。知识图谱三元组事实写入成功。 |
| 第一阶段：查询 KG 事实 | 测试 `mempalace_kg_query`。 | 查询 `Alice` 返回 1 条 outgoing fact：`Alice likes Italian food`，`current=True`。 | 符合预期。说明 KG 查询能按实体查到刚写入的事实。 |
| 第一阶段：KG 时间线 | 测试 `mempalace_kg_timeline`。 | 查询 `Alice` 的 timeline 返回同一条事实，`current=True`。 | 符合预期。说明时间线能展示实体相关事实。 |
| 第二阶段：跨 session 搜索 drawer | 验证只读阶段能读到上一阶段写入的数据。 | 使用新的 `session_id` 搜索，仍返回姓名和喜欢的食物 2 条结果。 | 符合预期。说明数据不依赖当前 session 内存，而是已经落到 MemPalace。 |
| 第二阶段：跨 session 读取日记 | 验证 diary 数据可跨 session 读取。 | 仍能读取到上一阶段写入的 1 条日记。 | 符合预期。说明 diary 数据持久化成功。 |
| 第二阶段：跨 session 查询 KG | 验证 KG 数据可跨 session 读取。 | 查询 `Alice` 仍返回 `Alice likes Italian food`，且 `current=True`。 | 符合预期。说明 KG 数据持久化成功，且在 invalidate 前仍是当前事实。 |
| 第三阶段：失效 KG 事实 | 测试 `mempalace_kg_invalidate` 的语义。 | 对 `Alice -> likes -> Italian food` 执行 invalidate，返回 `success=True`，`ended=2026-05-09`。 | 符合预期。invalidate 不删除事实，而是设置失效日期。 |
| 第三阶段：失效后查询 | 验证失效后的事实状态。 | 再次查询 `Alice`，事实仍存在，但 `valid_to=2026-05-09`，`current=False`。 | 符合预期。说明 KG 保留历史事实，同时标记其不再是当前事实。 |
| 结束清理 | 验证测试数据不会影响下次运行。 | 输出 `Cleaned MemPalace demo drawers: 3`，并删除 `knowledge_graph.sqlite3` 及 `-wal/-shm` 文件。 | 符合预期。drawer、diary 和 KG 文件都被清理。 |

整体结论：`out.txt` 的结果符合本示例预期。它验证了每条消息使用不同 `session_id` 时，MemPalace 仍能从本地持久化数据中检索 drawer、diary 和 KG；同时也验证了 KG invalidate 的行为是“保留历史记录但标记为非当前事实”。

## 使用 CLI 查询

如果指定了 `MEMPALACE_PALACE_PATH`，需要用同一个路径查询：

```bash
mempalace --palace /tmp/trpc-agent-mempalace-demo search "user name"
mempalace --palace /tmp/trpc-agent-mempalace-demo search "favorite food"
```

如果没有指定路径，CLI 需要使用 MemPalace 默认 palace 路径，或者先确认当前代码实际写入的路径。

## 和 MemoryService 的区别

| 方式 | 触发时机 | 适合场景 |
|---|---|---|
| `MempalaceMemoryService` | 框架自动在会话结束/记忆加载阶段处理 | 推荐用于稳定的长期记忆能力 |
| `mempalace_tool` | 模型主动调用工具搜索或写入 | 适合让模型显式控制“查什么、存什么” |

本示例属于第二种方式，因此 prompt 中明确要求模型在需要回忆时调用 `mempalace_search`，在需要保存稳定事实时调用 `mempalace_add_drawer`，在需要日记或知识图谱能力时调用对应的 MemPalace 工具。
