# Eval → Attribution → Optimize → Gate 自动闭环示例

本示例演示如何把 tRPC-Agent 的 `AgentEvaluator` 与一个"等价扩展机制"的优化器,
串成一个**可复现、可审计、带质量闸门**的自动闭环:先评测,再对失败做可解释归因,
然后生成候选 prompt 并回归验证,最后由 gate 判定候选"是否真的提升、是否牺牲其他
指标、是否过拟合、是否值得回写源 prompt"。

整条流程默认**完全不需要任何 API Key**(确定性 fake 后端),同时提供一个可选的
真实 LLM 后端(OpenAI 兼容,如 hy3),两套后端共用同一套编排、归因、gate 与审计逻辑。

## 关键特性

- **六阶段闭环**:评测 → 失败归因 → 优化执行 → 回归验证 → 接受策略(gate) → 产物审计。
- **无 Key 可跑**:确定性 Fake Model / Fake Judge,秒级完成,结果完全可复现(固定 `seed`)。
- **可解释失败归因**:把失败稳定归到 6 大类,并给出一句话原因与 `regression` 标记。
- **防过拟合 gate**:关键 case 退化 / 新增 hard fail 一律拒绝,即使验证集总分提升。
- **完整审计产物**:结构化 + 人读报告、每轮候选快照、可复现配置全部落盘。
- **可选真实后端**:`EVAL_BACKEND=real` 一键切换到真实 LLM 生成 + `llm_rubric_response` judge。

## 目录结构

```text
eval_optimize_loop/
├── run_pipeline.py                  # 入口:组装配置、运行闭环、落盘报告
├── pipeline.py                      # 编排层:把 6 个阶段串起来
├── attribution.py                   # 失败归因(6 大类可解释分类)
├── gate.py                          # 接受策略(可配置 gate)
├── optimizer.py                     # 规则式优化器(与 GEPA 等价的确定性机制)
├── fake_agent.py                    # 确定性 Fake Model / Fake Judge(call_agent)
├── real_call_agent.py              # 真实 LLM call_agent(接入 OpenAI 兼容后端,如 hy3)
├── verify_real.py                   # 真实凭据连通性自检(单条调用)
├── prompts/
│   ├── baseline_system.md           # fake 模式 baseline prompt
│   └── baseline_real.md             # 真实模式 baseline prompt
├── config/
│   ├── optimizer.json               # fake 模式配置:指标 / gate / 候选池 / 种子
│   ├── real_optimizer.json          # 真实模式配置:llm_rubric_response judge / gate / 候选池
│   └── real_optimizer_smoke.json    # 真实模式轻量变体(低配额验证用)
├── data/
│   ├── train.evalset.json           # fake 模式 3 条训练 case
│   ├── val.evalset.json             # fake 模式 3 条验证 case(含过拟合退化样本)
│   ├── real/                        # 真实模式 3 训练 + 3 验证(含退化哨兵 val_robust)
│   └── real_smoke/                  # 真实模式轻量变体(1 训练 + 2 验证)
├── .env.example                     # 环境变量模板(TRPC_AGENT_*)
├── .gitignore                       # 忽略本地 .env(避免真实凭据入库)
├── artifacts/                       # 运行产物(报告 + 候选快照 + 配置快照)
└── optimization_report.json         # 示例输出(fake 模式,与 artifacts 一致)
```

## 快速开始(无需 API Key,验收主路径)

```bash
# 首次安装依赖
pip install -r ../../requirements.txt

# 从仓库根运行
python examples/optimization/eval_optimize_loop/run_pipeline.py
```

运行结束后,报告写入 `artifacts/`:结构化 `optimization_report.json`、人读
`optimization_report.md`、每轮候选快照 `candidates/<label>.md`、可复现配置
`optimizer.snapshot.json`。

## 可选:真实 LLM 后端

把三项凭据写入本目录的 `.env`(该文件已被 `.gitignore` 忽略,不会随 PR 提交):

```bash
TRPC_AGENT_API_KEY=你的真实key
TRPC_AGENT_BASE_URL=https://your-endpoint/v1
TRPC_AGENT_MODEL_NAME=your-model
```

先用自检脚本确认凭据可用,再用 `EVAL_BACKEND=real` 切换后端:

```bash
# 单条调用自检
python examples/optimization/eval_optimize_loop/verify_real.py

# 完整真实闭环(需配额充足;生成 + judge 约 60 次调用)
EVAL_BACKEND=real python examples/optimization/eval_optimize_loop/run_pipeline.py

# 轻量变体:约 12 次调用,适合配额/限流较紧时验证完整闭环
EVAL_BACKEND=real REAL_SMOKE=1 python examples/optimization/eval_optimize_loop/run_pipeline.py
```

真实模式复用同一套 6 阶段编排、gate、归因与审计逻辑,只替换两处:

- **生成**:`real_call_agent.call_agent` 用真实模型跑一次推理(system prompt 即当前候选);
- **判定**:`config/real_optimizer.json` 用 `llm_rubric_response`,让真实模型当 judge
  (rubric 从 `${TRPC_AGENT_*}` 占位符展开,框架自动注入 judge 模型)。

