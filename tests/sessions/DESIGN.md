# Session / Memory / Summary Replay Harness 设计说明

框架用同一组轨迹驱动 InMemory、SQLite 及可选 Redis 后端，分为用例、执行、快照和比较四层。快照覆盖事件、state、memory 与 summary。归一化只处理自动 ID、相对时间、Unicode 空白和无序容器，业务字段仍严格比较。Summary 文本允许规范化，session 归属、版本、时间顺序和覆盖关系必须一致。差异白名单按后端对、字段路径和原因精确匹配，并记录命中次数。SQLite 关闭重开验证持久化；设置 `TRPC_REPLAY_BACKENDS=in_memory` 可运行轻量模式，Redis 通过环境变量接入，未配置时跳过。
