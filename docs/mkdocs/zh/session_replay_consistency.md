# Session / Memory / Summary 回放一致性测试

## 设计说明

框架使用 JSONL 标准轨迹驱动 InMemory、文件型 SQLite 及可选 SQL/Redis 后端，回放后读取 session、events、state、memory、summary 快照并比较。归一化只处理非业务差异：事件时间戳、memory 时间戳、summary 更新时间写入 `allowed_diff`；显式 replay event id 仍参与比较，memory 返回顺序按多重集排序。Summary 比较区分语义文本和存储元数据：摘要文本做空白、大小写归一化，`session_id`、版本链、active/historical 覆盖关系、事件计数和同进程 public `get_session_summary()` 读取结果精确比较。SQLite 会重建 service 验证 summary anchor 与 historical events 的持久化回放；manager cache 只在进程内比较。报告写入 `session_memory_summary_diff_report.json`，定位到 case、真实 session id、event index 或 summary id、字段路径和两端值。

## 运行方式

固定收集报告：

```bash
TRPC_AGENT_REPLAY_REPORT_PATH=./session_memory_summary_diff_report.json \
  .venv/bin/python -m pytest tests/sessions/test_replay_consistency.py
```

开启 SQL/Redis 集成模式：

```bash
TRPC_AGENT_REPLAY_BACKENDS=in_memory,sqlite,sql_env,redis_env \
TRPC_AGENT_REPLAY_SQL_URL=mysql+pymysql://... \
TRPC_AGENT_REPLAY_REDIS_URL=redis://... \
  .venv/bin/python -m pytest tests/sessions/test_replay_consistency.py
```

未设置集成环境变量时，SQL/Redis 后端会自动跳过；后端构建或回放失败会记录到 report 的 `errors` 字段。集成模式会 best-effort 清理本次 run 的数据，仍建议使用临时库或独立 Redis DB。
