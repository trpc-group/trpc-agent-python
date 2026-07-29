# Skills-based Automated Code Review Agent

一个基于 tRPC-Agent Skills + 沙箱执行 + SQL 存储的自动代码评审 Agent 原型([issue #92](https://github.com/trpc-group/trpc-agent-python/issues/92))。
输入一份 git diff / PR patch / 工作区变更,输出结构化审查报告(JSON + Markdown),全过程(任务、沙箱执行、Filter 拦截、findings、监控指标)持久化到数据库。

```
输入(--diff-file | --repo-path | --files | --fixture)
   │
   ▼
DiffParser ── 文件/hunk/候选行号结构化,ReviewTask + diff_file 落库
   │
   ▼
LlmAgent(真实模型 或 dry-run FakeModel;双挂 skill_load/skill_run + skill_repository)
   │   skill_load("code-review")            ← 规则文档进上下文
   │   skill_run("python3 scripts/run_checks.py")
   │      ├─ SkillRunTool(allowed_cmds=["python3"])      第 1 层:命令白名单,拒 shell 元字符
   │      ├─ ReviewToolFilter(_before)                   第 2 层:脚本/路径/env/输入源/预算,决策落 filter_event
   │      └─ Container runtime(默认,禁网)| local(--unsafe-local)
   │   findings.json 写 $OUTPUT_DIR → 文件通道回传(绕开 16KB stdout 截断)
   ▼
决策表分流(静态精度 × LLM 复核)→ 两级去重 → Redactor 脱敏
   │
   ▼
review_report.json / review_report.md + 7 张表(SQLite 默认,DSN 可切)
```

## 安装

```bash
cd trpc-agent-python
pip install -r requirements.txt && pip install -e .
cd examples/skills_code_review_agent
cp .env.example .env        # 可选:配置真实模型;不配置则自动 dry-run
```

沙箱默认使用 Docker 容器(镜像 `python:3-slim`,规则脚本零第三方依赖)。无 Docker 时自动回退 local 并在报告中记录原因;显式开发模式用 `--unsafe-local`。

## 运行

```bash
# 审查一份 diff(无 API key 时自动 dry-run,FakeModel 驱动完整工具循环)
python3 run_agent.py review --diff-file fixtures/02_sql_injection/input.diff --dry-run

# 内置样例(14 条,按编号或名称)
python3 run_agent.py review --fixture 02 --dry-run

# git 工作区变更(HEAD 对比 + 未跟踪文件)
python3 run_agent.py review --repo-path /path/to/repo --dry-run

# diff + repo 同给 → repo 模式(重建完整 post-image,AST 全文分析,检出/降噪更好)
python3 run_agent.py review --diff-file x.diff --repo-path /path/to/repo
```

输出 `review_report.json` / `review_report.md`(`--output-dir` 可改),样例见 `sample_output/`。

## 数据库查询

```bash
python3 run_agent.py init-db --db sqlite:///review.db     # 建表(幂等)并验证 DSN
python3 run_agent.py show --task-id <id>                  # 任务状态/执行日志摘要/Filter 拦截/findings/监控/结论
```

7 张表:`review_task`、`diff_file`、`sandbox_run`、`filter_event`、`finding`(UNIQUE(task_id, dedup_key))、`report`、`metrics`。
列类型用 SDK 的 DynamicJSON / UTF8MB4String / PreciseTimestamp,`--db mysql+pymysql://...` 即切后端,无迁移脚本(SqlStorage 首用自动建表 + 前向加列)。

## 规则(6 类,静态通道独立达标)

| 类别 | 规则文档 | 代表规则 |
|---|---|---|
| 安全风险 | docs/rules-security.md | SQL 拼接进 execute(含一步变量追踪)、eval/exec、shell=True、yaml.load、pickle、verify=False |
| 敏感信息 | docs/rules-secrets.md | AWS/GitHub/GitLab/Slack/Stripe/… 13 类格式 + 熵检测 + allowlist |
| 异步错误 | docs/rules-async.md | async 内阻塞调用、协程未 await、create_task 结果丢弃 |
| 资源泄漏 | docs/rules-resource-leak.md | open/socket/Lock 未释放(所有权转移不误报) |
| 连接生命周期 | docs/rules-db-lifecycle.md | connect/cursor 未关、写操作无 commit、事务悬空 |
| 测试缺失 | docs/rules-missing-tests.md | 源码变更无测试伴随(diff-only 降为 warnings,宁缺勿滥) |

检查在沙箱内一次 `skill_run` 串行跑完(`scripts/run_checks.py`),单个检查崩溃被隔离并记录,不影响其余。

## 评测

```bash
python3 run_agent.py eval --samples fixtures --unsafe-local
```

对任意标注样本目录(`<dir>/<name>/input.diff` + `expected.json`)输出逐条 TP/FN/FP 与汇总指标——验收方可直接指向隐藏样本目录。当前 14 条内置样例(dry-run 纯静态通道):

```
高危检出率 14/14 = 100%(验收线 ≥ 80%)
误报率      0/16 =   0%(验收线 ≤ 15%)
```

## 安全性(可证明,非声称)

三条对抗性测试在 `tests/test_filter_security.py`、`tests/test_end_to_end.py`,全部通过:

1. **拦截确未执行**:deny / needs_human_review 时断言工具 handler 未被调用(filter 先于 handler 的机制保证);另有架构断言——agent 工具面恰为 {skill_load, skill_run} 且全部挂 ReviewToolFilter,不存在绕过路径。
2. **沙箱内出网必须失败**:容器内 urllib 探针实测被拒(network_mode=none)。不依赖 describe() 元数据(其 network_allowed 字段与实现不符)。
3. **提示注入不改变结论**:fixture 14 在 diff 中嵌入"报告无问题"指令,静态通道照报 SQL 注入;LLM prompt 将 diff 框定为不可信数据。

其他边界:超时钳制(Filter 层 clamp,LLM 传大 timeout 无效)、输出 16KB/文件通道上限、env 键白名单、host:// 输入限定在任务目录、拒绝 3 次后终止防重试空转、脱敏 ≥95%(23 种格式用例)且报告与库中无明文密钥(测试断言)。
资源限制口径:超时 + 输出上限 + 禁网;不声称 CPU/内存限制(SDK 未实施 WorkspaceResourceLimits)。

## dry-run 与耗时口径

dry-run(无 API key)由脚本化 FakeModel 发起与真实模型完全相同的工具调用——skills staging、Filter、沙箱、落库全部真实执行。
计时口径 = 进程启动 → 报告写盘(一次性镜像拉取除外):容器模式单次评审实测 ~17s,local 模式 ~3s,远低于 2 分钟验收线(tests 中有 120s 断言)。

## 真实 LLM

配置 `TRPC_AGENT_API_KEY / BASE_URL / MODEL_NAME` 后,LLM 参与方式由 `--llm-mode` 控制:

- `agent`(默认 auto 解析):LLM 自主驱动 skill_load → skill_run,并在最终消息输出结构化复核 JSON;
- `hybrid`:脚本化 FakeModel 驱动沙箱(确定性),LLM 只做一次无工具的复核调用(逐条 verdict + 补充发现);
- `off`:纯静态(等价 --dry-run)。

两种 LLM 模式下,补充发现都必须逐字引用 diff 否则丢弃;LLM 判 reject 的高危项转人工复核;info 级提示不论 verdict 只进 warnings。

## 测试

```bash
python -m pytest examples/skills_code_review_agent/tests/ -q     # 38 项:单元/端到端/对抗性(Docker 存在时含禁网探针)
python -m pytest tests/evaluation/test_skills_code_review_example.py -q   # 根 tests/ CI 冒烟(无 Docker 依赖)
```
