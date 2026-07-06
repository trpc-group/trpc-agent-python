# Issue #89 测试计划: Session/Memory 多后端回放一致性测试框架

## 概述

构建可复用的回放一致性测试框架，用同一组标准化输入驱动 InMemory、SQL、Redis 多后端，自动检测差异。

## 维度 1: 单元测试 — normalizer (15 tests)

- [x] Event 归一化: author 保留、text 从 parts 提取、state_delta 保留/缺省
- [x] Snapshot 归一化: session_id/state/events/memories/summaries 正确
- [x] 空 parts / 空 text 处理
- [x] Memories 按 content 排序确保确定性对比
- [x] Unicode 文本正确保留

## 维度 2: 单元测试 — comparator (15 tests)

- [x] 相同 dict → 0 diff; 不同 value → 正确 path
- [x] 缺 key / 多 key 检测
- [x] 相同 list → 0 diff; 长度不同 / value 不同
- [x] 嵌套 dict/list 递归比较
- [x] 原始类型 (str/int/None) 比较
- [x] Summary injection / session snapshot 多节检测

## 维度 3: 单元测试 — cases + JSONL (9 tests)

- [x] 10 cases 定义完整性
- [x] 必填字段校验
- [x] 至少有 tool_call / summary / track_events case
- [x] 10 个 JSONL fixture 文件存在
- [x] JSONL 加载正确
- [x] JSONL 格式合法 JSON
- [x] Round-trip (save → load → equal)

## 维度 4: 集成测试 — E2E (4 tests)

- [x] InMemory vs SQLite 全部 10 cases → 0 unallowed diff
- [x] Summary diff 检测
- [x] State/Memory/Track diff 多节检测
- [x] JSONL round-trip 与 Python 定义一致

## 维度 5: Redis 集成测试 (3 tests, env-var gated)

- [x] Redis vs InMemory 10 cases → gracefully skipped when unavailable
- [x] Redis vs SQLite 10 cases → gracefully skipped
- [x] 三后端全矩阵比较 → gracefully skipped

## 维度 6: 边界/极端测试

- [x] 空 session / 空 memory
- [x] Unicode 中文+emoji
- [x] State key 含特殊字符
- [x] 极长 summary 文本

## 结果

- **379 passed, 3 skipped (Redis), 1 pre-existing failure (unrelated)**
