# Replay 一致性设计说明

框架把五类操作定义为后端无关轨迹，同一执行器驱动 InMemory、SQLite 与 Redis，真实 SQL/Redis 可通过环境变量接入。未配置外部服务时，通用 SQL 工厂回退到 SQLite，Redis 服务使用进程内存储替身，测试不跳过。快照仅归一化自动摘要 ID、文本空白、字典键序和更新时间单调性；事件保持原序，记忆按完整条目排序。SessionSummary 暂无 version，故按成功写入计数并经公共 API 核对；摘要归属、覆盖及失败恢复严格比较。时间差异只按精确路径列入 `allowed_diff`，报告保留 session、事件/摘要定位、字段路径和两侧值。
