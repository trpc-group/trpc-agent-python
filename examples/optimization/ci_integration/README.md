# CI Integration — 评测与优化拼成 CI/CD 闭环

> **适用场景**：业务希望在持续集成流水线中同时运行 prompt 质量守门（每次 PR 触发）与 prompt 自动优化（夜间窗口运行），形成"PR 守门 → 夜间优化 → 写回 prompt → 下一次 PR 跑新 prompt"的演进闭环。本 example 演示 `AgentEvaluator.evaluate`（pytest）与 `AgentOptimizer.optimize` 共享同一份数据集、同一个 `call_agent`、同一对 prompt 文件的端到端集成方式。阅读前请先熟悉 `quickstart/README.md` §2。

## 1 · 适用问题与设计目标

prompt 工程在工程化场景下的两类需求：

- **PR 守门**：每次 PR 触发自动跑评估，分数低于阈值即 CI 红灯，阻止劣化 prompt 进主干
- **夜间优化**：在低峰期跑反思优化，把更优 prompt 写回源文件，下一次 PR 自动用上

单独使用任一链路都不足：纯守门不会让 prompt 自动变好，纯优化没有质量门禁。本 example 把两者集成到同一份资产之上：

- **同一份 evalset**：物理上拆 train / val（SDK 强制约束，防泄漏），逻辑上是一套连续语料
- **同一个 `call_agent`**：pytest 与 optimizer 都从 `agent/agent.py` 导入相同实现，prompt 改动一处生效
- **同一对 prompt 文件**：optimizer 用 `update_source=True` 写回源文件，pytest 下次自动读取

| 输入 | 输出 |
| --- | --- |
| 一份 evalset（拆为 train / val 两文件）+ 一个 call_agent + 一对 prompt 文件 | PR 阶段：pytest 红 / 绿 + JUnit XML |
| 两个 shell 入口（PR 检查 + 夜间优化） | 夜间阶段：源 prompt 文件被最优候选覆盖 |

### 本 example 演示的最小用例

| 维度 | 值 |
| --- | --- |
| 业务任务 | RESTful API 描述 → 严格 JSON 结构化摘要 |
| 优化目标 | `agent/prompts/system.md` + `agent/prompts/skill.md` |
| 验证指标 | `final_response_avg_score`（exact 匹配规范化 JSON，CI 上无需 LLM judge） |
| 训练 / 验证规模 | 见 `data/train.evalset.json` / `data/val.evalset.json` |

## 2 · 术语对照

仅列出本 example 引入的新概念。基础术语见 `quickstart/README.md` §2。

| 术语 | 含义 |
| --- | --- |
| **PR 守门（pre-merge gate）** | 在 PR 触发的 CI 流程中跑 `AgentEvaluator.evaluate`，分数低于阈值时 pytest 抛 `AssertionError`、CI exit code 非 0、合并被阻止。 |
| **夜间优化（nightly optimize）** | 在 CI 低峰窗口跑 `AgentOptimizer.optimize`，`update_source=True` 时优化结束后最优候选自动覆盖源 prompt 文件。 |
| **`update_source=True`** | 优化成功（`OptimizeResult.status=SUCCEEDED`）后用最优候选覆盖 `TargetPrompt` 注册的源文件。CI 闭环的关键开关。 |
| **JUnit XML** | pytest `--junitxml=<file>` 输出的标准化测试报告格式。GitHub Actions / 蓝盾流水线 / Tencent CI 等主流平台均原生解析。 |

## 3 · 运行示例

### 3.1 安装依赖

```bash
pip install -e ".[optimize]"
pip install pytest pytest-asyncio
```

### 3.2 配置环境变量

```bash
export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
```

### 3.3 PR 阶段：pytest 守门

```bash
cd examples/optimization/ci_integration
PYTHONPATH=../../.. bash ci/run_pr_check.sh
```

行为：

- pytest 加载 `tests/test_agent_quality.py` → 调 `AgentEvaluator.evaluate(call_agent, val.evalset.json, ...)`
- 失败时框架抛 `AssertionError` → pytest exit code != 0 → CI 红灯
- JUnit XML 落到 `runs/pytest_report.xml`，CI 平台原生展示

### 3.4 夜间窗口：跑优化并写回

```bash
cd examples/optimization/ci_integration
PYTHONPATH=../../.. bash ci/run_nightly_optimize.sh
```

行为：

