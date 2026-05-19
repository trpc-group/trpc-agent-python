# Blackbox CLI — 优化外部黑盒 CLI 的 prompt 文件

> **适用场景**：业务 agent 不是本框架的 `LlmAgent`，而是一个外部命令行工具（如 `claude` / `codex` / 自研 CLI），其行为由若干 prompt 文件（如 `CLAUDE.md` / `SKILL.md`）控制。本 example 演示通过 `subprocess` 把 CLI 当作完全黑盒的 agent，让 GEPA 优化它读取的 prompt 文件，整个过程不修改 CLI 代码、不绑定其内部 LLM client。阅读前请先熟悉 `quickstart/README.md` §2。

## 1 · 适用问题与设计目标

外部 CLI 工具的 prompt 工程特点：

- 工具实现细节（语言、运行时、内部 LLM client）对优化器完全黑盒
- prompt 通过特定文件名 / 目录结构约定加载（典型如 `CLAUDE.md` + `.claude/skills/<name>/SKILL.md`）
- CLI 启动时是独立进程，与优化器进程通过 stdin / stdout / 文件系统通信

`AgentOptimizer` 在此场景下扮演纯客户端角色：通过 `subprocess` 启动 CLI 进程、把测试 query 作为参数传入、收集 stdout、按 metric 评分。优化器与 CLI 进程间的唯一耦合点是 **CLI 读取的 prompt 文件**——优化器写入新候选，CLI 在下一次启动时自动读取新内容。

| 输入 | 输出 |
| --- | --- |
| 一个支持"启动时读 prompt 文件"的外部 CLI 工具 | 满足 metric 阈值的最优 prompt 候选 |
| CLI 接受 query 作为参数 / stdin 的协议 | CLI 二进制完全不变，仅磁盘上 prompt 文件被改写 |

### 本 example 演示的最小用例

| 维度 | 值 |
| --- | --- |
| 业务任务 | 中国城市信息查询（输入城市名，输出严格 JSON `{city, country, is_capital}`） |
| 黑盒 CLI | `trpc-claudecode`（腾讯内部 Claude Code 适配层，OpenAI 兼容协议指向 GLM-5.1） |
| 优化目标 | `workspace/CLAUDE.md` + `workspace/.claude/skills/city-info/SKILL.md` 共两个文件 |
| 验证指标 | `final_response_avg_score`（exact 匹配 stdout 规范化后的 JSON） |
| 训练 / 验证规模 | 5 条 / 3 条 |

## 2 · 术语对照

仅列出本 example 引入的新概念。基础术语见 `quickstart/README.md` §2。

| 术语 | 含义 |
| --- | --- |
| **subprocess 调用** | 用 `asyncio.create_subprocess_exec` 启动子进程，传 query 作 argv，读 stdout。子进程独立进程，与优化器进程无任何资源共享。 |
| **CLI 工作目录（workspace）** | CLI 启动时通过 `--add-dir <path>` 指定的目录，CLI 自动从中加载 prompt 文件。本 example 中即 `workspace/`。 |
| **stdout 规范化** | 用 `json.loads + json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 把 LLM 自由文本输出转换为唯一字符串形态，使 metric 直接走文本精确匹配，无需 LLM judge。 |
| **环境变量映射** | 把通用的 `TRPC_AGENT_*` 三件套映射成 CLI 期望的 `TRPC_CLAUDECODE_*` 三件套，避免用户为 CLI 单独配置 OAuth 或 API key。 |

## 3 · 运行示例

### 3.1 依赖检查

```bash
which trpc-claudecode      # 应输出可执行路径
trpc-claudecode --version  # 验证可正常启动
```

CLI 二进制为外部依赖，本 example 不通过 pip 安装。其他自有 CLI 替换 `CLI_BINARY` 常量即可。

### 3.2 安装 SDK 可选依赖

```bash
pip install -e ".[optimize]"
```

### 3.3 配置环境变量

```bash
export TRPC_AGENT_API_KEY="<your-key>"
export TRPC_AGENT_BASE_URL="<your-endpoint>"
export TRPC_AGENT_MODEL_NAME="<your-model>"
```

`call_agent` 内部会自动把这三个变量映射成 `TRPC_CLAUDECODE_BASE_URL` / `TRPC_CLAUDECODE_API_KEY` / `TRPC_CLAUDECODE_MODEL`，并附加 GLM-5.1 推荐的 `CLAUDE_CODE_AUTO_COMPACT_WINDOW=165000` / `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=85`。

**无需 `trpc-claudecode auth login`，无需 `ANTHROPIC_API_KEY`**。

### 3.4 启动

```bash
python examples/optimization/blackbox_cli/run_optimization.py
```

### 3.5 产物结构

```
runs/<timestamp>/
├── result.json
├── summary.txt
├── baseline_prompts/      运行前的 CLAUDE.md / SKILL.md 快照
├── best_prompts/          val 集得分最高的候选
└── rounds/

