# Session / Memory / Summary Replay Harness 设计说明

同一组轨迹驱动 InMemory、SQLite 和可选 Redis，快照覆盖事件、state、memory、summary。归一化只处理自动 ID、相对时间、Unicode 空白和无序容器；Summary 文本可规范化，但 session 归属、版本和覆盖关系必须一致。差异按后端对和字段路径精确匹配。SQLite 重开验证持久化；`TRPC_REPLAY_BACKENDS=in_memory` 为轻量模式。两份 JSON 均为测试生成产物，可删除后重跑再生，不是手写契约。
