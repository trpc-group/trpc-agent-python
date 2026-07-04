# Replay Consistency Harness

这个 harness 用同一组 deterministic replay cases 驱动多个后端。每个 case 先通过正式
`SessionServiceABC` 创建 session，再按固定 id、timestamp、invocation_id 写入事件；最后用
`MemoryServiceABC.store_session(session)` 写入 memory，并通过 `search_memory(key, query, limit)`
读取结果。默认矩阵真实运行 InMemory 和 SQLite；如果设置
`TRPC_AGENT_REPLAY_REDIS_URL`，Redis 会作为可选后端加入比较。

snapshot 会归一化非业务字段：事件 timestamp 不比较精确值，summary timestamp 只比较是否存在，
自动生成的 event id 统一为 normalized，dict 递归按 key 排序，memory timestamp 只保留
`has_timestamp`。memory 返回顺序不作为语义，因此按 `(query, author, text)` 排序。

严格比较的字段包括 event 顺序、author、role、text、tool args、tool response、state、memory
content、summary text、summary session_id、summary overwrite 后的最新值、summary event flag、
summary event 数量，以及 historical_events 数量和内容。summary 使用
`DeterministicSessionSummarizer`，不调用真实 LLM，但保留现有 `SessionSummarizer` 的压缩逻辑，
因此 summary event 和 historical_events 仍由产品代码生成。

SQLite 后端使用独立临时 SQLite 文件并显式初始化 SQL storage；初始化失败不会静默降级。mutation
tests 会对 clean snapshot 人为制造 drop、reorder、state、memory、summary 等不一致，验证
recursive diff 能输出定位到 session/event/memory/summary/path 的非允许差异。