- `AgentOptimizer.optimize(update_source=True)` 跑 GEPA 反思
- 优化成功后最优候选覆盖 `agent/prompts/system.md` + `agent/prompts/skill.md`
- 真实流水线在末尾可加 `git diff agent/prompts/` + 自动开 PR

下一次 PR 触发的 `run_pr_check.sh` 自动用上新 prompt → 闭环达成。

### 3.5 产物结构

```
runs/
├── pytest_eval/                  # AgentEvaluator 输出（pytest 阶段）
├── pytest_report.xml             # JUnit XML（CI 平台原生消费）
└── optimize_<timestamp>/         # AgentOptimizer 输出（夜间阶段）
    ├── result.json
    ├── summary.txt
    ├── baseline_prompts/
    ├── best_prompts/
    └── rounds/
```

## 4 · 架构与数据流

```
                        ┌────────────────────────────────┐
                        │   agent/                       │
                        │   ├── agent.py    (call_agent) │
                        │   └── prompts/                  │
                        │       ├── system.md             │
                        │       └── skill.md              │
                        └─────────────┬───────────────────┘
                                      │ 共享
            ┌─────────────────────────┴──────────────────────────┐
            │                                                    │
       ┌────▼────────────┐                          ┌────────────▼─────┐
       │ AgentEvaluator   │                          │ AgentOptimizer    │
       │   .evaluate()    │                          │   .optimize()      │
       │                  │                          │                    │
       │ 触发: PR         │                          │ 触发: 夜间窗口      │
       │ 数据: val.json   │                          │ 数据: train + val  │
       │ 产出: 红/绿       │                          │ 产出: 写回 prompt  │
       │ 退出码: 守门      │                          │ update_source=True │
       └──────────────────┘                          └────────────────────┘
                                      │
                                      └─→ 共享同一份 data/ + 同一份 metric 定义
```

### 4.1 文件清单

| 文件 | 角色 | 接入自有业务时的修改方向 |
| --- | --- | --- |
| `run_optimization.py` | 夜间优化入口，`update_source=True` | 与 quickstart 同；保持 `update_source=True` |
| `agent/agent.py` | 共享 `call_agent`（被 pytest + optimizer 同时调用） | 替换为业务 agent；保持函数命名 `call_agent` |
| `agent/prompts/{system,skill}.md` | 优化目标 + 守门读取的 prompt | 写入业务 baseline |
| `tests/test_agent_quality.py` | pytest 守门入口 | 调整 `agent_name` / 数据路径；测试方法保持不变 |
| `optimizer.json` | 算法 + metric 配置 | 与单 example 写法一致 |
| `data/train.evalset.json` / `data/val.evalset.json` | 训练 / 验证集 | 替换为业务用例 |
| `ci/run_pr_check.sh` | PR 阶段 shell 入口 | 调整 `pytest` 参数 / `--junitxml` 路径 |
| `ci/run_nightly_optimize.sh` | 夜间阶段 shell 入口 | 调整调用频率 / 失败回滚策略 |

### 4.2 train / val 拆分的强制约束

SDK `_validate_inputs` 会校验 `train_dataset_path != validation_dataset_path` 防止数据泄漏。物理上必须是两个文件，但二者：

- schema 完全一致（同一套 `EvalSet` 模型）
- `eval_set_id` 命名族可共享（如 `api_summarizer.train` / `api_summarizer.val`）
- metric 定义统一在 `optimizer.json` 中（pytest 走 `test_config.json` 同目录约定时也是一致 schema）

逻辑上仍是同一套数据语言。

## 5 · 关键配置

### 5.1 `update_source=True` 的语义

```python
await AgentOptimizer.optimize(
    ...,
    update_source=True,  # 优化成功后覆盖源 prompt 文件
)
```

| 状态 | 行为 |
| --- | --- |
| `OptimizeResult.status=SUCCEEDED` | 最优候选写入 `TargetPrompt` 注册的源文件 |
| `status=FAILED` / `BUDGET_EXHAUSTED` 等其他 | 源文件保持不变；候选只在 `runs/<timestamp>/best_prompts/` |

CI 闭环依赖该开关：只有"优化真的找到了更好的 prompt"才会写回；否则保持现状不污染主干。

### 5.2 CI 上不依赖 LLM judge

`agent/agent.py` 中 `_normalize_json` 把 LLM 输出规范化成稳定 JSON 字符串：

```python
json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
```

→ `final_response_avg_score(text.match=exact)` 可直接逐字符比对，**CI 上完全不需要 LLM judge**：