workspace/                  CLI 工作目录（update_source=False 时自动回滚到 baseline）
├── CLAUDE.md
└── .claude/skills/city-info/SKILL.md
```

## 4 · 架构与数据流

```
[run_optimization.py]
    │
    ├── TargetPrompt
    │     .add_path("claude_md", workspace/CLAUDE.md)
    │     .add_path("skill_md",  workspace/.claude/skills/city-info/SKILL.md)
    │           │  GEPA 每轮把候选写入对应文件
    │           ▼
    │   workspace/{CLAUDE.md, .claude/skills/city-info/SKILL.md}
    │           │  CLI 启动时通过 --add-dir 自动加载
    │           ▼
    └── call_agent(query):
            ├── _build_cli_env()                          映射 env 三件套
            ├── asyncio.create_subprocess_exec(
            │     "trpc-claudecode", "--print",
            │     "--add-dir", workspace/,
            │     "--dangerously-skip-permissions",
            │     query,
            │   )
            ├── proc.communicate(timeout=90s)
            └── _normalize_response(stdout)               紧凑 JSON 字符串
```

### 4.1 文件清单

| 文件 | 角色 | 接入自有业务时的修改方向 |
| --- | --- | --- |
| `run_optimization.py` | 优化器入口，注册 `TargetPrompt` 两个文件 | 调整 `CLAUDE_MD_PATH` / `SKILL_MD_PATH` 至自有 CLI 期望的文件路径 |
| `agent/call_agent.py` | subprocess 调用 + env 映射 + stdout 规范化 | **核心改造点**：替换 `CLI_BINARY` / 命令行参数 / env 映射规则 |
| `workspace/CLAUDE.md` | CLI 启动时读取的主 prompt（GEPA 写入目标） | 替换为业务 baseline 起点 |
| `workspace/.claude/skills/city-info/SKILL.md` | CLI 启动时读取的 skill 描述（GEPA 写入目标） | 单文件优化时整体删除并去掉 `add_path("skill_md", ...)` |
| `optimizer.json` | 算法 + metric 配置 | 调整阈值 / 停止条件 |
| `train.evalset.json` / `val.evalset.json` | 数据集 | 替换为业务用例（reference 字段需经过 `_normalize_response` 同等处理） |

## 5 · 关键配置

### 5.1 推荐参数取值

```jsonc
{
  "optimize": {
    "eval_case_parallelism": 1,           // 黑盒 CLI 串行最稳；并发可能踩 CLI 进程并发问题
    "algorithm": {
      "module_selector": "round_robin",
      "frontier_type": "instance",         // CLI 慢/贵，instance 收敛快不浪费调用
      "use_merge": false,                  // 避免 metric_calls 浪费在 merge 上
      "reflection_minibatch_size": 3,
      "max_metric_calls": 24,              // CLI 一次约 10s，24 次约 4 分钟
      "score_threshold": 1.0
    }
  }
}
```

| 字段 | 选择理由 |
| --- | --- |
| `eval_case_parallelism=1` | CLI 子进程并发存在不确定性（共享文件锁、stdout 缓冲、子进程数上限），串行最稳 |
| `frontier_type=instance` | CLI 调用慢且贵，instance 前沿在小规模评估下收敛更快 |
| `use_merge=false` | merge 需要额外 metric calls；黑盒 CLI 场景下应集中预算在反思上 |
| `score_threshold=1.0` | 黑盒结构化输出的目标是完美匹配 |

### 5.2 CLI 子进程超时

`agent/call_agent.py` 中 `CLI_TIMEOUT_SEC=90.0`：单次 CLI 调用超过 90 秒被强制 kill 并抛 `RuntimeError`，避免某次 CLI 卡死拖垮整轮评估。业务 CLI 平均耗时不同需相应调整。

## 6 · 设计要点

### 6.1 为什么不用 `--system-prompt` 注入 prompt

CLI 通常支持 `--system-prompt "<text>"` 一次性注入字符串。但本 example 使用 `--add-dir <workspace>` 让 CLI 自己从目录加载 prompt 文件，原因：

- **支持多文件优化**：`CLAUDE.md` + `SKILL.md` 是 CLI 约定结构，多文件作为独立 `TargetPrompt` 字段才能让 GEPA 选择性改写其中之一
- **与 CLI 原生工作流对齐**：业务真实使用 CLI 时也是把 prompt 写到工作目录、CLI 自动发现，本 example 路径与之一致

### 6.2 为什么 stdout 要做 `_normalize_response`

LLM 输出常带尾部空格、JSON 前后多吐字符等噪音。`_normalize_response`：

1. 用正则定位首个 `{...}` 块
2. `json.loads` + `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 消除空格 / key 顺序差异

