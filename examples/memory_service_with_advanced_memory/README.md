# Standard SessionService + Advanced Compact + Advanced Memory

本示例使用统一后的组合方式：

```text
InMemorySessionService
└── AdvancedSessionCompactManager
    ├── Session Memory
    ├── Tool Result Budget
    ├── History Snip
    ├── Microcompact
    └── AutoCompact

AdvancedMemoryService
├── save_memory
├── read_memory
├── list_memory_index
└── long-term memory injection
```

不再使用独立的 Advanced SessionService。Session 的创建、Event 保存和状态管理始终
由标准 `InMemorySessionService`、`RedisSessionService` 或 `SqlSessionService`
负责；Advanced Compact 通过 `BaseSessionCompactManager` 生命周期接入。

## 核心组装

```python
config = AdvancedMemoryServiceConfig(
    root_dir=Path(__file__).resolve().parent,
)

session_service = InMemorySessionService(
    session_config=SessionServiceConfig(
        store_historical_events=True,
    ),
    session_compact_manager=AdvancedSessionCompactManager(
        config=AdvancedCompactConfig(),
    ),
)

memory_service = AdvancedMemoryService(config=config)
runner = Runner(
    app_name="advanced_memory_demo",
    agent=agent,
    session_service=session_service,
    memory_service=memory_service,
)
```

Session Compact 与 Advanced Memory 使用独立配置和 Runtime。Compact 只使用
SessionService 的 events、historical_events 和 state。

## 运行

在 `.env` 中配置模型，然后执行：

```bash
python run_agent.py
```

示例会在两个 Session 中使用同一用户，验证用户级长期记忆可以跨 Session 使用。
