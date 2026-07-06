# 方案设计说明（Eval-Optimize Loop）

**失败归因**：对每条失败 case 依据框架 metric 结果做规则归因，聚成六类——工具调用
错误、工具参数错误、知识召回不足、格式不符合要求、LLM rubric 不达标、最终回复
不匹配。轨迹失败先比调用名字多重集：名字不同判调用错误，名字同而参数异判参数
错误，漏调知识工具时并报召回不足；期望回答可解析为 JSON 而实际不能则判格式违规。
每条归因附证据与中文解释，规则未覆盖的失败按 metric 兜底映射，保证每个失败 case
至少有一条可解释理由；根因按「轨迹在上游」的优先级选取。

**接受策略**：候选须连过六道可配置闸门——验证集通过率与平均分双阈值提升、不得
新增 hard fail、保护 case 不得退化、过拟合守卫、成本预算、时长预算，全部通过才
接受；拒绝理由按严重度点名最关键的闸门，并列出同时未过的其它闸门。

**防过拟合**：优化器只见弱指标（黑盒模式禁用轨迹与召回）与自己那份调参集；
pipeline 一律用独立验证集加完整验收套件复评，出现「训练集提升且验证集退化」即判
过拟合并拒绝，保护 case 与新增失败两道闸门再兜底。示例内置「泄漏调参集」场景：
优化器视角一路变好，独立复评当场揭穿，守卫必将其拒绝。

**产物审计**：每轮候选 prompt、接受理由、成本、耗时、种子与配置快照由优化器
落盘 rounds/ 等目录；pipeline 另存基线与候选的逐 case 记录、归因明细、闸门配置
快照，报告以 JSON 与 Markdown 双格式输出，整条链路离线确定可复现、可追溯审计。

---

**English abstract** — The loop evaluates baseline on train+val with the full metric
suite, clusters failures into six explainable types, runs GEPA-based prompt
optimization, then re-evaluates the candidate on an independent validation set with
per-case deltas. Six configurable gates (improvement, no new hard fails, protected
cases, overfit guard, cost and duration budgets) must all pass before acceptance,
and every round leaves reproducible audit artifacts (candidates, reasons, cost,
seed, config snapshots) in JSON and Markdown reports.
