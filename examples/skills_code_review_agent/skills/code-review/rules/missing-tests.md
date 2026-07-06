# 测试缺失 Missing Tests (`missing_tests`)

变更集级规则：改了代码却没改任何测试。
Changeset-level rule: code changed, no test files touched.

| Rule | Trigger 触发条件 | Severity | Confidence |
|---|---|---|---|
| TST001 | ≥1 个代码文件新增行（.py/.go/.js/.ts/…）且变更集中无任何测试文件 | low | 0.90 |

测试文件识别：`tests/` / `test/` 目录、`test_*.py`、`*_test.py`、`*.test.ts`、
`*_test.go` 等命名约定。删除的文件与二进制文件不计入。每个变更集最多报一条
（挂在第一个代码文件上，line=1），避免刷屏。
Test files are recognized by path (`tests/`, `test/`) and naming conventions
(`test_*.py`, `*_test.py`, `*.test.ts`, `*_test.go`). Deleted and binary files
are ignored. At most ONE finding per changeset (anchored to the first changed
code file, line 1) to avoid noise.

## 修复建议 Remediation

为变更的行为补充或更新单元测试；未被测试覆盖的改动会静默回归。
Add or update unit tests covering the changed behavior — untested changes
regress silently.
