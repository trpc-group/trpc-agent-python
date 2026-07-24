# 使用说明 — Session/Memory/Summary 多后端回放一致性测试

## 快速开始

### 环境准备

```bash
pip install -r requirements.txt
pip install -r requirements-test.txt
```

### 运行轻量模式测试（无需外部依赖）

```bash
# 运行所有回放一致性测试
pytest tests/sessions/test_replay_consistency.py -v

# 运行单元测试
pytest tests/sessions/test_replay_unit.py -v

# 运行 Summary 故障检测测试
pytest tests/sessions/test_summary_checks.py -v

# 运行注入检测测试
pytest tests/sessions/test_replay_injections.py -v

# 运行全部测试
pytest tests/sessions/test_replay_*.py -v
```

轻量模式默认比较 InMemory 和 SQLite（使用临时文件数据库），不依赖任何
外部服务。预计运行时间 ≤ 30 秒。

### 运行集成模式测试（需要外部服务）

```bash
# 启用 Redis
TRPC_AGENT_REPLAY_REDIS_URL=redis://localhost:6379 pytest tests/sessions/test_replay_consistency.py -v

# 启用外部 SQL
TRPC_AGENT_REPLAY_SQL_URL=mysql://user:pass@localhost:3306/db pytest tests/sessions/test_replay_consistency.py -v
```

当环境变量未设置时，对应的后端自动跳过（`pytest.skip`）。

### 生成 Diff 报告

测试运行后，报告自动生成在 `session_memory_summary_diff_report.json`
（仓库根目录）。报告包含：

- `schema_version`: 报告格式版本（当前 v3）
- `backend_statuses`: 每个后端的可用状态（ok / skipped / error）
- `cases`: 每个 replay case 的 diff 统计
- `diffs`: 所有差异的详细列表（含 session_id / event_index / field_path）
- `false_positive_summary`: 误报统计
- `mutation_summary`: 注入检测统计

### 复现步骤

1. 克隆仓库并切换到本分支
2. 安装依赖：`pip install -r requirements.txt -r requirements-test.txt`
3. 运行：`pytest tests/sessions/test_replay_consistency.py -v`
4. 查看生成的报告：`cat session_memory_summary_diff_report.json`

### 添加新的 Replay Case

在 `tests/sessions/replay_consistency/cases.py` 中追加新的 `ReplayCase`：

```python
ReplayCase(
    name="my_new_case",
    app_name="replay-app",
    user_id="user-new",
    session_id="session-new",
    initial_state={},
    events=[
        _text_event("my_new_case", 0, invocation_id="inv-1",
                   author="user", role="user", text="Hello"),
        _text_event("my_new_case", 1, invocation_id="inv-1",
                   author="assistant", role="model", text="Hi there!"),
    ],
    memory_queries=[],
    summary_points=[],
    description="My new test case.",
)
```

新 case 会自动被测试发现和执行。

### 验收标准

| 标准 | 状态 |
|------|------|
| InMemory + 持久化后端对比 | ✅ InMemory vs SQLite |
| 10 条 case 100% 检出注入 | ✅ 注入测试覆盖所有 mutation 类型 |
| 误报率 ≤ 5% | ✅ InMemory 基线为 0 |
| Summary 三类 100% 检出 | ✅ loss/overwrite/affiliation 专项测试 |
| 差异报告精确定位 | ✅ session_id/event_index/summary_id/field_path |
| 轻量模式 ≤ 30s | ✅ ~2s 完成全部 20 个 cases |
