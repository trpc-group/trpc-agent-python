# Session / Memory / Summary Replay Consistency

框架以 JSONL 记录标准操作，用固定 id 和顺序驱动后端。默认比较内存与 SQLite；环境变量可切轻量模式或加入 Redis。时间统一为占位符，memory 结果排序；事件、state 和工具数据仍严格比较。`allowed_diff` 必须声明原因。

Summary 同时比较文本及存储关系。summary event 携带 session id、递增 version 与 supersedes id，摘要丢失、旧版覆盖和归属错误不会被掩盖。active 与 historical events 分开校验。报告给出 session、event index 或 summary id、路径及两端值；测试还会对 10 条轨迹逐一注入差异。
