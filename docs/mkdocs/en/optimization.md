# Prompt Self-Optimization (AgentOptimizer)

`AgentOptimizer` is the **prompt self-optimization module** of tRPC-Agent-Python: it transforms the iterative process of prompt engineering—failure case analysis, rewriting, regression validation, version management—into a reproducible automated pipeline, freeing engineers from manual trial and error.

> **The scope of "prompt" here**: In agent applications, "prompt" refers not only to the narrow system prompt, but also to all natural language assets that drive agent behavior—skill descriptions, rule specifications, sub-agent coordination instructions, tool usage instructions, etc. Their essence is natural language text interpreted by LLMs; as long as they influence agent decisions, they can be optimization targets for `AgentOptimizer`.

The module consists of four sub-modules, driven externally through a single entry point `AgentOptimizer.optimize`:

| Sub-module | Responsibility |
|---|---|
| **Optimization Algorithm** | Reflection-evaluation-retention loop; currently built-in [GEPA](https://github.com/gepa-ai/gepa) (Genetic-Evolutionary Pareto, MIT License), extensible to other algorithms via `OPTIMIZER_REGISTRY` |
| **Evaluation Bridge** | Reuses `AgentEvaluator`, allowing the optimization process to share the same `EvalSet` and metric configuration with daily regression |
| **Prompt Management** | `TargetPrompt` unifies prompt field read/write; supports two sources: local files (path) and arbitrary backends (callback) |
| **Runtime Orchestration** | Resource scheduling, stoppers, atomic artifact persistence, SIGINT signal safety |

`AgentOptimizer` redefines "prompt tuning" as an engineering problem that is **bounded, reproducible, and auditable**:

| Dimension | Expression |
|---|---|
| Optimization Objective | `evaluate.metrics[]` — a set of numerical, repeatable evaluation metrics |
| Decision Variables | Prompt fields registered with `TargetPrompt` (one or more) |
| Search Process | Reflection-evaluation-retention loop driven by reflection LM (see [§5](#5-how-gepa-works) for details) |
| Termination Conditions | 6 built-in stoppers + user-defined stoppers (see [§4.7](#47) for details) |
| Artifacts | `OptimizeResult` object + `runs/<timestamp>/` full audit directory (see [§8](#8-artifacts-and-directory-conventions) for details) |

> **Prerequisite Reading**: [Agent Evaluation](evaluation.md) — Optimization is built on top of evaluation; this document assumes the reader understands the basic concepts of `EvalSet` and `metric`.

---

## 1 What Is This / What Problem Does It Solve

### 1.1 Problems Solved

After agent applications enter business-critical paths, prompts (including all natural language text that drives agent behavior such as skills, rules, etc.) are among the most expensive assets to iterate: manual tuning relies on engineers' ability to summarize failure cases, and regression risks amplify rapidly after scaling; coupling between prompt fields on multi-sub-agent chains makes single-field optimization meaningless; model upgrades, tool changes, and scenario expansion all cause "yesterday's optimal" prompts to fail today.

The `AgentOptimizer` module completely **engineers this iterative process**:

- **Explicit optimization objectives** — crystallizes "what counts as good" into a numerical contract of metric + threshold, shareable across evaluation, optimization, and CI/CD
- **Algorithmic search process** — reflection-evaluation-retention loop replaces manual trial and error; process is replayable, results are comparable
- **Multi-prompt joint optimization** — supports simultaneous optimization of multiple fields (e.g., router + worker + summarizer instructions, CLAUDE.md + SKILL.md), and uses GEPA's merge mechanism for cross-field search
- **Auditable runtime process** — each round's reflection input, candidate changes, evaluation scores, acceptance/rejection reasons are all persisted to `runs/<timestamp>/`, supporting post-hoc traceability
- **Controllable and rollbackable results** — `update_source` determines whether to write back to source prompts; `TargetPrompt` provides atomic writes and failure rollback; half-written disk writes or secondary SIGINT interrupts will not corrupt source files

### 1.2 Relationship with the Evaluation Module

`AgentEvaluator` and `AgentOptimizer` constitute the two ends of the **evaluation-optimization closed loop**:

| Module | Role | Output |
|---|---|---|
| `AgentEvaluator` ([evaluation.md](evaluation.md)) | Measures current prompt quality | Pass/fail per case + each metric score |
| `AgentOptimizer` (this document) | Searches for better prompts based on measurement results | Optimal prompt + full optimization history |

The two share the same `EvalSet`, the same metric configuration, and the same `call_agent`. One set of assets supports both daily regression (pytest running `AgentEvaluator`) and periodic optimization (night window running `AgentOptimizer`, see [§4.6 CI Closed Loop](#46)).

### 1.3 Applicable Boundaries

The effectiveness of `AgentOptimizer` depends on three prerequisites:

1. **Evaluation signals are sufficiently stable**. When the variance of the scoring itself is greater than the improvement brought by prompt rewriting, the optimization direction is unreliable. It is recommended to first run `AgentEvaluator` with `num_runs=3` to observe metric cross-run consistency before starting optimization.
2. **Budget matches the search space**. A typical small-scale optimization is on the order of `max_metric_calls=30~60` (one case-level evaluation counts as one metric_call), 5~20 reflection LM calls, running 1~10 minutes, consuming tens to hundreds of dollars (see [§6 Cost and Concurrency](#6-cost-and-concurrency) for details). When the budget is significantly lower than this level, you should first complete baseline tuning on `AgentEvaluator`.
3. **Prompt has optimizable semantic structure**. Prompts with fewer than 20 characters hardcoded or used only for placeholder concatenation have too narrow a search space; GEPA reflection degenerates into synonym rewriting in this scenario.

For scenarios not within the above prerequisites, you should prioritize using [`AgentEvaluator`](evaluation.md) for continuous observation rather than starting optimization.

## 2 5-Minute Quickstart

Complete code and data: [`examples/optimization/quickstart/`](../../../examples/optimization/quickstart/).

### 2.1 Example Task

The agent in this example is an **elementary school arithmetic word problem solver**: it receives arithmetic problems described in natural language (e.g., "Xiao Ming bought 4 apples in the morning and 7 more apples in the afternoon. How many apples does he have in total?"), and outputs a numerical answer with units (e.g., "Answer: 11 apples").

The agent behavior is driven by two prompt files together, which are the optimization targets for this session:

| Optimization Target | Path | Role in Agent |
|---|---|---|
| **system_prompt** | `agent/prompts/system.md` | Role and response style definition (e.g., "You are a math teaching assistant, answer in clear Chinese") |
| **skill** | `agent/prompts/skill.md` | Problem-solving methodology (e.g., "First identify the problem type → set up equation → calculate → write answer with units") |

Evaluation scores from two dimensions simultaneously, both must pass for the agent to pass:

| Evaluation Metric | Type | Threshold | Scoring Method |
|---|---|---|---|
| `final_response_avg_score` | Text matching | 1.0 | Agent output must **contain** the reference text (e.g., "Answer: 11 apples"), case-insensitive |
| `llm_rubric_response` | LLM judge | 0.66 | Independent LLM scores according to three rubrics and takes the mean: ① answer value matches reference ② reasoning steps are clear ③ answer has correct units |

Dataset size: training set 5 cases, validation set 3 cases.

### 2.2 Prepare Environment

```bash
pip install "trpc-agent-py[optimize]"

export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
```

The `[optimize]` extra includes `gepa` (reflection algorithm implementation) and `rich` (terminal progress panel).

### 2.3 Directory Structure

```text
examples/optimization/quickstart/
├── agent/
│   ├── agent.py              # Defines create_agent() factory function
│   ├── config.py             # Model / credentials read from environment variables
│   └── prompts/
│       ├── system.md         # Baseline system prompt (to be optimized)
│       └── skill.md         # Baseline skill document (to be optimized)
├── train.evalset.json        # 5 training cases (source of reflection minibatch)
├── val.evalset.json          # 3 validation cases (full evaluation each round, decides whether candidate is accepted)
├── optimizer.json            # Algorithm + metric configuration
└── run_optimization.py       # Entry script
```

> Training and validation sets must be different files; the framework validates at startup that paths do not overlap.

### 2.4 Core Code

`run_optimization.py` consists of three segments, corresponding to the three core abstractions exposed by the optimizer.

**Segment 1: `call_agent` — Business Bridge Function** (see [§3.4](#34-call_agent) for details)

The signature is fixed as `async def(query: str) -> str`. The framework drives the agent to complete single inference through it; agents of any form (`LlmAgent`, HTTP service, subprocess CLI, etc.) are all accessed through this layer of bridging.

```python
async def call_agent(query: str) -> str:
    # Re-read prompt files each time → GEPA writes new candidates and they take effect immediately
    root_agent = create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent,
                    session_service=session_service)
    # ... send user_content, collect is_final_response events
    return final_text.strip()
```

**Segment 2: `TargetPrompt` — Optimization Target Declaration** (see [§3.3](#33-targetprompt) for details)

Registers which prompt fields will be read/written by the optimizer. Each field corresponds to a local file (`add_path`) or a pair of async read/write callbacks (`add_callback`, used for arbitrary backends like remote KV).

```python
target = (
    TargetPrompt()
    .add_path("system_prompt", str(SYSTEM_PROMPT_PATH))
    .add_path("skill",         str(SKILL_PATH))
)
```

**Segment 3: `AgentOptimizer.optimize` — Optimizer Invocation** (full parameters see [§7.1](#71-agentoptimizeroptimize-parameter-table))

```python
await AgentOptimizer.optimize(
    config_path=str(CONFIG_PATH),
    call_agent=call_agent,
    target_prompt=target,
    train_dataset_path=str(TRAIN_PATH),
    validation_dataset_path=str(VAL_PATH),
    output_dir=str(RUNS_DIR / timestamp),
    update_source=False,
    verbose=1,
)
```

| Parameter | Description |
|---|---|
| `config_path` | `optimizer.json`, defines metric / algorithm / stop conditions |
| `output_dir` | Artifact directory; created automatically if it doesn't exist, recommended to use timestamp subdirectory |
| `update_source` | `False` only produces `best_prompts/`; `True` writes back to source files after successful optimization (CI scenario, see [§4.6](#46)) |
| `verbose` | `0` silent / `1` Rich progress panel / `2` plus gepa diagnostic logs |

### 2.5 Configuration File `optimizer.json`

The configuration is divided into two sections: `evaluate` (evaluation, same source as the evaluation module) + `optimize` (optimizer-specific).

```json
{
  "evaluate": {
    "metrics": [
      {
        "metric_name": "final_response_avg_score",
        "threshold": 1.0,
        "criterion": {
          "final_response": {"text": {"match": "contains", "case_insensitive": true}}
        }
      },
      {
        "metric_name": "llm_rubric_response",
        "threshold": 0.66,
        "criterion": {
          "llm_judge": {
            "judge_model": {"model_name": "...", "base_url": "...", "api_key": "..."},
            "rubrics": [
              {"id": "numeric_correct", "content": {"text": "Answer value matches reference"}, "type": "FINAL_RESPONSE_QUALITY"},
              {"id": "reasoning_clear", "content": {"text": "Reasoning steps are clear"},      "type": "FINAL_RESPONSE_QUALITY"},
              {"id": "units_present",   "content": {"text": "Answer has correct units"},    "type": "FINAL_RESPONSE_QUALITY"}
            ]
          }
        }
      }
    ],
    "num_runs": 1
  },
  "optimize": {
    "eval_case_parallelism": 2,
    "stop": {"required_metrics": "all"},
    "algorithm": {
      "name": "gepa_reflective",
      "seed": 42,
      "reflection_lm": {"model_name": "...", "base_url": "...", "api_key": "..."},
      "candidate_selection_strategy": "pareto",
      "module_selector": "round_robin",
      "reflection_minibatch_size": 3,
      "skip_perfect_score": false,
      "max_metric_calls": 60,
      "max_iterations_without_improvement": 8
    }
  }
}
```

Key concepts used in this example:

| Concept | Location in Config | One-Line Explanation | See Also |
|---|---|---|---|
| **metric** | `evaluate.metrics[]` | List of evaluation metrics; multiple can be stacked, each scored independently | [§4.5](#45) |
| **LLM judge** | `criterion.llm_judge` | LLM judge that scores according to rubrics; serves `llm_rubric_response` in this example | [§4.5](#45) |
| **stop.required_metrics** | `optimize.stop.required_metrics` | Framework-level stop: which metrics must all reach threshold before stopping | [§7.3.5](#735-optimizestop-section) |
| **reflection_lm** | `optimize.algorithm.reflection_lm` | Reflection LLM that reviews failed cases each round and generates new candidate prompts | [§3.8](#38-reflection-lm) / [§6.5](#65-reflection-lm-selection-suggestions-table) |
| **candidate_selection_strategy** | `optimize.algorithm` | Which candidate to pick as reflection parent each round | [§7.3.3](#733-optimizealgorithm-section) |
| **module_selector** | `optimize.algorithm` | Which field to rewrite each round in multi-field optimization | [§4.3](#43) |
| **reflection_minibatch_size** | `optimize.algorithm` | How many cases to sample from train each round for reflection | [§5](#5-how-gepa-works) |
| **stopper** | `optimize.algorithm.max_*` / `timeout_seconds` / `score_threshold` | Algorithm-level stop conditions, at least one must be set | [§4.7](#47) / [§7.3.3](#733-optimizealgorithm-section) |

See [§7.3](#73-optimizerjson-configuration-items-table) for the complete field reference.

### 2.6 Run

```bash
python examples/optimization/quickstart/run_optimization.py
```

The terminal outputs in order: baseline evaluation scores → acceptance/rejection records for each round's reflection → final summary. Completes in 1~3 minutes under small-scale configuration.

![Quickstart Terminal Output Example](../assets/imgs/optimization_quickstart.png)

```text
runs/<timestamp>/
├── result.json              # Complete run record (OptimizeResult serialized)
├── summary.txt              # Human-readable overview (read this first)
├── run.log                  # Single-line status
├── config.snapshot.json     # Snapshot copy of input configuration
├── rounds/round_NNN.json    # Each round's RoundRecord
├── baseline_prompts/<field>.md   # Pre-optimization snapshot
└── best_prompts/<field>.md       # Best candidate after optimization (only if SUCCEEDED)
```

Key lines in `summary.txt`:

```text
Optimization complete  | status=SUCCEEDED | algorithm=gepa_reflective
pass_rate     : 0.5000 -> 0.8500   (+0.3500, improved)
rounds        : 3 accepted / 7 total
duration      : 124.31s
stop_reason   : required_metrics_passing
update_source : false
```

> **What is pass_rate?**
>
> pass_rate measures: **what proportion of cases your agent "got right" on the validation set**.
>
> ---
>
> **Step 1: Each metric independently determines pass/fail**
>
> Each metric has its own threshold. Score ≥ threshold means pass; otherwise fail.
>
> **Step 2: A case passes only when ALL metrics pass**
>
> Think of it like an exam with multiple subjects — you must pass every subject to pass overall. Failing any single subject means the whole case fails.
>
> **Step 3: pass_rate = number of passing cases ÷ total cases**
>
> ---
>
> **Walkthrough example**: Suppose the validation set has 4 cases, with 3 metrics configured:
>
> | | metric_A (threshold 0.8) | metric_B (threshold 0.6) | metric_C (threshold 1.0) | Does this case pass? |
> | --- | --- | --- | --- | --- |
> | case_1 | score 0.9 ✅ | score 0.7 ✅ | score 1.0 ✅ | **Pass** (all 3 met) |
> | case_2 | score 0.85 ✅ | score 0.4 ❌ | score 1.0 ✅ | **Fail** (metric_B not met) |
> | case_3 | score 0.6 ❌ | score 0.8 ✅ | score 0.0 ❌ | **Fail** (metric_A & C not met) |
> | case_4 | score 0.95 ✅ | score 0.9 ✅ | score 1.0 ✅ | **Pass** (all 3 met) |
>
> 2 passed out of 4 total:
>
> ```
> pass_rate = 2 / 4 = 0.5
> ```
>
> ---
>
> **Back to the summary.txt above**:
>
> ```
> pass_rate : 0.5000 -> 0.8500   (+0.3500, improved)
> ```
>
> This means: before optimization the agent could only get half the cases right; after optimization it gets 85% right. An improvement of 35 percentage points.
>
> **Three related fields**:
>
> | Field | Meaning |
> | --- | --- |
> | `baseline_pass_rate` | Pass rate before optimization (scored with the initial prompt) |
> | `best_pass_rate` | Highest pass rate found during optimization |
> | `pass_rate_improvement` | `best - baseline`, the improvement gained from this optimization run |

See [§8 Artifacts and Directory Conventions](#8-artifacts-and-directory-conventions) for the complete meaning of each field.

### 2.7 Next Steps

| Your Next Question | Jump to Section |
|---|---|
| What exactly are these API concepts? | [§3 Core Concepts](#3-core-concepts) |
| My agent isn't this kind of local LlmAgent, how do I integrate? | [§4 Your Scenario → How to Integrate](#4-your-scenario--how-to-integrate) |
| What exactly does each step of the reflection-evaluation-retention loop do? | [§5 How GEPA Works](#5-how-gepa-works) |
| Want to estimate LLM call costs / adjust concurrency parameters? | [§6 Cost and Concurrency](#6-cost-and-concurrency) |
| Want to directly look up parameters / configuration items? | [§7 Complete API Reference](#7-complete-api-reference) |

## 3 Core Concepts

> This section uses 8 concepts to establish a "mental model" of the optimization module. Each concept starts from "what does it correspond to in your work" rather than from type signatures. The introduction order is consistent with the appearance order of the three code segments in [§2.4 Core Code](#24-core-code).

### 3.1 Module Overall Data Flow

The optimization module's work loop: the user inputs 4 types of assets, and the module produces 2 types of results in the reflection-evaluation-retention loop.

```text
                             +---> Evaluate candidate
                             |         |
 call_agent       ---+       |         v
                     |       |    Reflect on failures
 optimizer.json   ---+       |         |
                     |       |         v              ---> OptimizeResult
                     +------>|    Write new candidate      + runs/<ts>/
 TargetPrompt     ---+       |         |
                     |       |         v
 EvalSet x 2      ---+       |    Accept new best?
                             |     Y:keep / N:drop
                             |         |
                             +---------+
```

Roles of the four inputs:

| Input | Form | Role in the Loop |
| --- | --- | --- |
| `call_agent` | `async (str) -> str` | Passes query to business agent; optimizer samples behavior through this |
| `optimizer.json` | JSON configuration | Defines evaluation metrics (`evaluate.metrics`) and algorithm parameters (`optimize.algorithm`) |
| `TargetPrompt` | Multi-field prompt registration table | Declares which prompt files / remote configuration entries are optimization targets |
| `EvalSet × 2` | Two evalsets | Training set for reflection LM to see failure cases, validation set for scoring / early stop determination |

Destinations of the two outputs:

| Output | Form | Typical Use |
| --- | --- | --- |
| `OptimizeResult` | In-memory object returned by `optimize()` | Programmatic reading (baseline / best / each round details) |
| `runs/<timestamp>/` | Audit directory | Manual review, CI parsing, re-run (see [§8](#8-artifacts-and-directory-conventions) for details) |

### 3.2 call_agent

**One sentence**: The "universal plug" for your business agent.

**Why needed**: Your agent might be a local `LlmAgent`, might be a deployed HTTP service, might be a black-box CLI like `claude` / `codex`. The module cannot write adapters for every form; you only need to wrap "given a query → get the agent's final response" into an async function, and the module drives the agent to run evaluations through it.

**How to use**:

```python
async def call_agent(query: str) -> str:
    # Your implementation: call local agent / HTTP service / subprocess CLI, all fine
    # Key point: re-read prompt files each time (so GEPA's new candidates take effect immediately)
    root_agent = create_agent()
    runner = Runner(...)
    return await run_and_collect_final_response(runner, query)
```

The signature is fixed as `async (str) -> str`, cannot have more parameters nor be synchronous.

**When the framework calls it**:

| Timing | Frequency |
|---|---|
| Baseline evaluation | Each val case × `num_runs` |
| Each round's minibatch evaluation | Each sampled case 1 time |
| Each round's candidate validation set evaluation | Each val case × `num_runs` |

### 3.3 TargetPrompt

**One sentence**: Tells the module "which prompt files are to be optimized", equivalent to an **optimization target registration table**.

**Why needed**: In agent projects, prompts are usually scattered across multiple files or even multiple backends (system.md / skill.md / also placed in QCS versions); the module needs to know: **when a new candidate is reflected, where should it be written, and where should it read from when reading baseline**. `TargetPrompt` is this "address book".

**How to use**:

```python
from trpc_agent_sdk.evaluation import TargetPrompt

target = (
    TargetPrompt()
    .add_path("system_prompt", "agent/prompts/system.md")    # File type
    .add_path("skill",         "agent/prompts/skill.md")     # File type
    .add_callback("rule",                                    # Callback type (remote KV)
                  read=load_rule_from_kv,
                  write=save_rule_to_kv)
)
```

Each field `name` (e.g., `"system_prompt"`) will become, after optimization ends:

- `result.best_prompts["system_prompt"]` — programmatic reading of optimal prompt
- `runs/<timestamp>/best_prompts/system_prompt.md` — human reading of optimal prompt
- Elements in `RoundRecord.optimized_field_names` — see which field was changed each round

**Two types of sources**:

| Source | Applicable When | What the Framework Does |
|---|---|---|
| `add_path(name, path)` | Prompt is in local file | Write to disk using tmp + `os.replace` atomic write; multi-field failure rolls back source files |
| `add_callback(name, *, read, write)` | Prompt is in remote configuration center / database / git, etc., any backend | Calls your `read` / `write` async functions; atomicity is guaranteed by you |

See [§7.2](#72-targetprompt-api-table) for the complete API.

### 3.4 AgentOptimizer

**One sentence**: The module's "power button".

**Why needed**: You wouldn't want to manually write the whole process of "read config → validate inputs → run reflection loop → persist to disk → assemble result"; `AgentOptimizer` encapsulates this process into one call—you give it **inputs**, it returns **results**.

**How to use**:

```python
from trpc_agent_sdk.evaluation import AgentOptimizer

result = await AgentOptimizer.optimize(
    config_path="optimizer.json",
    call_agent=call_agent,
    target_prompt=target,
    train_dataset_path="train.evalset.json",
    validation_dataset_path="val.evalset.json",
    output_dir="runs/2026-05-19T17-00-00",
)
print(result.best_pass_rate)
```

This module has only this one public entry point, **no other way to start optimization**.

**What it does**:

1. Loads and validates `optimizer.json` (throws error before running if schema is wrong)
2. Validates `call_agent` is async function / `target_prompt` has at least one registered field / training set ≠ validation set
3. Runs reflection-evaluation-retention loop
4. Persists artifacts to `output_dir/`
5. Returns an `OptimizeResult` object

`optimize` has 11 keyword-only parameters in total; the 6 commonly used ones are in [§2.4](#24-core-code), all parameters see [§7.1](#71-agentoptimizeroptimize-parameter-table).

**`update_source` decision table** (key parameter shared by all §4.x scenarios): Determines whether to **write back** the optimal candidate to the source prompt files registered in `TargetPrompt` after successful optimization—

| `update_source` | What to do after success | Effective Path | Applicable Scenario |
|---|---|---|---|
| `False` (default) | Only write the optimal candidate to `output_dir/best_prompts/` | You **manually** review → copy to online prompt file → takes effect on next call | Grayscale deployment, requires manual review, don't want optimizer to directly modify online files |
| `True` | Directly **overwrite** source prompt files with the optimal candidate | Business next call **immediately** uses the new prompt | Automated closed loop (e.g., night optimization task, see [§4.6 CI Closed Loop](#46)) |

Regardless of which you choose, the business side requires **zero restart, zero code changes**—the way to perceive prompt changes is always "re-read file on next call".

> Safety guarantee of `update_source=True`: Overwrite uses tmp + `os.replace` atomic write; if optimization is interrupted midway or by SIGINT, the source prompt file **will not be half-written**, preserving original content (see [§8.3 Atomic Disk Persistence](#83-atomic-disk-persistence-guarantee) for details).

### 3.5 optimizer.json

**One sentence**: A configuration file that tells the module "what counts as good" and "how to search".

**Why needed**: Metric thresholds, minibatch size, reflection LM configuration, stop conditions... if these parameters are scattered in code, you need to modify code every time you run an experiment. After centralizing to one JSON file, tuning parameters = modify JSON, and reproducibility is also better (a copy of `config.snapshot.json` will be saved in the artifacts).

**What it looks like**: [§2.5](#25-configuration-file-optimizerjson) already showed the complete example. Structurally divided into two sections:

```text
{
  "evaluate": { ... },        # Same schema as AgentEvaluator: metric list + num_runs
  "optimize": {
    "eval_case_parallelism": 2,
    "stop": {                 # Framework-level stop: which metrics must reach threshold
      "required_metrics": "all"
    },
    "algorithm": {            # Algorithm-specific: reflection_lm / minibatch / 6 types of stoppers
      "name": "gepa_reflective",
      ...
    }
  }
}
```

**Division of labor between the two sections**:

- `evaluate` section: **completely reuses** the evaluation module's schema. Metric configurations you wrote for evaluation projects can be directly copied over
- `optimize` section: **optimizer-specific**. Among them, `algorithm.name` is the algorithm selector; currently the only optional value is `"gepa_reflective"`, will be extended by [§9.2 Registering New Algorithms](#92) when new algorithms are added in the future

See [§7.3](#73-optimizerjson-configuration-items-table) for the complete field table.

### 3.6 EvalSet / EvalCase

**One sentence**: Training set + validation set, format identical to the evaluation module.

**Why need two separate files**:

- **Training set**: The module randomly **samples** a few cases from it each round (`reflection_minibatch_size`, default lets gepa decide) for the reflection LM to see failure cases → used to "find improvement directions"
- **Validation set**: After each new candidate is generated, **run fully** on it for scoring → used to "verify whether the candidate is actually better"

**Why must they be different files**: The training set determines what the reflection LM sees, the validation set determines whether a candidate is accepted. If the two overlap, it becomes "using exam questions for practice, then using exam questions for grading"—the resulting best_pass_rate is not credible. The framework validates at startup by comparing paths (`os.path.normpath(os.path.abspath(...))`) to defend against this, and directly throws `ValueError` if they overlap.

See [Evaluation Set Writing Guide](evaluation.md#evaluation-set-evalset-writing-guide) for format and writing guidelines.

### 3.7 OptimizeResult

**One sentence**: The "complete output" after one optimization run, both the return value of `optimize()` and the content of `runs/<timestamp>/result.json`.

**Why needed**: After running optimization, you care most about three things—success or not / how much improvement / what is the optimal prompt. `OptimizeResult` packages them:

```python
result = await AgentOptimizer.optimize(...)

# 1. Success or not
if result.status == "SUCCEEDED":
    ...

# 2. How much improvement
print(f"{result.baseline_pass_rate:.2%} → {result.best_pass_rate:.2%}, "
      f"+{result.pass_rate_improvement:.2%}")

# 3. What is the optimal prompt
new_system_prompt = result.best_prompts["system_prompt"]
new_skill         = result.best_prompts["skill"]
```

It also carries process data (what happened each round, reflection LM call count, total duration, etc.) for post-hoc analysis.

**The 6 most frequently viewed fields**:

| Field | Type | Meaning |
|---|---|---|
| `status` | `"SUCCEEDED"` / `"FAILED"` / `"CANCELED"` | Final state |
| `baseline_pass_rate` / `best_pass_rate` | `float` | Pass rate before / after optimization |
| `pass_rate_improvement` | `float` | Difference between the two |
| `best_prompts` | `dict[str, str]` | Field name → optimal prompt text |
| `rounds` | `list[RoundRecord]` | Each round's record |
| `stop_reason` | `Literal[...]` or `None` | Which stopper triggered the stop |

See [§7.4](#74-optimizeresult--roundrecord-field-table) for all 22 fields (including `RoundRecord`).

### 3.8 Reflection LM

**One sentence**: The LLM used internally by the module, which receives a set of failure cases each round and outputs improved prompt candidates; it is a separate configuration from the business LM used by your agent.

Configured in the `optimizer.json::optimize.algorithm.reflection_lm` section, type is `OptimizeModelOptions`:

```json
"reflection_lm": {
  "model_name": "gpt-4o",
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-...",
  "generation_config": {"temperature": 0.6, "max_tokens": 4096}
}
```

See [§6.5](#65-reflection-lm-selection-suggestions-table) for model selection suggestions; see [§7.3.3](#733-optimizealgorithm-section) for complete fields.

## 4 Your Scenario → How to Integrate

| Your Situation | Section | Corresponding Example |
|---|---|---|
| Agent is an online HTTP service (FastAPI / Gin / self-developed interface) | [§4.1](#41) | `http_service` |
| Agent is a subprocess / command-line tool (`claude` / `codex` / internal CLI) | [§4.2](#42) | `blackbox_cli` |
| Agent is a multi-sub-agent chain (multiple sub-agents collaborate to complete one response), want to optimize each sub-agent's prompt simultaneously | [§4.3](#43) | `multi_agent_pipeline` |
| Prompts are not in local files, stored in remote KV / configuration center / database / Git, etc., any backend | [§4.4](#44) | `remote_prompt_store` |
| Single evaluation metric is insufficient, need to run multiple evaluation metrics simultaneously (e.g., answer accuracy + hallucination rate + style compliance rate) and fuse into a total score | [§4.5](#45) | `multi_metric_with_judges` |
| Want to integrate CI closed loop: run evaluation gate on PR, run optimization in night window and automatically write back new prompts | [§4.6](#46) | `ci_integration` |
| Optimization task has hard constraints (e.g., must complete within 1-hour window / cumulative calls not exceeding N / stop after consecutive no-improvement) | [§4.7](#47) | `slo_runtime_control` |
| Can already run through the basic process, want to further improve results (adjust GEPA candidate selection / Pareto frontier / cross-field fusion) | [§4.8](#48) | `advanced_strategies` |
| Other common extensions (connect Grafana / WandB, etc. for monitoring, custom stop strategy, use your own optimization algorithm) | [§4.9](#49) | (Multiple examples combined) |

### 4.1 My Agent is an HTTP Service, How to Integrate? {#41}

**Your situation**: The business agent is already online as an independent service (FastAPI / Gin / self-developed framework are all acceptable), hoping to perform automatic optimization on its prompts—but the service runs long-term and cannot stop, service implementation details are a black box to the optimizer, and prompts are usually injected in file form.

**Integration model**: The optimizer accesses as a **pure client**, with only **one coupling point** with the service process—the prompt files on disk.

```text
+-------------------+       HTTP request + query         +-------------------+
|  AgentOptimizer   |  --------------------------------> |   HTTP agent      |
|   (optimizer)     |  <--------- text response -------- |  (no code change) |
+---------+---------+                                    +---------+---------+
          |                                                        ^
          | write new prompt candidate                             | Each request
          v                                                        | re-reads prompt
       +------------------------------------------------------------+
       |              prompt files  (on disk)                        |
       +------------------------------------------------------------+
```

The service process **does not need any code changes**, only needs to satisfy one convention: **re-read prompt files before processing each request**—so that the new candidate written by the optimizer takes effect on the next request.

**Integration in 3 steps**:

**Step 1: Register `TargetPrompt` on the prompt files read by the HTTP service**

```python
target = TargetPrompt().add_path("system_prompt", "service/prompts/system.md")
```

The second parameter of `add_path` must be **the exact file path that the service process actually reads** (not an arbitrary copy), otherwise the new candidate written by the optimizer will not be perceived by the service.

**Step 2: Write `call_agent` as an HTTP client to the service**

```python
async def call_agent(query: str) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post("http://my-agent-service/chat",
                                 json={"query": query})
        resp.raise_for_status()
        return resp.json()["final_text"]
```

Modify the `json=...` field according to the actual interface payload schema of the business; adjust `timeout` according to the business's first inference latency (example default 120s).

**Step 3: Call `AgentOptimizer.optimize`**

```python
await AgentOptimizer.optimize(
    config_path="optimizer.json",
    call_agent=call_agent,
    target_prompt=target,
    train_dataset_path="train.evalset.json",
    validation_dataset_path="val.evalset.json",
    output_dir=f"runs/{timestamp}",
    update_source=False,    # Decision table see [§3.4](#34-agentoptimizer)
)
```

**Pre-integration checklist**:

| Check Item | Description |
|---|---|
| Does the service re-read prompt files on each request? | No → New candidates written by optimizer won't be seen by the service, optimization is ineffective. Need to add re-read logic in the handler |
| Does the optimizer process have write permission to prompt files? | No → Optimizer cannot persist new candidates |
| Are the prompt file paths seen by the service and the optimizer consistent? | Especially need to confirm in containerized deployment (mount path / symlink) |
| What is the service's 5xx behavior? | The service should not silently retry internally—this would mask the real failure rate, letting the optimizer see a false "high score" |

**→ Complete example**: [`examples/optimization/http_service/`](../../../examples/optimization/http_service/)
- `service/server.py` — Demonstrates FastAPI service with prompt hot-loading (`/chat` rebuilds agent and re-reads `system.md` each time), can be used as a reference for business service transformation
- `run_optimization.py` — Client optimizer entry, includes pre-start service health check (fail-fast)

### 4.2 My Agent is an External Command-Line Tool (CLI), Optimizer Cannot Get Its Code {#42}

**Your situation**: The business agent is an external executable program—`claude` / `codex` / self-developed CLI, etc. Its source code, internally used LLM client, and runtime language are **completely black boxes** to the optimizer, but it reads several prompt files from a working directory at startup (typically `CLAUDE.md` + `.claude/skills/<name>/SKILL.md`). You hope to optimize these prompt files without modifying the CLI code or binding to any of its internal dependencies.

**Integration model**: The optimizer calls the CLI through **subprocess**, and the **only coupling point** with the CLI is still the prompt files on disk—this is the same structure as §4.1's HTTP service, the difference is only replacing "HTTP request" with "starting a subprocess".

```text
+-------------------+    start subprocess + pass query   +-------------------+
|  AgentOptimizer   |  --------------------------------> |   External CLI    |
|   (optimizer)     |  <--------- stdout text ---------- |  (no code change) |
+---------+---------+                                    +---------+---------+
          |                                                        ^
          | write new prompt candidate                             | Each startup
          v                                                        | auto-reads
       +------------------------------------------------------------+
       |              prompt files  (on disk)                        |
       +------------------------------------------------------------+
```

The CLI binary itself **does not need any modifications**, only needs to satisfy: **it loads prompt files from the specified directory on each startup** (most CLI tools are designed this way).

**Integration in 3 steps**:

**Step 1: Register `TargetPrompt` on the prompt files read by the CLI (use `add_path` multiple times for multiple files)**

```python
target = (
    TargetPrompt()
    .add_path("claude_md", "workspace/CLAUDE.md")
    .add_path("skill_md",  "workspace/.claude/skills/city-info/SKILL.md")
)
```

Each `add_path` registers one independent field; GEPA treats each field as an independently optimizable module, can optimize separately/jointly (see §3.7, §4.3 for details).

**Step 2: Wrap subprocess call + stdout normalization into `call_agent`**

```python
async def call_agent(query: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "trpc-claudecode", "--print",
        "--add-dir", str(WORKSPACE_DIR),       # CLI loads prompt files from here
        "--dangerously-skip-permissions",
        query,                                  # Pass query as argv, avoid shell escaping
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_build_cli_env(),                   # Environment variables expected by business's own CLI
    )
    stdout_b, stderr_b = await asyncio.wait_for(
        proc.communicate(), timeout=90.0,        # Prevent single CLI from hanging
    )
    if proc.returncode != 0:
        raise RuntimeError(f"CLI exited {proc.returncode}: {stderr_b[:400]!r}")
    return _normalize_response(stdout_b.decode("utf-8", "replace"))
```

`call_agent` still has the standard signature `async (query: str) -> str` from §3.1; to the optimizer main loop, this `call_agent` is no different from "calling local LLM". `_build_cli_env` / `_normalize_response` are helper functions implemented by the business according to their CLI's characteristics (the former modifies/supplements environment variables to the form expected by the CLI, the latter normalizes CLI stdout into a stable string comparable for evaluation)—this framework does not prescribe their form, implement as needed.

**Step 3: Run once to confirm baseline works, then hand over to GEPA reflection optimization**

```python
await AgentOptimizer.optimize(
    config_path="optimizer.json",
    call_agent=call_agent,
    target_prompt=target,
    train_dataset_path="train.evalset.json",
    validation_dataset_path="val.evalset.json",
    output_dir="runs/<timestamp>/",
    update_source=False,
)
```

**Pre-integration checklist**:

| Check Item | Consequence of Failure |
| --- | --- |
| Does the CLI re-read prompt files on each startup? | No → New candidates written by optimizer won't take effect; evaluation between candidates is equivalent to running the same baseline |
| Does the CLI support passing query through argv / stdin / `--query xxx`? | No → Integration is not feasible (need to add this entry point to CLI first) |
| Is the CLI's average single-run latency known? | No → Cannot reasonably set `CLI_TIMEOUT_SEC` and `max_metric_calls` |
| Does the CLI process pollute shared disk state (other than prompt files)? | Yes → Evaluation is not reproducible; need `eval_case_parallelism=1` or independent workspace for each case |

**→ Complete example**: [`examples/optimization/blackbox_cli/`](../../../examples/optimization/blackbox_cli/)
- `agent/call_agent.py` — Subprocess call + environment variable adaptation + stdout normalization engineering implementation, can be used as a starting point for integrating your own CLI
- `run_optimization.py` — Standard entry for dual-field (`CLAUDE.md` + `SKILL.md`) `TargetPrompt`

### 4.3 My Agent is a Multi-Sub-Agent Chain, Want to Optimize Each Sub-Agent's Prompt Simultaneously {#43}

**Your situation**: The business side has already orchestrated a multi-sub-agent collaboration chain. Each sub-agent has its own system prompt, and there are implicit contracts between fields (the output form of upstream sub-agent must match downstream expectations). Common symptoms during manual iteration are **"fixing A shows effect, but drags down B"**. You hope to **jointly optimize** prompts for all sub-agents, so that end-to-end metrics improve.

**Integration model**: Register each sub-agent's prompt file as an **independent field** of `TargetPrompt`—GEPA treats each field as an independently optimizable module (component), selects 1 or more fields to write back each round according to `module_selector`, and the optimizer only looks at the end-to-end metric score as feedback. The chain code requires **zero modifications**; each sub-agent just needs to re-read its own prompt file each time it is called.

```text
+-----------------------------+   select 1 field each round  +---------------------+
|      AgentOptimizer         |  --------------------------> |   prompt files      |
|  (multi-field TargetPrompt) |    write back new candidate  |  (each sub-agent    |
|                             |                              |   has 1 file)       |
+--------------+--------------+                              +----------+----------+
               ^                                                        |
               |  End-to-end metric score                               | Each call
               |                                                        | re-reads prompt
               |                                                        v
               |              +-----------------------------------------+
               +------------- |   call_agent(query)                     |
                              |     = Your multi-sub-agent chain        |
                              |     call entry                          |
                              |     (sub-agent A → sub-agent B → ...)   |
                              +-----------------------------------------+
```

**Integration in 3 steps**:

**Step 1: Register each sub-agent's prompt file as an independent field**

```python
target = (
    TargetPrompt()
    .add_path("agent_a", "<path-to-sub-agent-a-prompt>.md")
    .add_path("agent_b", "<path-to-sub-agent-b-prompt>.md")
    # ... one add_path per sub-agent
)
```

The key is the identifier of this field in reflection prompts / artifact filenames; it just needs to be readable by the business.

**Step 2: Wrap the entire chain call into `call_agent`, and ensure sub-agents re-read prompts each time**

```python
async def call_agent(query: str) -> str:
    return await invoke_pipeline(query)   # Your existing chain entry
```

Key constraint inside `invoke_pipeline`: **each sub-agent must re-read its own prompt file each time it is called**, otherwise new candidates written by the optimizer will not take effect.

**Step 3: Turn on multi-field related switches in `optimizer.json`**

```jsonc
{
  "optimize": {
    "algorithm": {
      "module_selector": "round_robin",   // Select 1 field per round in rotation, convenient for attribution
      "use_merge": true,                  // Actively fuse after accumulating several single-field improvements
      "max_merge_invocations": 3,
      "reflection_history_top_k": 3       // Recommended to increase when multi-field rotation (default 2)
    }
  }
}
```

See [§7 Complete API Reference](#7-complete-api-reference) for the complete semantics and value mappings of each parameter.

**Pre-integration checklist**:

| Check Item | Consequence of Failure |
| --- | --- |
| Does each sub-agent re-read its own prompt file each time it is called? | No → New candidates written by optimizer won't take effect; evaluation between candidates is equivalent to running the same baseline |
| Can end-to-end metrics reflect the joint quality of all fields? | No → Feedback signal seen by reflection LM is not real; recommend using `final_response_avg_score` to evaluate final response |
| How many LLM inferences does a single case go through? | Call volume multiplies by chain depth; need to correspondingly reduce `eval_case_parallelism` / `reflection_minibatch_size` to prevent rate limit |
| Do sub-agents need to be in the same process? | Not necessary—`call_agent` internals can be HTTP / gRPC / internal SDK / other orchestration frameworks; as long as it ultimately returns `str` |

**→ Complete example**: [`examples/optimization/multi_agent_pipeline/`](../../../examples/optimization/multi_agent_pipeline/)
- `pipeline/orchestrator.py` — Multi-sub-agent chain implementation, sub-agents re-read prompts on each call
- `run_optimization.py` — Standard entry for multi-field `TargetPrompt`
- `optimizer.json` — Recommended configuration for multi-field scenarios

### 4.4 My Prompts Are Not in Local Files, Stored in Remote Configuration Center / KV / Database {#44}

**Your situation**: Business prompts are not in local files, but placed in a remote configuration center (QCS / Apollo / Nacos / self-developed KV / database / Git, etc.), and the business fetches and uses them from the center. The optimizer cannot directly access the file system—it can only interact with the remote through the business's own SDK.

**Integration model**: `TargetPrompt` abstracts "where prompts are" into a pair of async functions `read` / `write`—the optimizer calls `read` to get the baseline snapshot, calls `write` to persist candidates; the remote backend form (KV / RPC / SQL / Git API ...) is **completely black box** to the optimizer. This is isomorphic to the structure coupled through local prompt files in §4.1 / §4.2, the difference is only replacing "read/write files" with "calling two async functions given by the business".

```text
+-------------------+         async read / write         +---------------------+
|  AgentOptimizer   |  <-------------------------------> |   Remote config     |
|   (optimizer)     |    (your own SDK / HTTP / RPC)     |  (KV / DB / Git ...)|
+---------+---------+                                    +---------+-----------+
          ^                                                       |
          | best_prompts/ persisted locally                       | Business calls
          |                                                       | pulls config
          v                                                       v
   +-------------------+                           +---------------------------+
   | output_dir/       |                           |  call_agent internals     |
   |  best_prompts/    |                           |  Pull latest prompt then  |
   +-------------------+                           |  call agent               |
                                                   +---------------------------+
```

**Integration in 3 steps**:

**Step 1: Implement a pair of async functions to operate remote prompts**

```python
async def read_prompt() -> str:
    return await your_config_sdk.get(key="system_prompt")

async def write_prompt(value: str) -> None:
    await your_config_sdk.put(key="system_prompt", value=value)
```

Signature constraints: `read: async () -> str`, `write: async (str) -> None`. Retry / idempotency / authentication are guaranteed by the business's own SDK.

**Step 2: Use `add_callback` instead of `add_path` to register `TargetPrompt`**

```python
target = TargetPrompt().add_callback(
    "system_prompt",
    read=read_prompt,
    write=write_prompt,
)
```

`add_callback` and `add_path` are peers on `TargetPrompt`—multi-field can also be mixed (some fields in local files, some fields in remote configuration center).

**Step 3: Write `call_agent` as "pull now, use now", call `optimize` as usual**

```python
async def call_agent(query: str) -> str:
    prompt_text = await read_prompt()        # Pull now, ensure candidate writes take effect immediately
    agent = create_agent(prompt_text)
    return await runner.run_async(query, ...)

await AgentOptimizer.optimize(
    config_path="optimizer.json",
    call_agent=call_agent,
    target_prompt=target,
    train_dataset_path="train.evalset.json",
    validation_dataset_path="val.evalset.json",
    output_dir="runs/<timestamp>/",
    update_source=False,                      # Decision table see §3.4
)
```

The value of `update_source` is determined by the business side's prompt write-back strategy (see §3.4 decision table for details), the framework has no additional restrictions on it.

**Pre-integration checklist**:

| Check Item | Consequence of Failure |
| --- | --- |
| Does the business side re-pull configuration on each call? | No → After optimizer writes new candidate, business cannot perceive it, reflection loop fails |
| Are both `read` / `write` async functions? | No → Error reported immediately when registering with `add_callback` |
| Is `write` idempotent (accepts repeated writes of the same value)? | No → May fail when automatically rolling back to baseline at finish, leaving remote contaminated |
| Does the optimizer process have write permission for this key / namespace? | No → `write` throws permission error, current candidate evaluation fails |

> **Safe mode involving production prompts** (adopt as needed, not forced by framework): If the business side already has sandbox / production namespace isolation, you can let the optimizer only read/write sandbox keys, cooperate with `update_source=False` to let the optimizer automatically roll back sandbox at finish, the best candidate is only persisted locally in `best_prompts/`, then synchronized to production through the business's own approval flow. `examples/optimization/remote_prompt_store/` demonstrates this workflow.

**→ Complete example**: [`examples/optimization/remote_prompt_store/`](../../../examples/optimization/remote_prompt_store/)
- `store/prompt_client.py` — `read` / `write` async function definitions, core transformation point for integrating business configuration center SDK
- `run_optimization.py` — Standard entry for `add_callback` registration (demonstrates workflow using sandbox + `update_source=False` + manual approval)

### 4.5 Single Evaluation Metric Is Insufficient, Need Multiple Metrics and Fuse into Total Score {#45}

**Your situation**: Business launch has requirements for agent output in more than one dimension—answer must be correct (correctness hard constraint) + must not talk nonsense (hallucination rate) + style must comply with specifications (format / tone) + must not contain sensitive words (compliance)... Single metric cannot contain all, forcibly using a single composite metric means the feedback signal seen by the reflection LM is a mixed scalar, making it difficult to attribute directionally.

**Integration model**: `optimizer.json`'s `evaluate.metrics` is a **list**—directly list multiple metrics, each scored independently, with independent threshold and independent configuration. Early stop determination declares which metrics must reach the threshold through `optimize.stop.required_metrics`; GEPA internally decides how to maintain the Pareto frontier among multiple metrics through `optimize.algorithm.frontier_type` to avoid "fixing A drags down B". The entire mechanism is purely configuration-driven—`call_agent` and `TargetPrompt` both do not need to change a single line of code for multi-metric.

**Configuration in 3 steps**:

**Step 1: List all metrics in `evaluate.metrics`**

```jsonc
{
  "evaluate": {
    "num_runs": 2,                            // Smooth LLM output variance (>1 lets each case run multiple times and take mean)
    "metrics": [
      {
        "metric_name": "llm_final_response",  // Hard constraint: is answer substantively equivalent to reference
        "threshold": 1.0,
        "criterion": { "...": "..." }         // Complete fields see §7 / example
      },
      {
        "metric_name": "llm_rubric_response", // Soft constraint: multiple rubrics (format / style / units ...)
        "threshold": 0.75,
        "criterion": { "...": "..." }
      }
    ]
  }
}
```

Each metric is scored independently and written independently to `metric_breakdown` in `result.json`, convenient for reverse-attributing which metric a certain evaluation lost points on.

**Step 2: Declare early stop gate in `optimize.stop.required_metrics`**

| Value | Semantics | Applicable Scenario |
| --- | --- | --- |
| `"all"` | Early stop only when all metrics reach threshold | All metrics are must-pass items |
| `["m1", "m2"]` | Early stop only when all metrics in the list reach threshold (other metrics still participate in evaluation but do not affect early stop) | Some metrics are reference observation items, not used as gates |
| `null` or `[]` | Does not participate in early stop, only controlled by algorithm-level budget / no-improvement / score_threshold | Just want to run out the budget and see results |

**Step 3: Adjust `frontier_type` to a value that correctly handles multiple metrics**

| Value | Meaning | Applicable |
| --- | --- | --- |
| `instance` | Maintain one best candidate per case | Single metric or no obvious conflict between metrics |
| `objective` | Maintain one best candidate per metric | Multiple metrics but small case count |
| `hybrid` | Maintain both case + metric two-layer frontier | **Real conflict scenario with multiple metrics** (recommended default) |
| `cartesian` | One best candidate per (case, metric) combination | Extremely complex / debugging use, candidate pool easily explodes |

`hybrid` lets GEPA not lose the best candidate on another metric when improving one metric—the **safe default for multi-metric business**. See [§7](#7-complete-api-reference) for the complete definition of each value.

**Pre-integration checklist**:

| Check Item | Consequence of Failure |
| --- | --- |
| Do the `threshold` values of each metric conform to business requirements? | No → Early stop determination is inaccurate; business-critical metrics may not have reached standard when optimization ends |
| Are only "hard constraints" listed in `stop.required_metrics`? | No → Soft constraint fluctuations will repeatedly interrupt early stop determination, wasting budget |
| Does `eval_case_parallelism` consider the concurrency of metric count × judge count? | No → Single-round LLM call volume explodes (N cases × M metrics × K judges × `num_runs`), easily hitting LLM backend rate limit |
| Is `num_runs` reasonable (default 1)? | Single LLM judge output has variance; recommend `num_runs=2` to let each case run twice and take mean to eliminate jitter |

**→ Complete example**: [`examples/optimization/multi_metric_with_judges/`](../../../examples/optimization/multi_metric_with_judges/)
- `optimizer.json` — Complete configuration example with `llm_final_response` (multi-judge `all_pass` voting) + `llm_rubric_response` (single judge multi-rubric) + `frontier_type=hybrid` + `stop.required_metrics` list style
- `run_optimization.py` — Standard entry consistent with single-metric scenarios (multi-metric does not affect entry code)

### 4.6 Want to Integrate CI Closed Loop: PR Gate + Night Optimization Auto Write-Back {#46}

**Your situation**: You hope prompt engineering also follows the CI/CD process—each PR automatically runs evaluation gate (score below threshold means CI red light, preventing degraded prompts from entering main branch), while simultaneously running reflection optimization in a low-peak window to write back better prompts, and the next PR automatically uses them. **Using either link alone is not enough**: pure gate will not automatically make prompts better, pure optimization has no quality gate.

**Integration model**: `AgentEvaluator.evaluate` (pytest runs PR gate) and `AgentOptimizer.optimize` (night optimization) share **the same set of assets**—the same `call_agent`, the same evalset (physically split into train / val two files to prevent leakage, logically one set of corpus), the same pair of prompt files. `update_source=True` is the key switch for the closed loop: after optimization succeeds (`OptimizeResult.status=SUCCEEDED`), the optimal candidate directly overwrites the source prompt files, and the next PR-triggered pytest automatically reads the new content.

```text
              +-----------------------------------------------------+
              |  Shared assets: call_agent + evalset + prompt files  |
              +------+----------------------------------------+-----+
                     |                                        |
         Trigger: PR |                                        | Trigger: Night window
                     v                                        v
       +---------------------------+              +---------------------------+
       |  AgentEvaluator.evaluate  |              |  AgentOptimizer.optimize  |
       |   (pytest runs)           |              |   update_source=True      |
       |                           |              |                           |
       |  Score < threshold → Red  |              |  Success → Overwrite      |
       |  pytest exit != 0 →       |              |  source prompts           |
       |  Block PR                 |              |  Failure → Files unchanged|
       +---------------------------+              +-------------+-------------+
                                                                |
                                                                v
                                                       Next PR automatically
                                                       uses new prompts
                                                      (Forms "eval→optimize→eval"
                                                       evolution closed loop)
```

**Integration in 3 steps**:

**Step 1: Extract `call_agent` into a module shared by evaluate / optimize**

```python
# agent/agent.py (both pytest and optimizer import from here)
async def call_agent(query: str) -> str:
    ...
```

**Why must share**: The agent used during evaluation and the agent used during optimization must be **equivalent**—otherwise "optimizer found a good prompt that evaluator cannot verify" or the reverse problem will occur. Sharing the same `call_agent` file is the most direct code-level guarantee. Any agent changes (model switch / temperature adjustment / output schema change) only need to be changed in one place.

**Step 2: Write pytest entry for PR gate**

```python
# tests/test_agent_quality.py
import pytest
from trpc_agent_sdk.evaluation import AgentEvaluator
from agent.agent import call_agent

@pytest.mark.asyncio
async def test_agent_quality():
    await AgentEvaluator.evaluate(
        call_agent=call_agent,
        eval_set_path="data/val.evalset.json",
        test_config_path="optimizer.json",       # Reuse same metric configuration
        ...
    )   # Framework throws AssertionError when score is below threshold → pytest red
```

Run in CI pipeline:

```bash
pytest tests/ --junitxml=runs/pytest_report.xml
```

The `--junitxml` output is a standard format test report, parsed natively by mainstream platforms like GitHub Actions / BlueKing Pipeline / Tencent CI. When failing, the `AssertionError` message contains the failure details JSON for each case; when the CI platform displays the stack trace, it can directly see which case failed, what the agent actually output, and where the difference from expected is.

**Step 3: Night window runs optimization + `update_source=True`**

```python
# run_optimization.py (triggered by night cron)
await AgentOptimizer.optimize(
    config_path="optimizer.json",           # Same metric configuration as pytest
    call_agent=call_agent,                  # Same call_agent as pytest
    target_prompt=target,
    train_dataset_path="data/train.evalset.json",
    validation_dataset_path="data/val.evalset.json",
    output_dir="runs/optimize_<timestamp>/",
    update_source=True,                     # Key switch for CI closed loop
)
```

Safety guarantee of `update_source=True`: Source prompt files are only written back when `OptimizeResult.status=SUCCEEDED`; source files remain unchanged in other states such as failure / budget exhaustion. Overwrite uses atomic write (tmp + `os.replace`), midway exceptions / SIGINT will not corrupt source prompt files (see [§8.3](#83-atomic-disk-persistence-guarantee) for details).

It is recommended to add `git diff --quiet agent/prompts/` at the end of the night script to determine if there are changes; exit directly if no changes; if there are changes, then `git checkout -b ...` + automatically open a PR—letting new prompts go through the standard PR review process instead of directly entering main branch.

**Pre-integration checklist**:

| Check Item | Consequence of Failure |
| --- | --- |
| Is `call_agent` **the same code** shared by pytest and optimizer? | No → Agent for evaluation and agent for optimization are not equivalent; optimization direction and gate direction drift |
| Do pytest and optimizer use **the same metric configuration**? | No → "Evaluation can pass but optimizer sees low score" or the reverse problem. Recommend reusing through `test_config_path` in pytest for the `optimizer.json.evaluate` section |
| Is evalset physically split into train / val two files? | No → SDK `_validate_inputs` forcibly validates `train != val`, otherwise reports error fail-fast |
| Does the night script have `git diff` + automatic PR opening steps at the end? | No → Optimized prompts directly enter main branch, bypassing review; recommend always going through PR process |
| Is there a grayscale strategy for prompt changes ready? | When multiple business lines share the same prompt repository, recommend switching to `update_source=False` + business's own grayscale deployment tool |

**→ Complete example**: [`examples/optimization/ci_integration/`](../../../examples/optimization/ci_integration/)
- `agent/agent.py` — `call_agent` shared by pytest and optimizer
- `tests/test_agent_quality.py` — pytest gate entry (called at PR stage)
- `run_optimization.py` — Night optimization entry (`update_source=True`)
- `ci/run_pr_check.sh` / `ci/run_nightly_optimize.sh` — CI pipeline shell entries

### 4.7 Optimization Task Has Hard Constraints: Must Complete Within a Time Window / Cumulative Calls Not Exceeding N / Stop After Consecutive No-Improvement {#47}

**Your situation**: Your optimization task runs in a constrained environment—CI pipeline must end within N minutes, LLM backend quota is calculated monthly and single run cannot exhaust it, should actively give up after several consecutive rounds without improvement. **Single stop condition is not enough**: only setting timeout may stop before budget is used up, only setting budget may run until the end of time. You need a multi-stop strategy of "stop immediately when any SLO triggers".

**Integration model**: The `optimize.algorithm` section of `optimizer.json` provides 6 algorithm-level stop conditions, with **OR semantics**—stop immediately when any one triggers. You reverse-calculate each threshold according to business SLO, and enable multiple switches simultaneously. When optimization ends, the `OptimizeResult.stop_reason` field tells you which SLO triggered first, convenient for subsequent parameter tuning.

**Configuration in 3 steps**:

**Step 1: Select several stop conditions that the business cares about from the 6 types**

| Field | Trigger Condition | Typical Business Scenario |
| --- | --- | --- |
| `timeout_seconds` | Wall-clock exceeds N seconds | CI pipeline time window hard constraint (must end within N minutes) |
| `max_metric_calls` | Cumulative case evaluation count ≥ N | LLM backend quota hard upper limit |
| `max_candidate_proposals` | Reflection LM cumulative proposal count ≥ N | Limit reflection LM call budget |
| `max_iterations_without_improvement` | N consecutive rounds without best valset improvement | Actively give up when already converged or trapped in local optimum |
| `score_threshold` | Best valset pass_rate ≥ threshold | Already reached business goal, no need to continue |
| `max_tracked_candidates` | Pareto frontier candidate pool size ≥ N | Control memory and merge candidate space size |

See [§7.3.3](#733-optimizealgorithm-section) for the complete definition of each field. **Configure at least 1**—otherwise the framework reports fail-fast at startup.

**Step 2: Reverse-calculate each threshold according to business SLO**

```jsonc
{
  "optimize": {
    "algorithm": {
      "timeout_seconds": 90.0,                    // CI must end within X minutes → set X*60 / 2 to leave buffer
      "max_metric_calls": 30,                     // LLM quota → reverse-calculate by "calls × single-run duration"
      "max_iterations_without_improvement": 3,    // Give up after 3 consecutive rounds without improvement
      "score_threshold": 1.0                      // Stop when business goal is reached
    }
  }
}
```

**Two key reverse-calculations**:

| Item | How to test | How to reverse-calculate |
| --- | --- | --- |
| Typical single-round duration | Run a baseline, look at `rounds[*].durationSeconds` in `runs/<ts>/result.json` (take median) | `timeout_seconds` should be at least single-round duration × 2, otherwise the first round triggers stop and you cannot see optimization progress |
| Single-round metric_calls count | Same as above, look at `totalMetricCalls / totalRounds` in round | `max_metric_calls` should be able to run through at least `max_iterations_without_improvement` rounds, otherwise budget always triggers stop first |

**Step 3: Clarify whether to participate in framework-level metric early stop**

| Value | Semantics |
| --- | --- |
| `optimize.stop.required_metrics: "all"` or `["m1"]` | Metric reaching threshold also participates in OR trigger |
| `optimize.stop.required_metrics: []` | Only let the 6 algorithm-level stoppers decide |

Business requirements:
- **Care about whether metrics reach standard** (typical prompt quality optimization) → use `"all"` or specific list
- **Only care about time / call budget** (known to converge, purely carding resources) → use `[]`

**`stop_reason` value reference**: When optimization ends, the `OptimizeResult.stop_reason` value can tell you the trigger—`score_threshold_reached` / `budget_exhausted` / `timeout_reached` / `no_improvement` / `max_proposals_reached` / `max_tracked_candidates_reached` / `user_requested_stop` (user actively triggers through `optimize.stop` sentinel file).

**Pre-integration checklist**:

| Check Item | Consequence of Failure |
| --- | --- |
| Are thresholds all reverse-calculated through baseline measurements, not intuited? | No → Highly likely some stopper always triggers first (e.g., timeout triggers in round 1), other configurations are decoration |
| Does `timeout_seconds` leave buffer (≤ 50% of real business window)? | No → Under the framework's "complete current round then stop" semantics, actual termination time may exceed the timeout set value, hitting business hard deadline |
| Do single-round LLM calls have their own timeout (e.g., CLI / HTTP calls)? | No → Single round hangs, entire timeout can only wait for current round to finish, may seriously exceed timeout (refer to CLI_TIMEOUT_SEC pattern in §4.2) |
| Have you run a baseline in the test environment once to verify `stop_reason` is consistent with expectations? | No → Only discover stopper behavior is inconsistent with expectations after going to CI, cannot quickly diagnose |

**→ Complete example**: [`examples/optimization/slo_runtime_control/`](../../../examples/optimization/slo_runtime_control/)
- `optimizer.json` — Configuration example with all 6 stop conditions enabled (business real integration should reverse-calculate thresholds according to own SLO, do not directly copy example values)
- `run_optimization.py` — After running, `result.json.stop_reason` field identifies the trigger

### 4.8 Can Already Run Through Basic Process, Want to Further Improve Results (GEPA Candidate Selection / Pareto Frontier / Cross-Field Fusion) {#48}

**Your situation**: You have already run through the basic optimization process according to quickstart, and can stably see score improvement from baseline → best. Now you want to understand several advanced switches of GEPA—`candidate_selection_strategy` / `frontier_type` / `use_merge` / `skip_perfect_score`—whether they are **actually useful on your task, whether they can squeeze out a few more points**. But running optimization once often cannot see the difference, because GEPA can converge to similar `best_pass_rate` on most tasks—**the difference is hidden in the arrival path** (round count / acceptance rate / whether merge triggered / reflection LM call count), not in the final score.

**Integration model**: Use **A/B controlled experiment**—same business, same evalset, same `seed`, run two different `optimizer.json`: one is the current online configuration or default configuration (baseline), one is the advanced combination to be verified. After running, compare the two `result.json`, focusing on **multi-dimensional metrics** rather than single `best_pass_rate`.

**Experiment in 3 steps**:

**Step 1: Use current configuration as baseline, fix other variables**

```jsonc
// optimizer_baseline.json
{
  "optimize": {
    "algorithm": {
      "seed": 42,                              // Fix seed to exclude randomness
      "max_metric_calls": 30,                  // Keep consistent with advanced to fairly compare
      "candidate_selection_strategy": "pareto",
      "frontier_type": "instance",
      "skip_perfect_score": false,
      "use_merge": false
    }
  }
}
```

**Step 2: Write advanced configuration, only change the switches to be verified**

```jsonc
// optimizer_advanced.json (only differs from baseline by a few switches)
{
  "optimize": {
    "algorithm": {
      "seed": 42,
      "max_metric_calls": 30,
      "candidate_selection_strategy": "pareto",
      "frontier_type": "objective",            // Change: from instance to objective
      "skip_perfect_score": true,              // Change: skip perfect score cases to save reflection calls
      "use_merge": true                        // Change: enable cross-field fusion (only actually triggers in multi-field)
    }
  }
}
```

**Step 3: Run twice + parse `result.json` to output multi-dimensional comparison**

```bash
python run_baseline.py        # Produce runs/baseline_<ts>/result.json
python run_advanced.py        # Produce runs/advanced_<ts>/result.json
python compare.py             # Parse two result.json, output comparison table
```

Dimensions `compare.py` should focus on:

| Dimension | Field (indexed by camelCase in `result.json`) | Interpretation |
| --- | --- | --- |
| Final quality | `bestPassRate` / `baselinePassRate` | End-to-end score improvement; two strategies converge closely on most tasks |
| Exploration depth | `totalRounds` / `roundsAccepted` | Acceptance rate (`roundsAccepted / totalRounds`) reflects frontier acceptance threshold |
| Merge behavior | `mergeRoundsTotal` / `rounds[*].kind` | Verify `use_merge=true` actually triggers merge |
| Reflection budget | `metricCallsTotal` / `proposalsTotal` | `skip_perfect_score=true` saves more obviously on large training set + high baseline start |
| `stop_reason` | `stopReason` | Which stopper triggered; cannot directly compare when advanced/baseline have different stop_reason |

> **Pitfall reminder**: Fields in `result.json` are camelCase (`bestPassRate` not `best_pass_rate`). SDK uses snake_case internally, automatically converted to camelCase during serialization through pydantic alias. Index by camelCase when reading `result.json`.

**Expected performance of several advanced switches** (may not all hold on business tasks—use your own actual measurements as basis):

| Switch | Expected Benefit | Applicable Prerequisites |
| --- | --- | --- |
| `frontier_type="objective"` (vs `"instance"`) | Higher acceptance rate / more aggressive exploration | Multi-metric scenario; may overfit train minibatch on small training set (< 10 cases) causing valset oscillation |
| `frontier_type="hybrid"` | Multiple metrics do not overwrite each other | Real conflict scenario with multiple metrics (see §4.5) |
| `skip_perfect_score=true` | Save reflection LM calls | Large-scale training set + high baseline start; few perfect score cases on small dataset, limited savings |
| `use_merge=true` | Cross-field fusion candidates | **Only actually triggers when multi-field (`add_path` ≥ 2)**; always 0 merge rounds in single-field configuration (`mergeRoundsTotal=0` is expected, see §4.3) |

**Pre-integration checklist**:

| Check Item | Consequence of Failure |
| --- | --- |
| Do the two configurations only differ in **the few switches to be verified**, all others identical? | No → Comparison result contains confounding variables, conclusion is not credible |
| Is `seed` consistent between the two sets? | No → Difference may come from randomness rather than configuration strategy |
| Is `max_metric_calls` consistent between the two sets? | No → One set naturally has higher score with more budget, cannot attribute to strategy |
| Are you simultaneously focusing on **multi-dimensional comparison** rather than single `bestPassRate`? | No → Final scores of two strategies are close on most tasks, cannot see difference; difference is hidden in arrival path |
| Do switches like `use_merge` / `skip_perfect_score` make sense in your task structure? | Enabling `use_merge` on single-field task never triggers (harmless but no benefit); enabling `skip_perfect_score` on high-baseline task saves considerably |

> Advanced configuration is **not the more complex the better**. On many tasks, baseline configuration can already achieve reasonable convergence; advanced only shows value in specific task structures (multi-objective, multi-field, large-scale training set, etc.). **Use data to decide, not intuition**.

**→ Complete example**: [`examples/optimization/advanced_strategies/`](../../../examples/optimization/advanced_strategies/)
- `optimizer_baseline.json` / `optimizer_advanced.json` — Two configurations for A/B control (only differ by 3 switches)
- `run_baseline.py` / `run_advanced.py` — Two independent entries (keeping other variables consistent)
- `compare.py` — Standard template for parsing two `result.json` and outputting multi-dimensional comparison table

## 5 How GEPA Works

After running an optimization and watching the score increase from 0.4 to 0.85, you don't know **what exactly the framework did along the way**—what data did it read? What did the reflection LM see? On what basis did it decide to retain or discard a candidate? When SLO triggers, does it stop immediately or wait for the current round to finish?

> **GEPA** = Genetic-Evolutionary Pareto, is a reflection-based evolutionary search algorithm ([gepa-ai/gepa](https://github.com/gepa-ai/gepa), MIT License). This framework wraps `gepa.optimize()` into `GepaReflectiveOptimizer` through `OPTIMIZER_REGISTRY`, and adds a layer of SDK adaptation (evaluation bridging, reflection feedback construction, stop determination, atomic disk persistence, etc.).

### 5.1 What Exactly Runs in One Optimization Round

**First remember three roles**—all subsequent diagrams and tables revolve around these three:

| Role | Who Is It | What It Does |
| --- | --- | --- |
| **agent** | Your business agent (accessed through `call_agent`) | Receives one query, outputs one response |
| **judge / metric** | Configured evaluators in `evaluate.metrics` | Score agent responses (0~1) |
| **Reflection LM** | LLM configured in `algorithm.reflection_lm` | Views failure case feedback → generates new prompt candidates |

**Round 0**: Run valset with baseline prompt → get baseline score (your "starting line")

**Each subsequent round (reflective round)** follows these 5 steps:

```text
                    ┌────────────────────────────┐
                    │  Candidate prompt selected  │
                    │  in previous round          │
                    └──────────────┬─────────────┘
                                   ▼
            (1) Sample minibatch       → Randomly sample N cases from trainset
                                         (N = reflection_minibatch_size)
                                   │
                                   ▼
            (2) Run one evaluation     → Write candidate to prompt file
                                       → Call call_agent to run these N cases
                                       → Metric scores, get failure cases
                                   │
                                   ▼
            (3) Reflection LM          → Feed failure case feedback to
                generates candidate      reflection LM
                                       → It outputs new prompt text
                                   │
                                   ▼
            (4) Re-evaluate + enter    → Re-run new candidate on minibatch
                Pareto frontier        → Better than historical → enter
                                         frontier, otherwise discard
                                   │
                                   ▼
            (5) Check stop conditions  → Any of 6 stoppers triggered → stop
                                       → Otherwise enter next round
```

**Several key explanations**:

- **"Evaluation" in step (2)** actually runs `len(minibatch) × num_runs × len(metrics)` LLM evaluations (see §6.1 for details)
- **"What reflection LM sees" in step (3)** determines rewrite quality—this is the content of next section §5.2
- **"Pareto frontier" in step (4)** simply put is "retain the set of candidates that are not surpassed in all aspects"; specific granularity is controlled by `frontier_type` (see §5.3 for details)
- **"Stop when any triggers" in step (5)** has a detail: after triggering, **wait for current round to finish before actually stopping**, not immediately kill (see §5.4 for details)
- **Valset evaluation** is interleaved in the middle rounds (determined internally by gepa), used to calculate the "real score of current best candidate on valset", also the basis for stopper judgments such as `score_threshold` / `required_metrics`

**Special case: merge round**

When `use_merge=true`, a **merge round** is inserted every several reflective rounds: select two candidates from the Pareto frontier and fuse them into one new candidate ("take A's wording on field X + B's wording on field Y"). **Only meaningful in multi-field scenarios**—never triggers in single-field, `mergeRoundsTotal=0` is expected. See §4.3 for details.

### 5.2 What Reflection LM Actually Sees

The quality of the reflection LM's prompt rewriting **completely depends on how rich the failure feedback it can see**. If you only tell it "case_3 failed, score 0.3", it can only guess blindly; if you tell it "case_3 turn 2 agent should output `{"city":"Shanghai"}` but actually output `Shanghai`, rule requires case-sensitive exact match", it can targetedly modify the prompt.

`_AgentGEPAAdapter.make_reflective_dataset` renders a markdown record for each **failed case**, fed to the reflection LM. Each record field:

| Field | One-Line Explanation | When It Appears |
| --- | --- | --- |
| `case_id` | Stable ID of the case (for reflection LM cross-reference) | Always |
| `score` | Aggregate score of this case (0~1, 1.0 = all metrics passed) | Always |
| `Case Body` | Markdown of failure scene: one segment per turn, containing user input, expected response, agent actual response, tool call trace, each metric's judgment (PASS/FAIL + score + failure reason) | Always |
| `Other Active Components` | What do other prompt fields NOT being rewritten in this round look like | When multi-field optimization—lets reflection LM see B/C status when modifying A, avoiding breaking upstream/downstream compatibility |
| `history_top_k` | Best agent responses for this case in history (sorted by score) | When `reflection_history_top_k > 0` |

**Specific structure of `Case Body`**:

```text
### Turn 1
**User**: <User original input>
**Expected**: <Expected response>
**Agent Response**: <Agent actual response>
**Tool Trace**:                    ← Only when tool calls exist
  - tool_name(args) → response
**Verdict** (Turn 1):
  [FAIL] metric_name: score=0.0000, threshold=1.0000
    reason: agent output not byte-equal to expected (case-sensitive)
    · rubric[no_emoji]: PASS score=1.00     ← Only for LLM rubric metric

### Turn 2
...

### Overall (case-level aggregate)   ← When multi-turn or multi-run
...
```

**Failure reason synthesis for deterministic metrics**: When metric is an evaluator without LLM judge like `final_response_avg_score`, only outputting score+status, the framework will **automatically synthesize a failure explanation** (e.g.: `agent output not byte-equal to expected (case-sensitive)` / `expected substring not contained in agent output (case-insensitive)` / `JSON structural comparison failed`), letting the reflection LM directly see **why it didn't match**, without having to diff text to guess.

> Want to see the full reflection prompt that the reflection LM actually receives? Set `verbose=2` when running optimization, gepa internal logs will include each round's reflection prompt text—read it once and you'll have a good understanding.

### 5.3 Actual Behavior of 5 Core Operators

The 5 switches most frequently asked about in the `optimize.algorithm` section of `optimizer.json`, what they actually do in the source code:

| Operator | One-Line Function | Typical Motivation to Adjust It | Detailed Reference |
| --- | --- | --- | --- |
| `reflection_minibatch_size` | How many cases the reflection LM sees each round | Smaller saves tokens, larger gives reflection LM more complete view | [§7.3.3](#733-optimizealgorithm-section) |
| `module_selector` | Which field to modify this round in multi-field (`round_robin` rotation / `all` select all / `random` random) | Want clear attribution of each field's contribution → `round_robin` | [§4.3](#43) |
| `frontier_type` | Pareto frontier granularity (`instance` one best per case / `objective` one per metric / `hybrid` two-layer / `cartesian` Cartesian product) | When multiple metrics truly conflict → `hybrid` | [§4.5](#45) |
| `candidate_selection_strategy` | How to select parent for next round's reflection (`pareto` default select from frontier / `current_best` use current best / etc.) | Want to accelerate convergence or increase exploration | [§7.3.3](#733-optimizealgorithm-section) |
| `use_merge` + `max_merge_invocations` | Whether to enable cross-field fusion + upper limit on trigger count | **Only actually triggers in multi-field**—`mergeRoundsTotal=0` is expected in single-field | [§4.3](#43) / [§4.8](#48) |

### 5.4 Stop Timing: Complete Current Round Before Stopping

6 algorithm-level stop conditions (`max_metric_calls` / `timeout_seconds` / `no_improvement` / `score_threshold` / `max_candidate_proposals` / `max_tracked_candidates`) are **synchronously checked at the end of each round**—stop when any condition is satisfied.

**3 easily stepped-on details**:

| Detail | Meaning | How to Avoid |
| --- | --- | --- |
| **Does not immediately kill current round** | When stop is triggered, it will not interrupt the currently running round; must wait for current round to finish before actually stopping | In SLO hard deadline scenarios, set `timeout_seconds` to about 50% of the real business window, leave buffer |
| **Actual termination time often exceeds `timeout_seconds`** | Direct consequence of the previous point—especially obvious when stuck in a long round | Add your own timeout to LLM calls inside `call_agent` (refer to 90s timeout in §4.2 CLI) |
| **Priority when multiple stoppers trigger simultaneously** | `framework_stopper` (`required_metrics` policy) first; then take the first one in algorithm-level stopper insertion order | `OptimizeResult.stop_reason` field records the trigger, see which one triggered directly after running |

**`stop_reason` value reference** (`OptimizeResult.stop_reason`):

```
required_metrics_passing  ← framework-level (highest priority)
score_threshold           ← Reached target score
budget_exhausted          ← max_metric_calls
timeout                   ← timeout_seconds
no_improvement            ← max_iterations_without_improvement
max_candidate_proposals
max_tracked_candidates
user_requested_stop       ← User touched optimize.stop file
completed                 ← No stopper triggered, gepa naturally finished
```

### 5.5 A Special Case: FAILED

Normally `OptimizeResult.status = "SUCCEEDED"`—gepa finished the loop (natural end / stopper trigger both count). But there is one special status worth user attention:

- **`status = "FAILED"`**: gepa threw an exception during running (most common: training/validation set loading failure, `gepa.optimize()` internal exception, reflection LM call failure)
- **At this time `best_prompts` is forcibly set to `baseline_prompts`**—ensuring the artifacts you get **will never be worse than baseline**
- **`update_source=True` will not write back** source prompt files when FAILED (see §3.4 decision table for details)

Another easily confused point is "finished running but no improvement": in this case `status` is still `"SUCCEEDED"`, but `finish_reason="no_improvement"`, and `best_prompts == baseline_prompts`—`summary.txt` will show `baseline → baseline` (no degradation nor improvement). This is expected, not a bug.


## 6 Cost and Concurrency

How many LLM calls does one optimization run require? Which knobs affect call volume, which affect concurrency, which affect both?

### 6.1 Where LLM Calls in One Optimization Come From

LLM calls are divided into two parts—**evaluation side eats the vast majority**, reflection side is just a fraction:

**Evaluation side (agent + judge)**: Run each of these once, each calls LLM once—

```text
Run one baseline evaluation:   Run valset fully once                          ← Starting point, 1 time
Each reflective round:         Sample N cases and run once + re-run candidate ← Main cost
Specific reflective round:     Re-evaluate current best candidate on valset   ← Determined by gepa
```

Actual LLM call count triggered by each "run once" = **number of cases × agent call count per case × `num_runs` × judge call count per metric**. Among them:

| Multiplier | Source | Typical Value |
| --- | --- | --- |
| Agent call count per case | Evalset data; accumulate by turn count in multi-turn conversation | Single turn = 1, multi-turn = N |
| `evaluate.num_runs` | Run each case several times and take mean to eliminate LLM output variance | 1 (default, saves) / 2~3 (recommended, stable) |
| Judge call count per metric | Depends on metric type: `final_response_avg_score` type deterministic matching = 0 times; `llm_judge` / `llm_rubric_response` ≥ 1 time (however many are in `judge_models` array) | 0~3 |

**Reflection side (reflection LM)**:

```text
Each reflective round:    1 time (generate new candidate prompt)
Each merge round:         1 time (only when use_merge=true and multi-field)
```

Reflection side call count is much less than evaluation side—usually 5~20 times for a complete optimization.

### 6.2 What to Read from result.json After Running

Fields actually recorded in `OptimizeResult` (camelCase indexed in artifact `result.json`):

| Field | Meaning |
| --- | --- |
| `totalMetricCalls` | Cumulative case-level evaluation count by gepa |
| `totalReflectionLmCalls` | Cumulative reflection LM call count (including retries) |
| `totalTokenUsage` | Cumulative tokens for reflection LM: `{prompt, completion, total}` |
| `durationSeconds` | Total wall-clock duration |

When needing to estimate actual USD cost on the business side, use `totalTokenUsage` × LLM backend unit price to reverse-calculate reflection side; agent / judge side is pulled from LLM backend usage records (API console / billing reports).

### 6.3 Multiplier Effect of 4 Commonly Used Knobs

Sorted by "magnitude of impact on total call volume" from large to small—when encountering optimization running out of budget, adjust the ones above first:

| Knob | Multiplies By How Much | Cost of Turning Down | Details |
| --- | --- | --- | --- |
| `algorithm.max_metric_calls` | **Hard upper limit on total call volume**—gepa stops when cumulative reaches it | Too small → Stopped by it in the 1st round; cannot see any score improvement | [§4.7](#47) |
| `evaluate.num_runs` | **Multiply by N**—run each case N times and take mean | LLM output variance directly enters score when 1 (same prompt gets different scores on two runs); recommend ≥ 2 | [§4.5](#45) |
| `optimize.eval_case_parallelism` | **Does not affect total volume**, only affects **wall-clock time** and **instantaneous QPS** | Higher saves time but easily hits LLM backend rate limit | [§4.5](#45) |
| `algorithm.reflection_minibatch_size` | **Multiply by a few**—how many cases the reflection LM sees each round; evaluation side also calculates by this number | Too large → Reflection prompt explodes LLM context window | [§4.3](#43) |

### 6.4 Want to Reasonably Set Thresholds? Run a Baseline First

Before setting thresholds such as `timeout_seconds` / `max_metric_calls`, **first run a baseline with default configuration**—read two numbers from the artifacts:

| Value to Measure | How to Test | How to Use |
| --- | --- | --- |
| **Typical single-round duration** | `rounds[*].durationSeconds` in `runs/<ts>/result.json` (take median) | `timeout_seconds` should be at least single-round duration × 2, otherwise stop is triggered in round 1 and you cannot see optimization progress |
| **Single-round metric_calls** | Same as above, `totalMetricCalls / totalRounds` | `max_metric_calls` should be able to run through at least `max_iterations_without_improvement` rounds, otherwise budget always triggers stop first |

**Example**: Baseline run shows 30 seconds per round, 4 metric_calls per round, CI window 5 minutes—then `timeout_seconds=120` (leave buffer), `max_metric_calls=24` (enough to run 6 rounds for `max_iterations_without_improvement=3` to trigger stop).

### 6.5 Single-Round Instantaneous LLM QPS Control

Number of LLM requests concurrently sent in a single round:

```text
Single-round instantaneous LLM QPS ≈ eval_case_parallelism
                                    × num_runs
                                    × (agent calls per case + all judge calls)
```

**Typical scenario estimation**: 3 judges + `num_runs=2` + `eval_case_parallelism=4` + 1 agent call per case + 3 judge calls → about 32 LLM requests per round instantaneous. When LLM backend rate limit is 30 QPS, this configuration will inevitably trigger rate limiting.

**Two parameters to control instantaneous QPS** (sorted by effect):

| Parameter | Impact | Applicable |
| --- | --- | --- |
| `eval_case_parallelism` | Directly reduces concurrent case count | First choice for most situations; set to `1` for serial execution in scenarios with intensive single-case calls such as black-box CLI, multi-judge (see [§4.2](#42), [§4.5](#45)) |
| `num_runs` | Reduces repeated evaluation per case | Sacrifices some variance stability; recommend only lowering after confirming LLM output variance is small |

### 6.6 Reflection LM Selection and Configuration

The output quality of the reflection LM directly determines prompt rewriting quality. Configuration location (`optimizer.json`):

```jsonc
{
  "optimize": {
    "algorithm": {
      "reflection_lm": {
        "model_name": "${TRPC_AGENT_MODEL_NAME}",
        "base_url":   "${TRPC_AGENT_BASE_URL}",
        "api_key":    "${TRPC_AGENT_API_KEY}",
        "generation_config": {
          "max_tokens": 4096,           // Reflection prompt is long, leave enough output space
          "temperature": 0.6            // Between 0.6~0.8, let LM be creative
        }
      }
    }
  }
}
```

**Two suggestions**:

- **Can be configured independently from agent / judge**—the `reflection_lm` section is independent, business can choose different model (avoid "self-evaluation" bias, or purely because reflection tasks require higher model reasoning power)
- **Token usage is truly recorded**—the `totalTokenUsage` field will accumulate actual prompt + completion + total token count for reflection LM; reverse-calculate USD by LLM backend unit price


## 7 Complete API Reference

Reference manual section, organized by "what parameter are you looking for". **Each table has a "Required" column**, three-gear meaning:

- **Required**: Not passed/not configured → fail-fast error at startup
- **Optional**: Can be omitted; uses default value when not configured
- **Conditionally Required**: Can be omitted when looking at the entry alone, but **must be configured when satisfying certain conditions**—conditions written in the "Condition" column at the end of each entry

All fields are based on actual source code (source file path annotated in each table header).

### 7.1 `AgentOptimizer.optimize` Parameter Table

Source code: `trpc_agent_sdk/evaluation/_agent_optimizer.py:AgentOptimizer.optimize`. **11 keyword-only parameters**—must be passed in `key=value` form, positional parameters not accepted.

| Parameter | Required | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `config_path` | **Required** | `str` | — | optimizer.json configuration file path |
| `call_agent` | **Required** | `async (str) -> str` | — | Business agent adapter function; signature fixed as "accept query return str" |
| `target_prompt` | **Required** | `TargetPrompt` | — | Register which prompt fields are optimization targets (at least 1, otherwise error) |
| `train_dataset_path` | **Required** | `str` | — | Training evalset file path |
| `validation_dataset_path` | **Required** | `str` | — | Validation evalset file path; **must be different from `train_dataset_path`** (prevent data leakage, framework will normalize paths before comparing) |
| `output_dir` | **Required** | `str` | — | Artifact directory; created automatically if it doesn't exist |
| `callbacks` | Optional | `Optional[Callbacks]` | `None` | Evaluator lifecycle callbacks (rarely used) |
| `update_source` | Optional | `bool` | `False` | Whether to write back to source prompt files after successful optimization (decision table see [§3.4](#34-agentoptimizer)) |
| `verbose` | Optional | `int` | `1` | Terminal output verbosity: `0` silent / `1` default Rich panel / `2` plus gepa internal log forwarding |
| `extra_stop_callbacks` | Optional | `Optional[Sequence]` | `None` | Stoppers appended at runtime (SLO monitoring / kill switch, etc.); ordinary callable displays as `stop_reason="completed"`, use `_LabeledStopper` wrapper or expose `.label` attribute when needing stable labels |
| `extra_gepa_callbacks` | Optional | `Optional[Sequence]` | `None` | Gepa event callbacks appended at runtime (e.g., forwarding to dashboard); need to implement `gepa.core.callback.GEPACallback` protocol |

**Return value**: `OptimizeResult` (see [§7.4](#74-optimizeresult--roundrecord-field-table) for details).

**Fail-fast checks at startup** (`_validate_inputs`):

| Situation When Check Fails | Throws |
| --- | --- |
| `output_dir` is empty string | `ValueError` |
| `target_prompt` did not register any fields | `ValueError` |
| `call_agent` is not async function (including `__wrapped__` check, supports `functools.partial` wrapped async) | `TypeError` |
| `train_dataset_path` and `validation_dataset_path` resolve to the same file (compared after normalizing with `os.path.normpath(os.path.abspath(...))`) | `ValueError` (prevent data leakage) |
| `evaluate.metrics` contains `tool_trajectory_avg_score` or `llm_rubric_knowledge_recall`—these two require session traces / tool intermediate_data, which cannot be obtained in `call_agent` black-box mode | `ValueError` |
| `algorithm.name` in config is not registered in `OPTIMIZER_REGISTRY` | `ValueError` (message lists all registered algorithm names) |
| `use_merge=true` and `TargetPrompt` field count < 2 | `UserWarning` (not fatal, but `mergeRoundsTotal` will always be 0) |

### 7.2 `TargetPrompt` API Table

Source code: `trpc_agent_sdk/evaluation/_target_prompt.py`. A container for registering multi-field prompts, supports both file source and callback source forms.

| Method | Signature | Behavior |
| --- | --- | --- |
| `add_path(name, path)` | `(str, str) -> Self` | Register file source field; `name` must be unique; returns self for chained calls |
| `add_callback(name, *, read, write)` | `(str, *, AsyncRead, AsyncWrite) -> Self` | Register callback source field; `read: async () -> str`, `write: async (str) -> None` must both be async; `name` must be unique |
| `names()` | `() -> list[str]` | Return field names (in registration order) |
| `describe_source(name)` | `(str) -> str` | File source returns path; callback source returns literal `"<callback>"`; unknown name throws `KeyError` |
| `read(name)` | `async (str) -> str` | Read single field |
| `read_all()` | `async () -> dict[str, str]` | Read all fields (in registration order) |
| `write_all(prompts)` | `async (dict[str, str]) -> None` | **Atomically write all fields** (see contract below for details) |

**Atomicity contract of `write_all`** (from source code comments):

1. **File source atomic write**: First write to `<path>.tmp`, then `os.replace` rename (POSIX guarantees rename atomicity)
2. **Failure rollback**: When any file write fails, already successfully written files roll back to pre-call content, clean up residual `.tmp`, original exception normally re-raised
3. **Rollback itself fails**: Original exception is preserved through `__context__`, and `_RollbackError` is raised listing each field's rollback failure details—rollback is best-effort, one field's failure does not skip subsequent ones
4. **Callback source does not rollback**: After file source writes successfully, then run callback sources in order; when callback source fails, file source rolls back to baseline, but **callback source itself does not rollback** (idempotency is caller's responsibility)

**Key validation of `write_all`**: The key set of incoming `prompts` must **exactly equal** the registered field name set, otherwise throws `ValueError`.

### 7.3 `optimizer.json` Configuration Items Table

Source code: `trpc_agent_sdk/evaluation/_optimize_config.py`. pydantic schema, **supports both camelCase and snake_case keys**. Top-level structure:

```jsonc
{
  "evaluate": { ... },         // Evaluation section (same schema as AgentEvaluator)
  "optimize": {                // Optimizer section
    "eval_case_parallelism": 4,
    "stop": { ... },           // Framework-level stop
    "algorithm": { ... }       // Algorithm block (including reflection_lm)
  }
}
```

#### 7.3.1 `evaluate` Section

Source code: `_eval_config.py:EvalConfig`.

| Field | Required | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `metrics` | **Conditionally Required** (see below) | `Optional[list[dict]]` | `None` | Metric array, each containing `metric_name` / `threshold` / `criterion`. **When `metrics` is configured, `criteria` is ignored** |
| `criteria` | **Conditionally Required** (see below) | `dict[str, Any]` | `{}` | Old-style shorthand: `metric_name → threshold` or `{threshold, criterion}` |
| `num_runs` | Optional | `int` | `1` | How many times to run each case and take mean (eliminate LLM output variance); `≥ 2` recommended |
| `user_simulator_config` | Optional | `Optional[Any]` | `None` | User simulator configuration (multi-turn scenarios; rarely used) |

**Condition**: At **least 1** of `metrics` and `criteria` must be configured—when both are empty, `evaluate.get_eval_metrics()` returns empty list, and startup will report error due to no metrics. New integrations recommend using `metrics` (more structured), `criteria` is mainly kept for compatibility with old configurations.

#### 7.3.2 `optimize` Section

Source code: `_optimize_config.py:OptimizeConfig`.

| Field | Required | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `eval_case_parallelism` | Optional | `int` | `4` | Case concurrency within same round (does not affect total call volume, affects instantaneous QPS) |
| `stop` | Optional | `FrameworkStopConfig` | `{required_metrics: "all"}` | Framework-level stop section (see [§7.3.5](#735-optimizestop-section) for details) |
| `algorithm` | **Required** | `GepaReflectiveAlgo` | — | Algorithm block (see [§7.3.3](#733-optimizealgorithm-section) for details) |

#### 7.3.3 `optimize.algorithm` Section

Source code: `_optimize_config.py:GepaReflectiveAlgo`. All adjustable parameters for the `gepa_reflective` algorithm.

> **Hard constraint**: Among the **last 6 stopper fields** in the table, **at least 1 must be configured**—if all are left empty (default `None`), it will be rejected by `_require_at_least_one_stop_condition`, throwing `ValueError` fail-fast. This is why they are marked as "Conditionally Required".

**Basic fields**:

| Field | Required | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `name` | **Required** | `Literal["gepa_reflective"]` | — | Algorithm selector; currently the only optional value |
| `reflection_lm` | **Required** | `OptimizeModelOptions` | — | Reflection LM configuration (see [§7.3.4](#734-optimizealgorithmreflection_lm-section) for details) |
| `seed` | Optional | `int` | `42` | Random seed; two sets of configurations should be consistent when A/B testing |

**Search behavior fields**:

| Field | Required | Type | Default | Values and Description |
| --- | --- | --- | --- | --- |
| `candidate_selection_strategy` | Optional | Literal | `"pareto"` | `pareto` select from frontier (default recommended) / `current_best` use current best / `epsilon_greedy` exploration-exploitation / `top_k_pareto` random from top K of frontier |
| `module_selector` | Optional | `str` | `"round_robin"` | Which field to modify this round in multi-field: `round_robin` rotate in registration order / `all` select all / `random` random |
| `frontier_type` | Optional | Literal | `"instance"` | Pareto frontier granularity: `instance` one best per case / `objective` one per metric / `hybrid` two-layer / `cartesian` Cartesian product |
| `reflection_minibatch_size` | Optional | `Optional[int]` | `None` | Minibatch size for each round's reflection; `None` lets gepa decide |
| `reflection_history_top_k` | Optional | `int` (0~5) | `2` | How many historical best responses to give reflection LM for each case; 0 disables, upper limit 5 |
| `perfect_score` | Optional | `float` | `1.0` | "Perfect score" threshold (used with `skip_perfect_score`) |
| `skip_perfect_score` | Optional | `bool` | `True` | Skip cases that already have perfect score during reflection |

**Multi-field fusion (merge) fields**:

| Field | Required | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `use_merge` | Optional | `bool` | `False` | Enable merge round; **only actually triggers in multi-field (≥2)**, never triggers in single-field and won't report error (only `UserWarning`) |
| `max_merge_invocations` | Optional | `int` | `5` | Upper limit on merge trigger count |
| `merge_val_overlap_floor` | Optional | `int` | `5` | Minimum val set case overlap count to trigger merge |

**Performance fields**:

| Field | Required | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `cache_evaluation` | Optional | `bool` | `False` | Cache (candidate, case) scores; skip directly on repeated evaluation |
| `track_best_outputs` | Optional | `bool` | `False` | Track best output for each case |

**6 stop condition items**—**configure at least 1** (OR semantics trigger):

| Field | Required | Type | Default | Trigger Condition |
| --- | --- | --- | --- | --- |
| `max_metric_calls` | Conditionally Required | `Optional[int]` | `None` | Cumulative case-level evaluation count ≥ N → stop |
| `max_iterations_without_improvement` | Conditionally Required | `Optional[int]` | `None` | N consecutive rounds without best valset improvement → stop |
| `timeout_seconds` | Conditionally Required | `Optional[float]` | `None` | Wall-clock exceeds N seconds → stop |
| `score_threshold` | Conditionally Required | `Optional[float]` | `None` | Best valset score ≥ N → stop |
| `max_candidate_proposals` | Conditionally Required | `Optional[int]` | `None` | Candidate proposal count ≥ N → stop |
| `max_tracked_candidates` | Conditionally Required | `Optional[int]` | `None` | Pareto candidate pool size ≥ N → stop |

**Condition**: At least 1 of the 6 items must be non-`None`, otherwise fail-fast at startup. See [§4.7 SLO Hard Constraints](#47) for details.

#### 7.3.4 `optimize.algorithm.reflection_lm` Section

Source code: `_optimize_model_options.py:OptimizeModelOptions`. Reflection LM connection configuration.

> **Only need to configure 4 in daily use**: `model_name` / `base_url` / `api_key` / `generation_config` (leave others as default). The 6 items marked "advanced" in the table below generally do not need to be touched.

| Field | Required | Type | Default | Description |
| --- | --- | --- | --- | --- |
| `model_name` | **Required** | `str` | `""` | Model name (e.g., `"gpt-4o-mini"`); empty string equals not configured, will report error at startup |
| `base_url` | Optional | `Optional[str]` | `None` | Custom endpoint URL |
| `api_key` | Optional | `str` | `""` | API key (most providers must provide, otherwise will report error at call stage) |
| `generation_config` | Optional | `Optional[dict]` | `None` | Generation parameters; typical: `{"max_tokens": 4096, "temperature": 0.6}` |
| `provider_name` | Advanced | `str` | `""` | Provider name; empty / `"openai"` goes to `OpenAIModel`, other values go to `ModelRegistry.create_model("{provider}/{model}")` |
| `variant` | Advanced | `str` | `""` | OpenAI-compatible variant (only when provider is openai) |
| `extra_fields` | Advanced | `Optional[dict]` | `None` | Extra fields transparently passed to underlying model |
| `num_samples` | Advanced | `Optional[int]` | `None` | Number of samples |
| `weight` | Advanced | `float` | `1.0` | Weight (multi-judge scenarios) |
| `think` | Advanced | `Optional[bool]` | `None` | Whether to enable thinking mode |

**Field values support environment variable expansion**—`"${TRPC_AGENT_API_KEY}"` will be automatically replaced.

#### 7.3.5 `optimize.stop` Section

Source code: `_optimize_config.py:FrameworkStopConfig`.

| Field | Required | Type | Default | Values |
| --- | --- | --- | --- | --- |
| `required_metrics` | Optional | `Optional[Union[Literal["all"], list[str]]]` | `"all"` | `"all"`: all metrics must reach threshold; `["m1", "m2"]`: listed metrics must reach threshold (other metrics still participate in evaluation but do not affect early stop); `null` or `[]`: disable framework-level early stop (rely only on algorithm-level stoppers) |

**List form validation**: Metric names in the list must be findable in `evaluate.metrics[]`, otherwise `OptimizeConfigFile._validate_required_metrics_against_evaluate` throws `ValueError` at startup, error message lists "unknown metrics" and "available metrics" checklist.

### 7.4 `OptimizeResult` + `RoundRecord` Field Table

Source code: `trpc_agent_sdk/evaluation/_optimize_result.py`. This is the return value of `optimize()`, and also the content of `runs/<ts>/result.json`.

> **Important convention**: Both `OptimizeResult` and `RoundRecord` are based on `EvalBaseModel` (`alias_generator=to_camel`). **Python in-memory uses snake_case, all converted to camelCase when serialized to JSON**—use camelCase when indexing `result.json` (`bestPassRate` not `best_pass_rate`), common pitfall. In the table below, the "Field" column uses Python names (snake_case), switch to camelCase when reading JSON.

#### 7.4.1 `OptimizeResult` Top-Level Fields

**Core result fields**:

| Field (snake_case) | Type | Meaning |
| --- | --- | --- |
| `status` | `Literal["SUCCEEDED", "FAILED", "CANCELED"]` | Final status; when `FAILED`, `best_prompts = baseline_prompts` |
| `finish_reason` | Literal | `completed` / `perfect_pass_rate` / `no_improvement` / `error` |
| `stop_reason` | `Optional[StopReason]` | Which stopper triggered (see [§5.4](#54-stop-timing-complete-current-round-before-stopping) for details); `None` when FAILED early stop |
| `error_message` | `str` | Error message when FAILED (default `""`) |
| `algorithm` | `str` | Algorithm name (e.g., `"gepa_reflective"`) |

**Score fields**:

| Field | Type | Meaning |
| --- | --- | --- |
| `baseline_pass_rate` | `float` | Pass rate of baseline on valset |
| `best_pass_rate` | `float` | Pass rate of optimal candidate on valset |
| `pass_rate_improvement` | `float` | `best - baseline` |
| `baseline_metric_breakdown` | `dict[str, float]` | Mean score of each metric for baseline |
| `best_metric_breakdown` | `dict[str, float]` | Mean score of each metric for optimal candidate |
| `metric_thresholds` | `dict[str, float]` | Threshold for each metric (copied from `evaluate.metrics[].threshold`) |
| `per_metric_best_candidates` | `dict[str, list[int]]` | Pareto frontier candidate index for each metric (0-based); empty = algorithm does not expose this information |

**Prompt fields**:

| Field | Type | Meaning |
| --- | --- | --- |
| `baseline_prompts` | `dict[str, str]` | Starting prompt content (keyed by TargetPrompt field names) |
| `best_prompts` | `dict[str, str]` | Optimal candidate prompts; = `baseline_prompts` when `FAILED` (ensuring artifacts **will never be worse than baseline**) |

**Round fields**:

| Field | Type | Meaning |
| --- | --- | --- |
| `total_rounds` | `int` | How many rounds were run |
| `rounds` | `list[RoundRecord]` | Each round's record (see §7.4.2 for details) |

**Statistics and time fields**:

| Field | Type | Meaning |
| --- | --- | --- |
| `total_reflection_lm_calls` | `int` | Cumulative reflection LM call count (including retries) |
| `total_token_usage` | `dict[str, int]` | Cumulative tokens for reflection LM: `{prompt, completion, total}` |
| `duration_seconds` | `float` | Total wall-clock duration |
| `started_at` / `finished_at` | `str` | ISO-8601 timestamps |

**Others**:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | `str` | Default `"v1"`; bump when artifact schema upgrades |
| `extras` | `dict[str, Any]` | Custom business fields; optimizer does not read or write |

#### 7.4.2 `RoundRecord` Fields (One Per Round)

**Basic round information**:

| Field | Type | Meaning |
| --- | --- | --- |
| `round` | `int` | 1-based round number |
| `kind` | `Literal["reflective", "merge"]` | Reflection round / fusion round |
| `started_at` | `str` | ISO-8601 timestamp |
| `duration_seconds` | `float` | Wall-clock duration of this round |

**Rewrite situation**:

| Field | Type | Meaning |
| --- | --- | --- |
| `optimized_field_names` | `list[str]` | Field names rewritten by reflection LM in this round |
| `candidate_prompts` | `dict[str, str]` | Full field content of this round's candidate |
| `accepted` | `bool` | Whether accepted as new best |
| `acceptance_reason` | `str` | Human-readable explanation of acceptance decision |
| `per_field_diagnosis` | `dict[str, str]` | Diagnosis text given by reflection LM for each field |

**Scoring situation**:

| Field | Type | Meaning |
| --- | --- | --- |
| `validation_pass_rate` | `float` | Pass rate of this round on valset |
| `metric_breakdown` | `dict[str, float]` | Mean score of each metric on valset this round; empty = this round did not run valset |
| `failed_case_ids` | `list[str]` | Failed case IDs on valset this round |
| `failed_cases_truncated` | `int` | Number of failed cases cut off due to token budget |
| `train_minibatch_size` | `int` | Minibatch size of this round; 0 = skip, not sampled |
| `train_subsample_parent_score` | `Optional[float]` | Parent candidate's score on minibatch; `None` = not run |
| `train_subsample_candidate_score` | `Optional[float]` | New candidate's score on minibatch; `None` = not run |
| `skip_reason` | `Optional[str]` | Skip reason (e.g., `"subsample perfect"`, `"no proposal"`) |
| `error_message` | `Optional[str]` | Algorithm error message this round |

**Statistical fields**:

| Field | Type | Meaning |
| --- | --- | --- |
| `reflection_lm_calls` | `int` | Reflection LM call count this round (including retries) |
| `round_token_usage` | `dict[str, int]` | Reflection LM tokens this round: `{prompt, completion, total}` |
| `budget_used` | `Optional[int]` | Cumulative used metric_calls |
| `budget_total` | `Optional[int]` | Configured budget upper limit (e.g., `max_metric_calls`) |

**`extras`** (`dict[str, Any]`): Custom business fields; optimizer does not read or write.

#### 7.4.3 `OptimizeResult` Utility Methods

| Method | Behavior |
| --- | --- |
| `dump_to(path)` | Serialize to JSON file (`indent=2`, `by_alias=True`) |
| `OptimizeResult.from_file(path)` | classmethod, deserialize from JSON |
| `format_summary(*, output_dir, update_source)` | Generate human-readable text for `summary.txt` |


## 8 Artifacts and Directory Conventions

Each time `optimize()` is run, the framework persists a complete set of audit artifacts under `output_dir`. All writes are **atomic**—SIGINT / process crash will not leave half-written files.

### 8.1 Directory Layout

```text
runs/<your-timestamp>/
├── result.json                  Complete OptimizeResult serialization (programmatic entry)
├── summary.txt                  Human-readable summary (see baseline → best at a glance)
├── config.snapshot.json         Complete snapshot of optimizer.json used this run (reproducible)
├── run.log                      Single-line status, CI parsing friendly
│
├── baseline_prompts/            Prompt snapshots before running (one .md per field)
│   ├── system_prompt.md
│   └── ...
│
├── best_prompts/                Optimal candidate from optimization (one .md per field)
│   ├── system_prompt.md
│   └── ...
│
└── rounds/                      Complete RoundRecord for each round
    ├── round_001.json
    ├── round_002.json
    └── ...
```

Role of each file:

| File / Directory | When Written | What It's For |
| --- | --- | --- |
| `result.json` | Optimization ends (including failure) | Most authoritative artifact for programmatic reading. Complete `OptimizeResult` serialization (see [§7.4](#74-optimizeresult--roundrecord-field-table) for details). **Field names are camelCase** |
| `summary.txt` | Optimization ends (only success) | Human-readable summary: `baseline → best` trend, metric breakdown, all best fields + character count, artifact directory index |
| `config.snapshot.json` | Optimization starts | Complete snapshot of `optimizer.json` used this run—directly use it later when wanting to "re-run this result" |
| `run.log` | Optimization ends | Single line: `<timestamp> status=... algorithm=... baseline=0.4 best=0.85 delta=+0.45 rounds=10 duration_seconds=120.5`; CI platform grep-friendly |
| `baseline_prompts/<name>.md` | Optimization starts | Content snapshot of each TargetPrompt field before running—**written regardless of `update_source` setting** (most important fallback artifact) |
| `best_prompts/<name>.md` | Optimization ends (only when result exists) | Optimal candidate prompts—when `update_source=False`, this is the most valuable artifact (awaiting manual review and synchronization) |
| `rounds/round_<NNN>.json` | Each round ends | Complete `RoundRecord` serialization (see [§7.4.2](#742-roundrecord-fields-one-per-round) for details); 3-digit zero-padded numbering for easy sorting |

### 8.2 Sentinel File: Letting Users Actively Stop Optimization

Source code: `_optimize_gepa_reflective.py:_build_stop_callbacks` end.

During optimization, the user manually `touch optimize.stop` under `output_dir`:

```bash
touch runs/<timestamp>/optimize.stop
```

The framework detects this file at the beginning of the next round and stops (`gepa.utils.FileStopper` implementation), `stop_reason="user_requested_stop"`. **Typical use case**: discovered it's already sufficient after running halfway / temporarily need to release LLM quota—more elegant than Ctrl+C, ensures current round completes and disk persistence is clean.

### 8.3 Atomic Disk Persistence Guarantee

**All artifacts use tmp + `os.replace` atomic write**—POSIX guarantees rename atomicity, when process is kill / power failure, either clean old file or clean new file exists in `output_dir`, **will never appear in half-written state**.

Source code: Two utility functions in `_agent_optimizer.py`:

- `_atomic_write_text(path, content)`: First write to `<path>.tmp`, then `os.replace(tmp, path)`
- `_mask_sigint`: Context manager, shields SIGINT during `_persist_artifacts` (avoid "second Ctrl+C interrupts finally disk persistence")

**Source prompt file write-back when `update_source=True`**: Uses `TargetPrompt.write_all`, also guarantees atomicity for **multi-field**—when any field write fails, all already successfully written fields roll back to pre-call content (see `write_all` contract in [§7.2](#72-targetprompt-api-table) for details).

> **Extreme fault tolerance**: If `os.replace` itself fails when `update_source=True` writes source files (e.g., target file's directory was concurrently deleted), the framework will **explicitly call `write_all(baseline)` to restore source files to pre-run content**, then re-raise the original exception—ensuring business never gets a "half-optimized" source file.


## 9 Want to Extend Yourself?

Source code main entry: `_optimize_registrations.py`. The framework supports three types of extensions through a **registration mechanism**, no need to fork the SDK.

### 9.1 Register New Algorithm

Source code: `_base_optimizer.py:BaseOptimizer` + `_optimize_registry.py:OPTIMIZER_REGISTRY`.

Write a `BaseOptimizer` subclass, implement `async def run(self, *, reporter=None) -> OptimizeResult`, register to `OPTIMIZER_REGISTRY`:

```python
from trpc_agent_sdk.evaluation._base_optimizer import BaseOptimizer
from trpc_agent_sdk.evaluation._optimize_registry import OPTIMIZER_REGISTRY
from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult


class MyOwnOptimizer(BaseOptimizer):
    async def run(self, *, reporter=None) -> OptimizeResult:
        # Your algorithm main loop. Base class has already injected:
        #   self.config         - OptimizeConfigFile (including evaluate / optimize two sections)
        #   self.call_agent     - Business agent adapter function
        #   self.target_prompt  - TargetPrompt instance
        #   self.train_dataset_path / self.validation_dataset_path
        #   self.callbacks / self.output_dir
        #   self.extra_stop_callbacks / self.extra_gepa_callbacks
        ...
        return OptimizeResult(...)


# Registration: second parameter must be BaseOptimizer subclass, otherwise register() throws TypeError
OPTIMIZER_REGISTRY.register("my_own_algo", MyOwnOptimizer)
```

Business side usage: Change `optimize.algorithm.name` in `optimizer.json` to `"my_own_algo"`, the framework finds your class through `OPTIMIZER_REGISTRY.get(...)` at startup, instantiates it, and runs `run()`.

**Note**: `GepaReflectiveAlgo.name` is currently `Literal["gepa_reflective"]`—**new algorithms need a new `pydantic.BaseModel` configuration class** (e.g., `MyOwnAlgo`), and modify `OptimizeConfig.algorithm` field to discriminated union (see `_optimize_config.py:OptimizeConfig` docstring for details).

### 9.2 Register Custom Stopper

Source code: `AgentOptimizer.optimize`'s `extra_stop_callbacks` parameter in `_agent_optimizer.py`.

Inject via `extra_stop_callbacks` at runtime—**no need to modify configuration file**:

```python
from trpc_agent_sdk.evaluation._optimize_gepa_reflective import _LabeledStopper


class MySloMonitorStopper:
    """Custom stopper: check external SLO monitoring system, stop when threshold is exceeded."""

    def __init__(self, slo_client):
        self._slo = slo_client
        self.last_triggered = False

    def __call__(self, gepa_state=None) -> bool:
        if self._slo.is_p99_breached():
            self.last_triggered = True
            return True
        return False


# Usage:
stopper = MySloMonitorStopper(slo_client)
result = await AgentOptimizer.optimize(
    ...,
    extra_stop_callbacks=[
        # Ordinary stopper: stop_reason displays as "completed"
        stopper,

        # When wanting stable stop_reason label, use _LabeledStopper wrapper:
        # _LabeledStopper(stopper, "slo_breach"),  # But "slo_breach" is not in StopReason Literal, pydantic will reject
    ],
)
```

**Interface contract** (see `_LabeledStopper`):

- Must have `__call__(self, gepa_state=None) -> bool` method
- `True` means stop
- Should have `last_triggered: bool` attribute for `_classify_stop_reason` to read

**Two behaviors of `stop_reason`**:

- Ordinary callable / custom class: `stop_reason` displays as `"completed"` when triggered (gepa doesn't know why you stopped)
- Wrapped with `_LabeledStopper(inner, label)`: `label` must be a legal value of `StopReason` Literal (see `_optimize_result.py`); need to extend Literal type when customizing new label

### 9.3 Register Custom Evaluation Callback

Source code: `AgentOptimizer.optimize`'s `extra_gepa_callbacks` parameter in `_agent_optimizer.py`.

Access gepa internal events through `extra_gepa_callbacks`—typical use: forwarding to dashboard / real-time monitoring metrics.

```python
class MyDashboardCallback:
    def on_proposal_end(self, *args, **kwargs) -> None:
        # Report to Grafana / WandB / internal monitoring
        ...

    # gepa silently ignores missing methods, just implement part of the protocol methods as needed


result = await AgentOptimizer.optimize(
    ...,
    extra_gepa_callbacks=[MyDashboardCallback()],
)
```

**Protocol constraints**: Each callback should implement several methods in `gepa.core.callback.GEPACallback` protocol (`on_iteration_start` / `on_proposal_start` / `on_proposal_end` / `on_valset_breakdown` / ...). **gepa silently ignores missing methods in callback**, so business can only implement those few that they care about.


## 10 FAQ

**Q: Ran once, `bestPassRate` in `result.json` is the same as `baselinePassRate`, `accepted` are all false—is it a bug?**

Not a bug. Optimization didn't find a candidate better than baseline—`status="SUCCEEDED"` + `finish_reason="no_improvement"` is the typical combination for this situation, `best_prompts` equals `baseline_prompts`. Possible reasons: baseline is already very good, `max_metric_calls` is too small to reach improvement point, training set and validation set have very different distributions, metric noise is too large (recommend increasing `num_runs`).

---

**Q: `update_source=True` crashed during run, were source prompt files corrupted?**

No. Two layers of protection: (1) When optimization fails (`status="FAILED"`), the framework simply doesn't call `write_all`; (2) Even if `write_all` itself fails, source files are atomically rolled back through tmp + `os.replace` (see [§8.3](#83-atomic-disk-persistence-guarantee) for details).

---

**Q: Can I modify `optimizer.json` mid-run?**

No. `optimizer.json` is loaded once at startup, subsequent modifications will not be read. Sentinel file `optimize.stop` is the only supported "runtime intervention" (see [§8.2](#82-sentinel-file-letting-users-actively-stop-optimization) for details).

---

**Q: Can I run with a very small training set (< 5 cases)?**

Yes, but effect is poor: (1) Reflection LM sees too few feedback samples, rewrite direction is unstable; (2) Small training set easily lets advanced configuration overfit (refer to [§4.8](#48)). Recommend at least 5~10 cases; consider manual tuning first when < 5.

---

**Q: How to handle retries when `call_agent` internally sends HTTP / RPC?**

Handle it yourself within `call_agent`. The framework does not do retries for business at LLM / service call layer—designed to keep `call_agent` as a black box. If the call fails, that case's evaluation score counts as 0, and the reflection LM will see the error message (refer to §5.2 Reflection LM feedback structure).

---

**Q: Can multiple `optimize()` runs happen simultaneously, sharing one `output_dir`?**

No. Multiple processes writing to one `output_dir`, atomic write constraint protects single files from being half-written, but **multiple processes overwrite files mutually**—`result.json` / `rounds/round_001.json`, etc. will step on each other. Use independent timestamp subdirectory for each run.

---

**Q: When using black-box `call_agent` mode, can I use metrics like `tool_trajectory_avg_score`?**

No. Black-box `call_agent` mode cannot obtain session traces / tool intermediate_data, the framework will fail-fast and reject at startup (see [§7.1](#71-agentoptimizeroptimize-parameter-table) startup check table for details). Switch to response-level metrics: `final_response_avg_score` / `llm_rubric_response` / `llm_final_response`.

---

**Q: After running with `update_source=False`, source prompts are still in place, but `target_prompt.write_all` was called repeatedly during the process?**

Yes. The optimizer main loop calls `write_all` every time a new candidate is generated to write the candidate to source files registered with `add_path`—this is to let the next `call_agent` call read the new prompt. **The `finally` phase will automatically `write_all(baseline_snapshot)` to roll back source files to baseline content** (source code: `cleanup_done` sentinel in `optimize` in `_agent_optimizer.py`). So after `update_source=False` finishes running, source files are **completely consistent with before running**—provided that `TargetPrompt.write_all` didn't throw an error during the rollback phase (in extreme cases when it throws an error, the framework will log a warning but will not affect `result.json` / `best_prompts/` artifact production).

---

**Q: How to "re-run" last optimization result?**

Re-run `runs/<ts>/config.snapshot.json`—it is the complete configuration snapshot from last time. But LLM output has randomness, even with consistent configuration you may get different best_prompts; fixing the `seed` field can reduce (not eliminate) this randomness. Must lock seed when A/B testing (refer to [§4.8](#48)).
