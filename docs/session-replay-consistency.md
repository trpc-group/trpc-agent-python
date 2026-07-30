# Session / Memory / Summary 回放一致性设计

回放框架以稳定的 session、event 和 summary 标识驱动 InMemory 与 SQLite 后端，并把读取结果转换为统一快照。归一化仅移除时间戳等非业务字段，字典按键排序，summary 文本只折叠空白；后端特有差异必须通过精确字段路径加入 `allowed_diff`，不可整段忽略。摘要内容与存储元数据分开比较，`session_id`、版本及覆盖关系始终严格校验。重复事件由稳定输入 ID 保证幂等，失败操作模拟在落库前中断。默认轻量模式无需 Redis/MySQL；设置 `TRPC_REPLAY_SQL_DB_URL` 可运行外部 SQL 集成测试。报告按 case 输出 session、事件索引或 summary 字段路径及两端值，便于直接定位偏差。
