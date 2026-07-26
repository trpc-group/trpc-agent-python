# Session / Memory / Summary Replay Harness 设计说明

目标是在测试层建立后端解耦的回放基准，不改生产接口。框架分为轨迹、执行、快照和比较
四层：fixture 定义操作及预期；执行器驱动 InMemory、SQLite 和可选 Redis；快照汇集事件、
state、memory、summary；比较器生成路径级差异报告。规范化仅处理自动
ID、相对时间、Unicode 空白和无序容器，业务 ID 与字段保持严格比较。Summary 文本按规范化
结果比较，session 归属、版本单调性和覆盖关系作为不可放宽约束。后端固有差异采用
backend pair、字段路径、原因组成的精确白名单，并要求实际命中。SQLite 关闭重开验证
持久化；新后端只需实现适配器。
