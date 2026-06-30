# Session / Memory / Summary 回放一致性测试

回放一致性测试位于 `tests/sessions/test_replay_consistency.py`，使用至少 10 条 Python fixture 驱动 InMemory 与 SQLite 后端，覆盖单轮、多轮、工具调用、scoped state、memory、summary 生成/覆盖/截断和恢复异常。默认无需外部依赖；设置 `TRPC_AGENT_REPLAY_SQL_URL` 或 `TRPC_AGENT_REPLAY_REDIS_URL` 后加入真实 SQL/Redis，未设置则跳过集成用例。

快照比较 `session/events/state/memory/summary`，会归一化事件 id、时间戳和序列化顺序；memory 结果按内容排序，summary 用确定性 fake model，比对文本、session 归属、压缩计数、覆盖关系和 historical events。异常恢复通过 public API 在 SQLite 侧真实注入重复写、部分丢失、state/memory 污染；summary 三类错误用 snapshot 注入验证 diff harness。报告默认持久化到仓库根目录 `session_memory_summary_diff_report.json`，也可用 `TRPC_AGENT_REPLAY_REPORT_PATH` 覆盖；`allowed_diff` 仅接受显式规则并写明原因，未命中的差异会导致测试失败。
