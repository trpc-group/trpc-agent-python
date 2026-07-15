# Replay 一致性设计说明

框架将对话、工具、状态、记忆和摘要定义为后端无关轨迹，由同一执行器驱动 InMemory 与 SQLite，Redis 通过环境变量接入。比较前仅归一化摘要事件 ID、时间、文本空白和字典键序；事件按原序严格比较，记忆按完整内容排序。框架按摘要写入次数递增版本，事件 ID、state、summary 归属和覆盖关系均严格比较。存储提交时间与摘要生成时间列入精确的 `allowed_diff` 并保留原因。报告记录 session、event index 或 summary id、字段路径及两侧值。SQLite 提供免安装验证，纯 InMemory 用于轻量运行，Redis 未配置时显式跳过。
