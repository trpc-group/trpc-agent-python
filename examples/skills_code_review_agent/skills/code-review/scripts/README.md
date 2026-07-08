# Code Review Skill 脚本

本目录中的脚本是沙箱执行目标。脚本优先从 argv 读取 unified diff 文件路径；没有 argv 时从 stdin 读取。脚本应向 stdout 输出紧凑文本或 JSON，只有脚本自身失败时才返回非零退出码。

## 脚本列表

- `diff_summary.py`
  - 输入：unified diff。
  - 输出：`files=<n> hunks=<n> additions=<n>`。
  - 用途：证明 diff 解析可以在 workspace runtime 内运行。

- `static_review.py`
  - 输入：unified diff。
  - 输出：静态危险模式命中次数的 JSON。
  - 用途：在沙箱侧执行轻量静态检查。

- `test_probe.py`
  - 输入：unified diff。
  - 输出：源码文件、测试文件和测试缺失状态的 JSON。
  - 用途：在沙箱侧探测测试覆盖缺口。

- `scanner_probe.py`
  - 输入：unified diff。
  - 输出：scanner run 摘要和规范化 findings 的 JSON。
  - 用途：可选聚合 bandit、ruff、detect-secrets 和 semgrep；未安装的 scanner 会标记为 `skipped`。

- `unit_test_probe.py`
  - 输入：环境变量 `CR_TEST_COMMAND`。
  - 输出：配置的测试命令 stdout/stderr。
  - 用途：Filter 允许后执行可选单元测试命令。

## 安全契约

Agent 必须先运行 Filter preflight，再调用任何脚本。脚本不能读取 secret，不能使用未授权网络访问，也不能修改沙箱 workspace 外部文件。输出在持久化前会被截断并脱敏。

# Code Review Skill Scripts

Scripts in this directory are sandbox targets. They read a unified diff path from argv when provided; otherwise they read stdin. They must write compact text or JSON to stdout and should return a non-zero exit code only when the script itself fails.

## Scripts

- `diff_summary.py`
  - Input: unified diff.
  - Output: `files=<n> hunks=<n> additions=<n>`.
  - Purpose: prove diff parsing can run inside a workspace runtime.

- `static_review.py`
  - Input: unified diff.
  - Output: JSON object with static pattern hit counts.
  - Purpose: sandbox-side static checks for dangerous patterns.

- `test_probe.py`
  - Input: unified diff.
  - Output: JSON object listing source/test files and missing-test status.
  - Purpose: sandbox-side test coverage probe.

- `scanner_probe.py`
  - Input: unified diff.
  - Output: JSON object with scanner run summaries and normalized findings.
  - Purpose: optional aggregation for bandit, ruff, detect-secrets, and semgrep; missing scanners are marked as `skipped`.

- `unit_test_probe.py`
  - Input: environment variable `CR_TEST_COMMAND`.
  - Output: configured test command stdout/stderr.
  - Purpose: optional unit test execution after Filter approval.

## Safety Contract

The agent must run Filter preflight before invoking any script. Scripts must not read secrets, use network access, or mutate files outside the sandbox workspace. Output is capped and redacted before persistence.