- **快**：单 case 一次评测仅一次 agent LLM 调用
- **稳**：同一 prompt 同一 case 输出确定（temperature=0.1）
- **可重复**：CI 多次跑结果一致

LLM judge 在主观维度评估上不可替代，但在结构化输出场景下应优先选择 text exact + 规范化方案。

### 5.3 失败 case 的可观测性

pytest 阶段失败时框架抛 `AssertionError`，错误消息包含每条 case 的失败明细 JSON。CI 平台展示 stack trace 时可直接看到具体哪条 case 失败、agent 实际输出是什么、与 expected 的差异在哪。无需额外日志解析逻辑。

## 6 · CI/CD 闭环设计要点

### 6.1 为什么 evaluate 与 optimize 共享 call_agent

prompt 工程的核心约束：**评测时使用的 agent 和优化时使用的 agent 必须等价**，否则优化方向与守门方向不一致，会出现"优化器找到了 evaluator 验证不了的好 prompt"或反向问题。

通过共享 `agent/agent.py` 中的 `call_agent` 实现，从代码层面保证等价性。任何 agent 行为改动（模型切换、temperature 调整、output schema 变化）只需改一处，pytest 与 optimizer 同时生效。

### 6.2 为什么夜间窗口跑而不实时优化

- LLM 调用预算有限，反思优化耗时数分钟到数十分钟，不适合 PR 触发
- 优化结果具有方差性，需在低峰期反复多轮验证后再发版
- 写回 prompt 文件应经过 git diff / 人工 review / 自动开 PR 等流程而非直接进主干

### 6.3 何时考虑灰度发布

`update_source=True` 直接覆盖源文件适合：

- 团队规模小，PR review 流程已能拦截不合理改动
- prompt 改动影响面可控（单 agent / 单业务）

不适合：

- 多业务线共享同一份 prompt 仓库
- 改动后需灰度观察线上 metric 变化

后者建议改为 `update_source=False` + 把 `runs/<timestamp>/best_prompts/` 接入业务自有的灰度发布工具。

## 7 · 常见问题

**Q：能否在同一个 CI job 中先跑评估再跑优化？**
A：技术上可行，但不推荐。评估应快速给反馈（< 1 min），优化耗时长（> 5 min）。两者拆成独立 job / 独立 trigger 更符合工程实践。

**Q：CI 如何识别"优化没改善"该如何回退？**
A：`run_nightly_optimize.sh` 末尾建议加 `git diff --quiet agent/prompts/` 判断是否有改动；无改动直接退出。如果改动质量后续被发现退化，由 PR review 拒绝合并即可——`update_source=True` 的写回不直接进主干，仍走标准 PR 流程。

**Q：pytest 与 optimizer 用的 metric 配置不同会怎样？**
A：会出现"评测能过但优化器看到的分数低"或反向问题。本 example 通过让 pytest 走 `AgentEvaluator.evaluate(test_config_path=...)`、optimizer 走 `optimizer.json.evaluate.metrics`、二者使用相同 schema 来避免漂移。生产中建议把 metric 配置抽成一份共享 JSON，两边引用。

**Q：reflection LM 失败重试预算？**
A：`optimizer.json` 中 `algorithm.max_iterations_without_improvement` 控制无改善早停；reflection LM 单次调用失败由 SDK 内部重试 1–2 次。CI 场景建议把 `max_metric_calls` 调到合理上限避免单次跑爆预算。

**Q：JUnit XML 中能看到具体失败 case 吗？**
A：能。pytest 把 `AssertionError` 消息原样写入 XML，CI 平台展示时可直接看到失败明细 JSON。

## 8 · 接入自有 CI 的步骤

1. **整理 evalset**：拆 `train.evalset.json` / `val.evalset.json` 两文件
2. **定义 metric**：在 `optimizer.json` 与 pytest 测试中使用同一 schema 的 metric 配置
3. **实现共享 call_agent**：`agent/agent.py` 写一份 `call_agent`，pytest 与 optimizer 都从此处导入
4. **设置 `update_source=True`**：夜间优化入口的关键开关
5. **配置 CI 流水线**：
   - PR 触发 `bash ci/run_pr_check.sh`，解析 `runs/pytest_report.xml`
   - 夜间触发 `bash ci/run_nightly_optimize.sh`，末尾加 git diff + 自动开 PR
6. **观察首轮闭环**：从 baseline pytest 红 → 夜间优化 → PR 自动开 → review → 合并 → 下一次 PR 绿
