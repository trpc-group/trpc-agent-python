# 数据库事务/连接生命周期 DB Lifecycle (`db_lifecycle`)

连接未关闭、事务未提交/回滚、循环内建连、autocommit 混用。
Unclosed connections, unfinished transactions, per-loop connections and mixed
autocommit modes.

| Rule | Trigger 触发条件 | Severity | Confidence |
|---|---|---|---|
| DBL001 | `conn = xxx.connect(...)` 无 `with`/`close()` | high | 0.75 |
| DBL002 | `cur = conn.cursor()` 无 `with`/`close()` | medium | 0.60 |
| DBL003 | `begin(` / `execute("BEGIN")` 而变更内无 commit/rollback | high | 0.75 |
| DBL004 | 循环体内创建数据库连接 | high | 0.70 |
| DBL005 | `autocommit=True` 与显式事务调用混用 | medium | 0.60 |

`close()`/`commit()` 可能位于 diff 可见范围之外，因此这些是启发式规则；
提供 `--files-dir`（repo-path / file-list 输入）时用全文件内容判断，精度更高。
The matching `close()`/`commit()` may live outside the visible hunk, so these
are heuristics; with `--files-dir` (repo-path / file-list inputs) whole-file
content is used and accuracy rises.

## 修复建议 Remediation

- `with engine.connect() as conn:` / `with conn.begin():` — 出错自动回滚。
- Never create connections inside loops — hoist them out or use a pool.
- Pick ONE mode: autocommit for single statements, or explicit transactions.