→ baseline 与候选 prompt 的输出对齐到唯一字符串形态，可直接走 `final_response_avg_score(text.match=exact)`，**评测层完全不需要 LLM judge**，CI 上快、稳、可重复。

### 6.3 subprocess 与 async 资源

子进程是独立 OS 进程，不与优化器进程共享 async 资源（事件循环、连接池等），是黑盒 CLI 模式的隐性优点：业务 CLI 的内部并发模型对 SDK 完全不可见也无需对齐。

## 7 · 常见问题

**Q：CLI 启动慢（每次几秒），怎么办？**
A：尽量调小 `max_metric_calls`、调大 `reflection_minibatch_size`（一次反思看更多 case 但少跑几轮）。彻底改造需将 CLI 改造为常驻服务，参考 `http_service/` example。

**Q：CLI 输出不是 JSON 怎么办？**
A：根据业务 metric 类型选择不同规范化策略。若 metric 是 `final_response_avg_score(text.match=contains)`，可直接 strip stdout；若需要严格匹配，按业务输出形态改写 `_normalize_response`。

**Q：CLI 进程意外退出（returncode != 0）会怎样？**
A：`_run_cli` 会抛 `RuntimeError` 携带 stderr 前 400 字符，异常传播到优化器，导致当前 case 评测失败、当前候选可能被拒绝。

**Q：`workspace/` 在被优化期间会不会被多个 CLI 进程并发读写？**
A：`eval_case_parallelism=1` 时不会。若强行调高并发，多个 CLI 实例可能同时读取被写入的 prompt 文件，导致评测结果不一致——这是设置 `eval_case_parallelism=1` 的根本原因。

**Q：跑完后想自动把 best 写回 `workspace/`？**
A：在 `run_optimization.py` 中将 `update_source=False` 改为 `True`。

## 8 · 接入自有 CLI 的步骤

1. **替换 `CLI_BINARY`**：`agent/call_agent.py` 中改为业务 CLI 可执行路径
2. **调整命令行参数**：`_run_cli` 中的 argv 数组按业务 CLI 协议改造（argv 传 query / stdin 传 query / `--query xxx` 形式等）
3. **替换 env 映射**：`_build_cli_env` 改为业务 CLI 期望的环境变量（或如业务 CLI 已有 OAuth 流程，删除该映射并提示用户先完成登录）
4. **修改 `TargetPrompt`**：`run_optimization.py` 中调整 `add_path` 至业务 CLI 期望的 prompt 文件路径
5. **替换 prompt baseline**：业务 baseline 内容写入对应文件
6. **替换数据集**：`train.evalset.json` / `val.evalset.json`，注意 reference 字段需匹配 `_normalize_response` 处理后的形态
7. **运行并观察**：根据 `summary.txt` 决定是否调参
