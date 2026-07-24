# G1 修复概述 — 沙箱 runtime 选择对齐 PRD 契约

## 问题（PRD 原则冲突）
- SKILL.md 声明 `default_runtime: container` / `fallback: local`，但 `agent.py` 真实模式**硬编码 `LocalRuntime()`**，完全无视该契约。
- `ContainerRuntime` 此前是 `NotImplementedError` stub，生产级容器隔离从未真正接入。

## 修复内容
### `sandbox/runtime.py`
- 抽出共享 `_finalize(...)`：脱敏 + 输出截断 + status 映射（`timeout > truncated > failed > ok`），三个后端复用，避免逻辑分叉。
- `LocalRuntime`（SDK `create_local_workspace_runtime`，进程隔离，作为 fallback，永远 available）保留。
- `ContainerRuntime` 从 stub → **SDK `ContainerClient` 真实适配**：建 docker 容器 → base64 暂存脚本目录 → `python run_checks.py` + stdin → 映射 `RunResult`。容器内不继承宿主 env，无密钥泄露面。docker 不可用时 `ensure_available()` 抛 `RuntimeUnavailable`。
- 新增 `CubeRuntime`：**SDK `CubeSandboxClient` 真实适配**（远程 Cube/E2B），`write_file_bytes` 上传 + `commands_run` 执行；缺 `[cube]` extra / 未配 `CR_CUBE_*` 时 `ensure_available()` 抛 `RuntimeUnavailable`。
- 新增 `RuntimeUnavailable` / `select_runtime(kind)` / `build_runtime_with_fallback(default, fallback, policy)`：先试 default，捕获 `RuntimeUnavailable` 后**透明回退** fallback，返回 `(runtime, actual_kind)`。

### `agent.py`（step5 真实模式）
- 从 `skill["sandbox_config"]` 读取 `default_runtime` / `fallback` → `build_runtime_with_fallback(...)`。
- 用返回的 `actual_kind` 落 `sandbox_run.runtime`，DB 记录**真实落地**的后端（`local`/`container`/`cube`）。

### 其他
- `sandbox/__init__.py` 导出新符号。
- README 第 3/4/7 节与 `agent.py` 顶部 docstring 同步（不再称 container 为 stub）。

## 验证
- 真实模式冒烟（`--fixture security --mode real`）：
  日志 `sandbox runtime 'container' unavailable (docker daemon not reachable); trying fallback` → 回退 `local`，`sandbox_run.runtime=local, status=ok`，pipeline `done`。
  证明 default→fallback 链路真实生效、且记录真实后端。
- 新增 `TestRuntimeSelection`（4 用例）锁定选择/回退逻辑（stub 注入，不依赖 docker）。
- **全量回归 162 用例 0 失败**（原 158 + 新增 4）。

## 剩余 PRD 差距
- G5：Filter 超预算检查仍 no-op（`gov.decide(..., {})` 传空 budget）。
- G6：security 规则未用 semgrep（正则+AST 替代，检出达标）。
- G7：Cube/E2B 已留真实适配，需装 `[cube]` extra + 配 `CR_CUBE_*` 才能启用（§6.2「生产可选」）。
