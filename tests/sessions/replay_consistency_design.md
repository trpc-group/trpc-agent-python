# Replay 一致性设计说明

框架以同一标准轨迹驱动 InMemory、SQLite、Redis；真实 SQL/Redis 由环境变量接入。无外部服务时 SQL 使用独立临时库，Redis 以内存客户端替代网络，但复用 RedisStorage 的命令、查询和 TTL 路径。快照仅归一化自动摘要 ID、文本空白、字典键序和时间；事件保序，记忆按条目排序。SessionSummary 暂无 version，故按成功写入计数并经公共 API 核对；归属、覆盖、失败恢复严格比较。差异按精确路径列入 allowed_diff，报告保留 session、事件/摘要位置、字段路径和两侧值。仓库 JSON 是静态基线，测试会重建并校验。
