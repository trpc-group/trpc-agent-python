# Skills 自动代码评审 Agent

该示例把 tRPC-Agent 的 Skill、Filter、Container workspace、SQL 存储和
Telemetry 组合成可复现的代码评审流程。默认扫描器是确定性规则引擎，
`--dry-run` / `--fake-model` 不需要任何模型 API Key，但仍完整执行解析、
Filter、沙箱、落库和报告生成。

## 能力

- 输入：unified diff、PR patch、文件列表、Git 工作区和 8 个公开 fixture
- Skill：`skills/code-review/SKILL.md`、规则文档、机器规则和沙箱脚本
- 规则：安全、异步错误、资源泄漏、测试缺失、敏感信息、数据库生命周期
- 沙箱：生产默认 Container 且禁网；Local 只能显式选择用于开发
- 治理：脚本/路径/网络/环境/超时/输出预算在 workspace 创建前由 Filter 决策
- 存储：SQLite 默认，接口和 SQLAlchemy URL 可切换 MySQL/PostgreSQL
- 输出：`review_report.json`、`review_report.md` 和按 task id 查询的完整记录
- 审计：总耗时、沙箱耗时、工具调用、拦截、severity、异常及脱敏统计

详细设计见 [DESIGN.md](./DESIGN.md)。

## 目录

```text
agent/                       # 编排、Filter、沙箱、存储、报告
scripts/init_db.py           # Schema 初始化
scripts/query_review.py      # 按 task id 回放
skills/code-review/
  SKILL.md
  references/rules.md
  references/rules.json
  scripts/review_diff.py
  scripts/timeout_probe.py
tests/fixtures/              # 8 个公开 diff 样本
sample_output/               # 稳定的示例 JSON 报告
```

## 环境

在仓库根目录安装项目和测试依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -r requirements-test.txt
```

Container 是默认运行时，需要可用的 Docker daemon。镜像由 SDK 的
Container runtime 管理，网络模式固定为 `none`。

## 运行

```bash
cd examples/skills_code_review_agent

# 默认生产路径：Container + SQLite + fake model
python run_agent.py \
  --diff-file tests/fixtures/security.diff \
  --runtime container \
  --fake-model

# 显式开发回退，不会调用模型
python run_agent.py \
  --fixture secrets.diff \
  --runtime local \
  --dry-run

# Git 工作区或文件列表
python run_agent.py --repo-path /path/to/repo --runtime container
python run_agent.py --files app/a.py app/b.py --runtime container

# 一次运行全部公开 fixture
python run_agent.py --run-all-fixtures --runtime local --dry-run
```

默认产物在 `output/`，数据库在 `data/reviews.db`。这两个目录均被本示例
忽略，不会污染提交。

## 查询数据库

```bash
python scripts/init_db.py
python scripts/query_review.py cr_TASK_ID
```

查询结果包含 task 状态、输入摘要、sandbox run、Filter 拦截、findings、
监控摘要和最终报告。所有持久化入口都会再次脱敏。

## Filter 与安全边界

执行请求必须使用白名单内的 Python 脚本，且不能包含 shell 操作符、绝对
脚本路径、禁止路径、非白名单网络目标或环境变量。超时和输出预算超过策略
会进入 `needs_human_review`，与 `deny` 一样不会启动沙箱。Container 默认
禁网；运行时仅接收临时 diff 文件，不继承宿主机 provider 环境变量。stdout、
stderr 和输出文件均有大小限制，超时、非零退出和解析失败只会把任务标记为
`completed_with_warnings`，不会使整个评审崩溃。

## 测试

```bash
# 单元与 Local 集成测试
pytest -q tests/examples/test_skills_code_review_agent.py

# 真实 Docker E2E
RUN_CODE_REVIEW_CONTAINER_TEST=1 \
  pytest -q tests/examples/test_skills_code_review_agent.py -k container
```

测试覆盖无问题 diff、安全问题、异步资源泄漏、数据库连接生命周期、测试
缺失、重复 finding、沙箱失败、敏感信息脱敏，以及额外的 Filter 拦截和超时
恢复。公开 fixture 的完整 fake-model 流程目标耗时小于 2 分钟。
