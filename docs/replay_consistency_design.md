# Session / Memory / Summary 回放一致性设计

## 归一化策略

各后端读回后生成统一快照，仅保留 session、state、当前与历史事件、memory 检索结果和 summary 版本。事件快照保留作者、文本、工具请求与响应及 state_delta；省略时间戳和自动 ID，字典按键排序、memory 按内容排序，事件列表顺序不变。

## Summary 比较策略

摘要文本转为小写，并统一空白和中英文标点；生成的 UUID 替换为固定占位符。summary_session_id、summary_version、summary_updated_at、被覆盖事件 ID、截断后保留事件及 historical_events 均按字段和下标严格比较。

## 允许差异

`_diff` 递归比较字典键和列表下标。样例只能通过 `allowed_diff` 声明完整路径，并用 `allowed_diff_reason` 解释原因；报告仍记录两侧值、session、event index、允许标记和原因，任何未声明差异都会使结果为 different。

## 后端接入方式

默认以 InMemory 为基准，对比内存 SQLite 和共享存储的 Mock Redis。`REPLAY_SQL_URL` 用于替换 SQL 地址，`REPLAY_REDIS_URL` 用于追加真实 Redis；外部持久化模式以 UUID 隔离每次回放，`REPLAY_LIGHTWEIGHT=1` 时仅运行 InMemory。
