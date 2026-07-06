# 资源泄漏 Resource Leaks (`resource_leak`)

未释放的文件句柄、socket、临时文件与线程。
Unreleased file handles, sockets, temp files and threads.

| Rule | Trigger 触发条件 | Severity | Confidence |
|---|---|---|---|
| RES001 | `x = open(...)` 无 `with` 且可见范围内无 `x.close()` | medium | 0.65 |
| RES002 | `s = socket.socket(...)` 无 `with` / `close()` | medium | 0.65 |
| RES003 | `NamedTemporaryFile(delete=False)` | low | 0.85 |
| RES004 | `threading.Thread(...)` 无 `join()` / `daemon` | low | 0.50 |
| RES005 | `open(...)` 内联使用，句柄无法关闭 | medium | 0.60 |

RES001/RES002/RES004/RES005 是 diff 范围内的启发式（`close()` 可能在 diff
之外），置信度 ≤ 0.65，默认进入 needs_human_review 桶而不混入高置信 findings。
These are diff-scope heuristics (the `close()` may live outside the hunk), so
their confidence is capped at 0.65 and they land in the needs_human_review
bucket by default.

## 修复建议 Remediation

- `with open(path) as f:` / `with socket.socket() as s:` — release on every path.
- Delete `delete=False` temp files in a `finally` block (`os.unlink`).
- `join()` worker threads on shutdown or mark them `daemon=True` deliberately.
