# Advanced Strategies — GEPA 高阶策略组合 A/B 对照

> **适用场景**：已熟悉 GEPA 基本流程，希望进一步理解 `candidate_selection_strategy` / `frontier_type` / `use_merge` / `skip_perfect_score` 等高阶配置在真实任务上的行为差异。本 example 跑 baseline 与 advanced 两套配置后用 `compare.py` 输出对比表。阅读前请先熟悉 `quickstart/README.md` §2。

## 1 · 适用问题与设计目标

GEPA 高阶配置开关多，业务方常见困惑：

- "打开 `use_merge=true` 真的会触发 merge 吗？"
- "`frontier_type` 选 `instance` 还是 `objective` 对我的任务有什么影响？"
- "`skip_perfect_score=true` 能省多少 reflection LM 调用？"

单跑一次优化往往看不出差异，因为 GEPA 在多数任务上都能收敛到相近 `best_pass_rate`。本 example 用 A/B 对照方法暴露差异：

- **方案 A（baseline）**：基础策略组合
- **方案 B（advanced）**：高阶策略组合（`frontier_type=objective` + `skip_perfect_score=true` + `use_merge=true`）
- **任务设计**：地址解析任务，混合"完整地址"与"缺信息地址"两类 case，制造多目标局部最优空间

| 输入 | 输出 |
| --- | --- |
| 两套不同的 `optimizer_*.json` 配置 | 两次独立的优化运行结果 |
| `compare.py` 解析两次的 `result.json` | 多维度对比表 |

### 本 example 演示的最小用例

| 维度 | 值 |
| --- | --- |
| 业务任务 | 自由文本地址解析为严格 JSON `{country, city, postal_code, street}`（缺信息字段输出 `null`） |
| 优化目标 | `agent/prompts/system.md` 单字段 |
| 训练集 | 6 条 case：3 条完整地址 + 3 条缺信息地址 |
| 验证集 | 6 条 case |

## 2 · 术语对照

仅列出本 example 引入的新概念。基础术语见 `quickstart/README.md` §2。

| 术语 | 含义 |
| --- | --- |
| **candidate_selection_strategy** | 反思每轮选哪个候选作为亲本的策略。可选 `pareto` / `current_best` / `epsilon_greedy` / `top_k_pareto`。 |
| **frontier_type** | Pareto 前沿粒度。可选 `instance`（按 case） / `objective`（按 metric） / `hybrid`（双层） / `cartesian`（按 case×metric）。 |
| **skip_perfect_score** | 反思 minibatch 抽样时是否跳过已满分的 case。 |
| **predictor-level merge** | merge 操作在 prompt 字段层面进行。**需要至少 2 个字段才有意义**——单字段优化下 merge 永远不会触发。 |
| **merge_val_overlap_floor** | 触发 merge 的最低 val 集 case 重叠数（默认 5）。 |

## 3 · 运行示例

### 3.1 安装依赖

```bash
pip install -e ".[optimize]"
```

### 3.2 配置环境变量

```bash
export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
```

### 3.3 顺序跑两次优化

```bash
cd examples/optimization/advanced_strategies
python3 run_baseline.py        # 配置 A：basic 策略组合
python3 run_advanced.py        # 配置 B：高阶策略组合
```

每次运行约 3 分钟。

### 3.4 输出对比表

```bash
python3 compare.py
```

`compare.py` 自动选取 `runs/` 下最新的 `baseline_*` 与 `advanced_*` 目录解析 `result.json`，输出多维度对比表（轮次数、接受率、merge 触发次数、reflection LM 调用数、baseline / best pass_rate 等）。

## 4 · 架构与数据流

```
[run_baseline.py]                       [run_advanced.py]
    │                                       │
    ├── optimizer_baseline.json             ├── optimizer_advanced.json
    │   instance frontier                   │   objective frontier
    │   skip_perfect_score=false             │   skip_perfect_score=true
    │   use_merge=false                      │   use_merge=true（单字段实际不触发）
    │                                       │
    └── runs/baseline_<ts>/result.json      └── runs/advanced_<ts>/result.json

                       ┌────────────┴────────────┐
                       │   python3 compare.py    │
                       │   _latest("baseline")   │
                       │   _latest("advanced")   │
                       │   解析 result.json      │
                       │   输出对比表             │
                       └─────────────────────────┘
```

### 4.1 文件清单

| 文件 | 角色 | 接入自有业务时的修改方向 |
| --- | --- | --- |
| `run_baseline.py` | basic 配置入口 | 与 quickstart 同 |
| `run_advanced.py` | 高阶配置入口 | 调整 `optimizer_advanced.json` 中策略组合 |
| `compare.py` | 解析两次 `result.json` 输出对比表 | 添加 / 删除关注的对比维度 |
| `agent/agent.py` | 地址解析 LlmAgent + `_normalize_json` | 替换为业务 agent |
| `agent/prompts/system.md` | baseline prompt（故意极简） | 写入业务 baseline |
| `optimizer_baseline.json` | basic 策略 JSON | 调整阈值与 metric |
| `optimizer_advanced.json` | 高阶策略 JSON | 调整高阶开关 |
| `data/train.evalset.json` / `data/val.evalset.json` | 数据集 | 替换为业务用例 |

## 5 · 高阶策略对照

### 5.1 配置差异速查

