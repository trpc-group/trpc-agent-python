# Session / Memory / Summary Replay Harness 设计说明

建立后端解耦回放基准，不改生产接口。分轨迹、执行、快照、比较四层：
fixture 定义操作与预期；执行器驱动 InMemory、SQLite 和可选 Redis；快照汇集事件、state、
memory、summary；比较器生成路径报告。规范化只处理自动 ID、相对时间、Unicode 空白和
无序容器，业务字段严格比较。Summary 文本按规范化结果比较，session 归属、版本单调性和
覆盖关系不可放宽。差异采用 backend pair、字段路径、原因组成的精确白名单，且须
命中。SQLite 关闭重开验证持久化；设置 TRPC_REPLAY_BACKENDS=in_memory 可仅跑内存，新后端
实现适配器即可。
