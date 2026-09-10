# SQL SessionService + Session Compact

本示例只演示如何在已有 `SqlSessionService` 上增加：

- Tool Result Budget
- History Snip
- Microcompact
- AutoCompact
- AutoCompact 触发时生成的 Session Memory

压缩能力来自 `trpc_agent_sdk.sessions.compact`，长期记忆仍属于独立的
`trpc_agent_sdk.memory.advanced_memory`。SQL 表结构不变，但活跃/历史 Event
会按原 Session 语义重新分区。

## 组装关系

```text
AdvancedCompactConfig
        ↓ AdvancedSessionCompactManager
SqlSessionService
├── AdvancedSessionCompactManager
├── events: summary + recent Events
├── sessions.historical_events: 被压缩的原始 Events
└── sessions.state["_trpc_agent:summary"]

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
    session_compact_manager=AdvancedSessionCompactManager(config=compact_config),
)

runner = Runner(
    app_name=app_name,
    agent=agent,
    session_service=session_service,
)
```

`SqlSessionService` 会接收 `session_compact_manager`。Compact 只使用 SessionService 的
`events`、`historical_events` 和 `state`，不创建额外的 SQL 表。

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

- `SqlSessionService`：Session、活跃 Events、historical Events 和 state。
- Compact 不创建独立的 SQL transcript、Tool Result 或 session-memory 表。
