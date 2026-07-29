# 回放测试样例目录

[English](README.md)

本目录保存 `tests/sessions/test_replay_consistency.py` 使用的标准 JSONL 回放轨迹。框架结构、归一化、比较和后端接入方式请参阅
[正式中文文档](../../../docs/mkdocs/zh/session_replay_consistency.md)。

## JSONL 结构

每个文件每行包含一个 JSON 对象。首行是 `case`，声明 case ID、分类、预期异常标记和规范化预期快照。
后续行是回放操作：

| 操作 | 样例用途 |
|------|----------|
| `create_session` | 创建 Session，并可设置初始 state |
| `append_event` | 追加完整 SDK Event |
| `store_memory` | 将重新读取的 Session 写入 Memory |
| `search_memory` | 保存一次具名 Memory 查询结果 |
| `summarize` | 生成并持久化确定性 Summary 文本 |
| `inject_failure` | 执行声明的写入失败或重复提交场景 |
| `checkpoint` | 标记逻辑校验边界 |

## 公开 case

| 文件 | 覆盖场景 |
|------|----------|
| `01_single_turn.jsonl` | 一个 user event 和一个 agent 文本 event |
| `02_multi_turn.jsonl` | 连续三轮 user/agent 对话 |
| `03_tool_call.jsonl` | function call、function response 和最终回答 |
| `04_state_update.jsonl` | 多次更新 Session、User、App 和临时 state |
| `05_memory.jsonl` | 跨 Session 的偏好与事实 Memory |
| `06_summary_create.jsonl` | 首次创建 Summary 及保留事件 |
| `07_summary_update.jsonl` | 带版本和覆盖关系的 Summary 更新 |
| `08_summary_truncation.jsonl` | Summary、保留历史与后续新增事件 |
| `09_write_failure.jsonl` | Session 与 Memory 持久化之间发生失败 |
| `10_duplicate_write.jsonl` | 重复提交相同 event ID |

## 编写规则

- JSONL 只保存标准输入轨迹和预期快照；人工变异保留在验收测试中。
- operation、query、checkpoint 和 summary 标识必须唯一。只有声明为重复提交的 case 才允许重复
  event ID。
- event 和 Summary 时间戳必须递增；Summary 更新必须增加版本并引用被覆盖的 Summary。
- 预期快照应包含业务相关的 state、Memory 结果、保留及历史 event ID 和 Summary 元数据。

从仓库根目录运行：

```bash
python -m pytest tests/sessions/test_replay_consistency.py -p no:cacheprovider
```
