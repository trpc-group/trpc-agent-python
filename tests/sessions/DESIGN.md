# 回放一致性框架设计说明

框架以 Python fixture 描述统一轨迹，默认驱动 InMemory 与 SQLite Session/Memory；`REPLAY_MODE=inmemory` 可只跑内存，integration 可用环境变量接入外部 SQL，Redis 未配置时跳过。

比较前递归排序字典，过滤空 Part，并归一化自动 UUID、时间戳和空集合；session id、事件顺序、state、工具参数及 memory 内容仍精确比较。`allowed_diff` 必须限定后端对、完整字段路径并写明原因，禁止宽泛忽略。

Summary 使用确定性 Mock 生成；harness 将文本、版本、更新时间、压缩计数和 session 归属写入 summary anchor metadata，再从各后端回读。文本仅做 Unicode 与空白归一化，版本、归属和覆盖关系严格比较。报告记录 case、后端、session id、event index/summary id、字段路径及双方值。