| 配置项 | baseline | advanced |
| --- | --- | --- |
| `candidate_selection_strategy` | `pareto` | `pareto` |
| `frontier_type` | `instance` | `objective` |
| `skip_perfect_score` | `false` | `true` |
| `use_merge` | `false` | `true` |
| `module_selector` | `round_robin` | `round_robin` |

### 5.2 `frontier_type` instance vs objective

| 取值 | 行为 | 在本任务上的表现 |
| --- | --- | --- |
| `instance` | 每条 case 维护一个 best 候选，反思看逐 case 反馈 | 接受门槛较高（需在某 case 上严格优于历史），rounds_accepted 较少 |
| `objective` | 每条 metric 维护一个 best，反思看聚合分数 | 接受门槛较低（聚合分有提升即接受），rounds_accepted 较多但 valset 易震荡 |

`objective` 更激进。小训练集（< 10 case）下可能过拟合 train minibatch，造成 valset pass_rate 波动。

### 5.3 `skip_perfect_score` 的实际节省

理论上能减少不必要的 reflection LM 调用。实际节省幅度取决于：

- baseline 起点高度（baseline=0 时早期满分 case 极少，节省有限）
- 训练集规模（小训练集下满分 case 在 minibatch 中比例不稳定）

本 example 实测约节省 1 次 reflection 调用，差异不显著。该开关在**大规模训练集 + 高基线起点**场景下效果更明显。

## 6 · 关键配置（含两条踩坑警示）

### 6.1 `use_merge` 在单字段优化下不会触发

merge 是 predictor-level 操作，**需要至少 2 个字段才有意义**。本 example 是单字段优化，因此 `optimizer_advanced.json` 中 `use_merge=true` 设置无副作用，但也不会带来任何实际 merge 行为——`compare.py` 输出中 `merge_rounds_total=0` 是预期。

需要观察 merge 实际效果时，参见 `multi_agent_pipeline/` example，其 4 字段配置下 merge 会真实触发。

### 6.2 `result.json` 字段命名为 camelCase

SDK 内部使用 snake_case 字段名（如 `stop_reason` / `total_rounds` / `best_pass_rate`），但序列化到 `result.json` 时会自动转换为 camelCase（`stopReason` / `totalRounds` / `bestPassRate`）。

这是因为 `EvalBaseModel` 的 `alias_generator=to_camel`，序列化时 `by_alias=True`。

**踩坑提醒**：用 Python 读 `result.json` 时按 camelCase 索引：

```python
data = json.loads(Path("result.json").read_text())
print(data["bestPassRate"])      # ✅
print(data["best_pass_rate"])    # ❌ KeyError
```

`compare.py` 中已经按 camelCase 解析；自有脚本读 `result.json` 时同样按此约定。

### 6.3 `frontier_type` 取值约束

SDK 仅接受以下 4 个字面量值：

```
"instance" | "objective" | "hybrid" | "cartesian"
```

其他取值（如 `"aggregate"` / `"mixed"`）会在 pydantic 层面直接 `ValidationError`，无法启动优化。配置前请确认拼写。

## 7 · 常见问题

**Q：为什么两次跑的 `best_pass_rate` 经常相同？**
A：GEPA 是 Pareto 优化算法，在简单任务 + 充足预算下两套策略最终常收敛到同一最优。差异往往体现在**到达路径**（轮次数、接受率、merge 行为）而非最终分数。这正是本 example 设计 `compare.py` 关注多维度而非单一 `best_pass_rate` 的原因。

**Q：advanced 接受了 4 轮但 baseline 只接受了 2 轮，是不是 advanced 更好？**
A：不一定。`objective` frontier 接受门槛低，可能"接受了一个 train 上更好但 val 上更差"的候选。需结合每轮的 `valset pass_rate` 趋势观察是否过拟合。

**Q：`compare.py` 输出 `merge_rounds_total=0` 但我开了 `use_merge=true`？**
A：单字段优化下符合预期。参见 §6.1。

**Q：怎么知道是哪一轮被接受的、是反思还是 merge？**
A：`result.json` 中 `rounds[*]` 数组每条记录都有 `accepted: true/false` 和 `kind: "reflective" | "merge"` 字段，可直接遍历查看。

**Q：advanced 配置里 `seed` 应该和 baseline 保持一致吗？**
A：保持一致便于对比时排除随机性影响。本 example 两份 JSON 都用同一 `seed`。

## 8 · 接入自有业务的步骤

1. **复制本 example 作为对照模板**：保留 `run_baseline.py` / `run_advanced.py` / `compare.py` 三脚本结构
2. **替换业务 agent**：`agent/agent.py` 改为业务 agent 实现
3. **设计两套配置 JSON**：
   - `optimizer_baseline.json`：当前线上配置或默认配置
   - `optimizer_advanced.json`：希望验证的高阶组合
   - 二者保持 `seed` / `max_metric_calls` 一致以便公平对比
4. **替换数据集**：业务 train / val
5. **跑两次 + compare**：根据对比表多维度评估高阶配置在业务任务上的实际收益
6. **决策**：把对比表中表现明显更优的配置作为生产配置

> 高阶配置不是"越复杂越好"。许多任务上 baseline 配置已能达到合理收敛，advanced 只在特定任务结构（多目标、多字段、大规模训练集等）下显示价值。**用数据决定，不用直觉**。
