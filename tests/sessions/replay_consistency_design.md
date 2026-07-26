# Replay 一致性设计说明

框架用同一轨迹驱动 InMemory 和 SQLite 基线，真实 SQL/Redis 由环境变量接入。Redis 无服务时以 fakeredis 客户端注入正式 RedisStorage，不在 harness 内重写语义。快照只归一化自动 ID、动态时间、文本空白和字典顺序；事件、state、memory 及 summary 的 session 归属、覆盖关系、回放版本严格比较。SDK 没有 summary version，harness 按成功写入次数计数。allowed_diff 只匹配精确路径。测试重建并读取仓库 JSON 基线，CLI 仅显式 `--output` 写文件。
