# Replay Consistency Design Note

## 设计说明
本框架以统一 `ReplayCase` 协议驱动 `InMemory`、文件型 `SQLite` 与可选 `Redis` 后端，先生成 `session/state/memory/summary` 四类快照，再做归一化比较。归一化只移除非业务噪声，不掩盖事件顺序、state 最终值、memory 检索结果及 summary 归属与覆盖关系；其中普通时间戳做精度收敛，`summary_timestamp` 使用 case 级确定性时钟参与比较。summary 比较分为两层：一层比较摘要文本与压缩后的事件窗口，另一层比较 `summary_id/version/replaces/session_id` 等 lineage 元数据。为验证保存/读取语义，summary 元数据会随 summary event 一起持久化，`SQLite/Redis` 适配器在抓取快照前执行一次关服务后重开读回，并支持在 case 中显式插入中途重启步骤验证“重启后继续写”。协议支持用 `session_alias` 在同一 case 内驱动多 session / 多 user，并在最终快照中同时保留 `active session` 与 `sessions_by_alias` 视图，避免非活跃 session 损坏被漏检。memory 检索按 step 记录为独立 observation，不会在重启后被重算覆盖；同名 query 可跨 session 重复使用。负例分为 snapshot mutation 与 runtime fault 两类，二者都支持 alias 级注入，前者验证比较器精度，后者验证重复写入、中途失败、非活跃 session 污染和运行时 summary 破坏。轻量模式默认运行 `InMemory + SQLite`，集成模式通过环境变量启用 `Redis`。

## 官方 10 条验收 Case
| Case ID | 场景 | 验收点 |
| --- | --- | --- |
| `single_turn_text` | 单轮普通对话 | 基础事件一致性 |
| `multi_turn_dialogue` | 多轮对话 | 事件顺序与多轮回放 |
| `tool_call_and_response` | 工具调用对话 | `function_call/response` 一致性 |
| `state_and_memory_roundtrip` | 多次 state 更新覆盖 + memory 检索 | state 覆盖语义与 memory 一致性 |
| `summary_compaction_with_history` | summary 生成与历史事件压缩 | summary 与 historical events |
| `summary_version_rolls_forward` | summary 更新 | version / replaces 递增 |
| `summary_binding_mismatch_injection` | summary 归属错误 | `summary.session_id` 检出 |
| `summary_missing_injection` | summary 丢失 | `summary` 缺失检出 |
| `runtime_summary_overwrite_fault` | summary 覆盖错误 | `summary.replaces` 检出 |
| `partial_failure_event_loss_fault` | 写入中途失败 | 事件窗口异常检出 |

## 扩展 Case
- `state_corruption_injection`：补充 state 字段级污染检测。
- `summary_lineage_corruption_injection`：补充 snapshot 层 lineage 篡改。
- `duplicate_event_runtime_fault`：补充重复写入异常。
- `runtime_state_corruption_fault`：补充运行时 state 污染。
- `runtime_summary_loss_fault`：补充运行时 summary 丢失。
- `non_active_session_summary_loss_fault`：补充非活跃 session 的 summary 损坏检测。
- `cross_session_memory_aggregation`：补充同一 app/user 下跨 session 的 memory 聚合语义。
- `restart_mid_replay_after_summary`：补充 summary 持久化后中途重启再续写的恢复语义。
- `state_namespace_roundtrip`：补充 `app:/user:/temp:` 状态命名空间在跨 session 和重启后的可见性语义。
- `cross_user_memory_isolation`：补充同一 app 下不同 user 的长期记忆隔离语义。
- `duplicate_memory_query_name_across_sessions`：用定向测试覆盖跨 alias 的同名 memory query 不应互相覆盖。
- `memory_query_observation_survives_restart`：用定向测试覆盖重启后 memory query 观测不得被后续结果回填。
