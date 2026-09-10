# SQL SessionService + Session Compact

本示例只演示如何在已有 `SqlSessionService` 上增加：

- Tool Result Budget
- History Snip
- Microcompact
- AutoCompact
- AutoCompact 触发时生成的 Session Memory

压缩能力来自 `trpc_agent_sdk.sessions.compact`，长期记忆仍属于独立的
`trpc_agent_sdk.advanced_memory`。SQL 表结构不变，但活跃/历史 Event
会按原 Session 语义重新分区。

## 组装关系

```text
AdvancedCompactConfig
        ↓ Runner 自动创建
SqlSessionService
├── AdvancedSessionCompactManager
├── events: summary + recent Events
├── sessions.historical_events: 被压缩的原始 Events
└── sessions.state["_trpc_agent:summary"]

AdvancedMemoryRuntime
├── advanced_memory_transcripts
├── advanced_memory_transcript_seen
└── advanced_memory_tool_results
```

核心调用：

```python
session_config = SessionServiceConfig(
    store_historical_events=True,
)
compact_config = AdvancedCompactConfig(
    model_context_window_tokens=4096,
    token_autocompact_ratio=0.30,
)
session_service = SqlSessionService(
    db_url=sql_url,
    is_async=False,
    session_config=session_config,
    session_compact_config=compact_config,
)

runner = Runner(
    app_name=app_name,
    agent=agent,
    session_service=session_service,
)
```

`Runner` 会读取 `session_compact_config`，自动从 `SqlSessionService` 获取 URL 和异步
模式，创建 `AdvancedSessionCompactManager` 并通过基类接口注入。
用户不需要手动调用 `setup_advanced_session_compact`，也不需要直接创建 Manager。

## 兼容已有 Session

旧 `sessions.state` 不需要预先包含 `_trpc_agent:summary`。Key 不存在时继续使用
原 Events；达到 AutoCompact 阈值后才生成并写入第一份结构化 Summary。

Session Memory 更新通过 `patch_session_state()` 完成。AutoCompact 成功后，
同一份内容会作为 summary Event 写入活跃 `events` 表；被替换的 Event 从活跃表
移入 `sessions.historical_events`。下一轮直接读取 `summary + recent events`。

## 配置与运行

默认使用 MySQL：

```dotenv
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=your-model-base-url
TRPC_AGENT_MODEL_NAME=your-model-name
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DB=trpc_agent_session
SESSION_ID=simple-demo
```

示例使用同步 `pymysql` 驱动。如果需要异步连接，可以将连接地址改为
`mysql+aiomysql://...`，安装 `aiomysql`，并将 `is_async` 改为 `True`。

运行：

```bash
cd examples/session_service_with_advanced_memory_sql
source ../../.venv/bin/activate
python run_agent.py
```

脚本默认使用 `simple-demo`，可通过 `SESSION_ID` 修改。重复运行可以验证
活跃窗口、历史原始 Events、Session Memory 和完整 Tool Result 能够恢复。

运行结束会输出 `Active Events`、`Historical Events`、活跃窗口是否以 summary
开头，以及 Session Memory state 是否存在，方便直接确认压缩是否触发。

## 存储职责

- `SqlSessionService`：Session、活跃 Events、historical Events、state 和 Session Memory。
- Advanced Memory SQL stores：压缩重放记录和完整 Tool Result。
- 不再创建 `advanced_memory_session_memory` 表。
- Advanced Memory transcript 不保存 `kind=event` 或
  `session-memory-checkpoint`。