> **说明(框架约束与限流)**
> 1. `llm_rubric_response` 的 rubric 是**全局**的(每条 case 共用同一组判据),无法逐 case
>    定制。真实模式因此使用一致的多 rubric 判据(中文 / 问候 / 天气恰当 / 自然文本 /
>    实时数据真实);最丰富的"逐 case 三类情况"演示保留在 fake 模式。
> 2. 真实后端在高并发下可能触发限流(429)。真实模式已做串行评估 + 节流;若仍遇限流,
>    个别 case 会被保守判失败,待配额充足或低峰重跑即可得到干净结果。

## Pipeline 六阶段

1. **Baseline 评测**:`AgentEvaluator.evaluate_eval_set` 对训练/验证集分别打分,
   记录每条 case 的 metric 分、pass/fail、失败原因与关键轨迹。
2. **失败归因**:`attribution.analyze_set` 解析实际回复 + query + 期望,把失败归到
   `tool_call_error / format_error / final_mismatch / param_error / knowledge_recall / llm_rubric` 之一。
3. **优化执行**:`RuleBasedOptimizer` 从候选池(对应归因发现的能力缺口)生成候选,
   经 `TargetPrompt` 注册并可在接受后写回源文件。
4. **回归验证**:每个候选重新跑验证集,与 baseline 做逐 case 对比(new_pass / new_fail / kept_*)。
5. **接受策略**:`gate.evaluate_gate` 按可配置规则决策(见下)。
6. **产物审计**:每轮候选 prompt、评测结果、接受/拒绝理由、成本、耗时、种子全部落盘。

## 失败归因方法

归因由确定性规则驱动(fake 模式不依赖 LLM,故稳定、可解释、可复现):解析 fake agent
的协议文本 `[TOOL]/[FMT]/[FINAL]`,结合 query 与期望——天气类问题未见 `[TOOL]` 调用 →
`tool_call_error`(并说明知识未召回);期望 `[FMT] json` 而实际为 text → `format_error`;
最终回复不含期望文本 → `final_mismatch`。每条失败 case 至少给出一句话原因,并标记
`regression`(baseline 通过、当前候选失败)。真实模式则叠加 judge 输出的可解释 reason。

## 接受策略(gate)

四类可配置规则,全部命中才接受:

- `min_val_improvement`:验证集总分提升 ≥ 阈值;
- `no_new_hard_fail`:不允许 baseline 通过的 case 在候选下失败;
- `key_cases_no_regression`:关键 case(如 `val_robust`)绝不能退化;
- `max_cost_usd`:成本硬上限(fake 模式为 0,不触发)。

## 防过拟合策略

过拟合的典型表现是"训练集提升但验证集退化"。本示例用验证集里的 `val_robust`
(baseline 已通过、且不依赖任何优化能力)作为哨兵:任何让该 case 退化的候选
(如"对所有问题都调 weather"的过度候选、或强制 JSON 破坏纯文本回复的候选)都会触发
`key_cases_no_regression` / `no_new_hard_fail` 被**拒绝**,即使其验证集总分看似未降。

## 产物审计

`artifacts/` 下保存:结构化 `optimization_report.json`(含 meta 种子/耗时/预算、
baseline 与每个 candidate 的逐 case 分数、delta、gate 决策与理由、失败归因统计)、
人读 `optimization_report.md`、`candidates/<label>.md` 候选快照、
`optimizer.snapshot.json` 复现配置。结合固定 `seed`,整条 pipeline 可完全复现。

## 方案设计说明

本闭环把"评测—优化"从一次性的 prompt 改写升级为带质量闸门的回归实验。评测直接复用
`AgentEvaluator`,但通过 `call_agent` 黑盒接入一个确定性 Fake Model:它解析当前 prompt
中的自然语言指令(如"调用 weather 工具")改变行为并返回结构化协议文本,从而在没有 LLM
的情况下仍能体现"优化→分数变化"的反馈信号,保证无 Key 可跑。失败归因在 pipeline 层
补齐:框架只给 pass/fail,本模块解析 `[TOOL]/[FMT]/[FINAL]` 把失败归到 6 大类并给出可
解释原因,使"为什么失败"可被人与后续优化消费。优化器采用与 GEPA 等价的确定性规则搜索
(候选池对应归因发现的能力缺口),而非依赖真实 reflection_lm,既满足"等价扩展机制"也
满足可复现。接受策略是防过拟合的核心:即便验证集总分提升,只要出现"关键 case 退化"或
"新增 hard fail"一律拒绝,成本/耗时作为硬上限兜底。产物审计把每轮候选、评测、决策理由、
成本、种子全部落盘,使任何一次"是否回写生产"都可被人工复核与复现——这与真实业务中
"评测差则优化过拟合、不可审计则改出的 prompt 难以上线"的痛点直接对应。

## 验收对照

- 样例 case 全部可运行并生成完整报告 ✅
- 接受/拒绝决策:好候选接受,过度/无效候选拒绝 ✅
- 过拟合场景(训练提升、验证退化)被拒绝 ✅
- 失败归因有可解释原因、分类稳定 ✅
- 无 Key 下完整 pipeline 耗时 ≪ 3 分钟(实测秒级)✅
- 报告含 baseline/candidate 分数、逐 case delta、gate 决策与理由 ✅
