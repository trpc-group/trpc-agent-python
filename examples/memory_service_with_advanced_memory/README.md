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
config = AdvancedCompactConfig(
    root_dir=Path(__file__).resolve().parent,
)

session_service = InMemorySessionService(
    session_config=SessionServiceConfig(
        store_historical_events=True,
    ),
)
compact_manager = setup_advanced_session_compact(
    agent,
    session_service,
    config,
)

memory_service = AdvancedMemoryService(runtime=compact_manager.runtime)
runner = Runner(
    app_name="advanced_memory_demo",
    agent=agent,
    session_service=session_service,
    memory_service=memory_service,
)
```

Session Compact 与 Advanced Memory 可以共享一个 Runtime；Runtime 的 `close()`
支持幂等调用，因此两个 Service 的正常关闭流程不会造成重复释放错误。

也可以直接构造实现了 `BaseSessionCompactManager` 的自定义 Manager，并通过
`session_compact_manager=` 注入标准 SessionService。

## 运行

在 `.env` 中配置模型，然后执行：

```bash
python run_agent.py
```

示例会在两个 Session 中使用同一用户，验证用户级长期记忆可以跨 Session 使用。
