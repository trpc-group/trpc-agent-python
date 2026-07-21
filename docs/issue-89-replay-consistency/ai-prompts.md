# Issue #89 开发过程记录

## 第 1 轮：架构设计与数据模型

需求：构建 Session / Memory / Summary 多后端回放一致性测试框架，
支持 InMemory / SQLite / Redis 三个后端。核心管线为
load → replay → normalize → compare → report。

实现步骤：
1. 定义 Pydantic 数据模型：EventSpec（确定性事件模板）、
   ReplayCase（完整测试场景）、ReplaySnapshot（后端快照）、
   DiffEntry（结构化差异）
2. 设计后端工厂模式，支持 InMemory / SQLite（默认）+
   环境变量门控的 Redis / 外部 SQL
3. 实现确定性 SessionSummarizer，覆写压缩方法避免 LLM 不确定性

关键技术决策：
- 用 Pydantic BaseModel 而非 dataclass，与项目代码风格一致
- 占位符归一化（保留字段存在性）而非 pop 删除
- JSONPath 精确匹配 allowed_diff + 治理上限

## 第 2 轮：核心比较引擎

实现步骤：
1. 实现递归比较器 `recursive_diff`：dict 按 sorted keys 对齐、
   list 按下标对齐、叶子值严格相等
2. 实现 normalizer：timestamp/id/invocation_id → `<normalized>`、
   剥离 `temp:` 状态、内存结果确定性排序
3. 实现 allowed_diff 规则引擎：JSONPath 精确匹配 + `[*]` 通配 +
   governance 上限

## 第 3 轮：20 个 Replay Cases

覆盖维度：
- Session：单轮对话、多轮追加、工具调用往返
- State：scoped overwrite、app/user 作用域、temp 排除
- Memory：偏好搜索、跨用户隔离
- Summary：生成、更新覆盖、事件截断
- Error：重复事件、错误恢复
- Enhanced：中文对话、Emoji/特殊字符、深层嵌套、大批量

## 第 4 轮：测试执行与调优

1. 运行 InMemory 基线测试：20 cases 全部 0 diff ✓
2. 运行 InMemory vs SQLite 跨后端测试：误报率 < 5% ✓
3. 运行 Summary 三类故障检测：loss/overwrite/affiliation 100% 检出 ✓
4. 运行注入测试：快照层 10 类 mutation 全部检出 ✓
5. 运行性能测试：轻量模式 ~2s 完成，远低于 30s SLO ✓

发现并处理的问题：
- SQLite 序列化将 None → [] 导致事件结构差异，增强了 normalizer 的空容器归一化
- Part.from_function_call API 签名差异（项目当前版本不接受 id 参数）
- MemoryEntry 的 text 需要从 content.parts 提取
