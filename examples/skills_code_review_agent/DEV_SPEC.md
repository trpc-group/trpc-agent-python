# DEV_SPEC — 自动代码评审 Agent（issue #92）

> 本规格是 `examples/skills_code_review_agent/` 的唯一真相源（single source of truth）。
> auto-coder 按第 6 章排期逐任务实现；任何设计分歧以本文档为准。
> 决策背景见 `自动代码评审_agent_e5c4eb90.plan.md`（计划书 v2，三轮拷问结论）。
> 设计对照与改进依据见仓库根目录 `implementation_plan.md`；该文档用于解释取舍，若与本规格冲突，以本规格为准。

## 1. 项目概述

### 1.1 背景

基于 tRPC-Agent-Python SDK 的 Skills、CodeExecutor 沙箱、SQL 存储、Filter 治理和 Telemetry 能力，构建一个可验证的自动代码评审 Agent 原型：输入 git diff / PR patch / 本地变更目录，通过 code-review Skill 加载规则与脚本，经 Filter 前置拦截后进入沙箱执行检查，把发现的问题按严重级别/文件/行号/证据/修复建议结构化输出，并将审查任务、拦截记录、监控摘要与结果写入数据库。

难点不是「让 LLM 评论代码」，而是把 Skills、沙箱执行、数据库、Filter 治理、审查规则、结果结构化、监控审计和安全边界串成一个可验证系统。

### 1.2 核心决策（已锁定，不再讨论）

1. **双入口、单核心**：确定性 `ReviewPipeline` 是唯一检测链路；CLI/dry-run/测试直接调用它，`LlmAgent + SkillToolSet` 作为第二入口经 SkillRepository 加载 skill 后触发同一 pipeline 并增强报告摘要。两个入口零逻辑复制。
2. **检测 100% 确定性规则**：所有检出不依赖模型。LLM 仅做报告增强（解释上下文、优化修复建议、生成摘要与复核提示），不得增删 finding，也不得改写 finding 的 identity、severity、confidence、bucket、dedup 结果。默认 `--model-mode fake`，`real` 仅可显式开启；检测与评测默认不因环境中存在 Key 而自动切换 real。
3. **规则引擎 Python-only**：只深耕 Python 的正则 + AST 规则；敏感信息类规则基于通用正则 + Shannon 熵，天然跨语言。
4. **沙箱安全边界不妥协**：规则脚本默认经 SkillRepository stage 进沙箱执行；沙箱被拒/超时/失败后只记录、不回退宿主执行。CLI 生产默认严格 `container`（无 Docker 直接报错，不静默降级），`--sandbox local` 仅显式开启。`--dry-run` **只**代表 fake model，**不**改变沙箱语义。无 Docker 的本地/CI 跑通路径是显式 `--sandbox local`（或 `evaluate.py` 默认 local），不是 dry-run 偷偷降级；pytest 单测可注入 fake runtime，与 dry-run/evaluate 不是同一条路径。
5. **单一真相源**：diff 解析器、规则引擎、密钥正则表全部位于 `skills/code-review/scripts/lib/`（纯标准库）；沙箱内直接执行这份代码，宿主 local 模式经 importlib 加载同一文件。
6. **失败即数据 + 脱敏后落库**：超时、非零退出、输出截断、Filter 拦截全部落库为记录行，评审任务永不崩溃。能出报告则收敛为 `completed_with_warnings`；只有输入解析失败、DB 初始化失败、关键写库失败或报告无法生成才标 `failed`。原始 diff 默认不落库（见 2.8）。
7. **原始输入只跨受控信任边界**：敏感信息检测必须读取原始内容，因此不得在检测前破坏性脱敏；原始 diff 只可短暂存在于受控宿主内存、任务临时目录和隔离 workspace，不得进入日志、Telemetry、LLM、数据库或最终报告。沙箱输出、宿主后处理和持久化出口逐层脱敏（见 2.8、5.4）。
8. **JSON 是报告规范源**：`review_report.json` 经 schema 校验、最终泄漏扫描和原子写入后，Markdown 只能由该 JSON 确定性渲染；数据库保存同一报告对象的脱敏内容或摘要，不得分别拼装三套结果。

### 1.3 验收标准（8 条，最终必须全绿）

| # | 验收标准 |
|---|---------|
| AC1 | 8 条公开 diff 样本全部可运行并生成审查报告 |
| AC2 | 隐藏样本高危检出率 ≥80%、误报率 ≤15%（以带标注公开代理语料测 P/R 佐证） |
| AC3 | 数据库完整记录 task、sandbox run、finding、report，支持按 task id 查询 |
| AC4 | 沙箱有超时控制和输出大小限制；超时或失败不导致评审任务崩溃 |
| AC5 | 敏感信息脱敏检出率 ≥95%，报告和数据库中无明文 API Key/token/password |
| AC6 | dry-run / fake model 模式完整评审流程耗时 ≤2 分钟（统一测量口径：`evaluate.py` 默认 path，即 model=fake + sandbox=local；CLI 等价命令为 `--dry-run --sandbox local`） |
| AC7 | 高风险脚本先经 Filter 决策；deny / needs_human_review 不进入沙箱执行 |
| AC8 | 报告包含 findings 摘要、严重级别统计、人工复核项、Filter 拦截摘要、监控指标、沙箱执行摘要和可执行修复建议 |

### 1.4 范围排除

不做：A2A/AG-UI 服务化、RAG 知识库、跨会话长期记忆、SARIF 输出、多语言规则均衡覆盖、在线评测平台。理由：不在验收标准内，接入会显著增加配置与测试面。

## 2. 功能规格

### 2.1 输入解析（R3）

支持四种输入，统一解析为 `ChangeSet`：

- `--diff-file <path>`：unified diff / PR patch 文件
- `--repo-path <dir>`：git 工作区变更（staged + unstaged，同时可读全文件内容）
- `--files <a.py> <b.py>`：无 baseline 的文件快照列表，执行**全文件扫描**；每个文件记为 `status=snapshot`、`review_scope=full_file`，整份内容都属于候选行
- `--fixture <name>`：内置测试样例；fixture 必须声明其载荷类型，diff fixture 保留真实 old/new hunk，full-file fixture 才按 `--files` 的 snapshot 语义处理

**四种输入互斥**：同一次调用只允许指定一种，CLI 层校验，同时给出多个直接报错退出，避免多输入源结果冲突的模糊状态。

**输入形态不等价**：`--repo-path` 的 tracked 变更和 `--diff-file` 是增量审查，changed-line 过滤能隔离历史遗留问题；`--files` 没有旧版本可比较，是显式全量扫描，AST 可报告文件任意行上的既有问题。调用方若需要增量语义必须提供 repo 或 diff，不能期待 `--files` 自动推断实际改动。报告 input summary 必须显示 `review_scope`，评测不得把 full-file 与 changed-lines 结果当作同一口径直接比较。

**领域模型契约**（实现、报告与测试共用；路径统一为 `/` 分隔的仓库相对路径）：

| 模型 | 必须字段 |
|------|---------|
| `ChangeSet` | `source_kind(diff_file\|repo_path\|files\|fixture)`、`input_sha256`、`files`、`file_count`、`hunk_count`、`additions`、`deletions`、`parse_warnings` |
| `FileChange` | `old_path`、`new_path`、`normalized_path`、`status(added\|modified\|deleted\|renamed\|snapshot)`、`review_scope(changed_lines\|full_file\|deleted_lines\|skipped)`、`is_binary`、`hunks`、`old_changed_lines`、`new_changed_lines`、`full_text(str\|None)`、`analysis_mode(ast_validated\|diff_heuristic\|skipped)` |
| `Hunk` | `old_start`、`old_count`、`new_start`、`new_count`、`context_lines`、`added_lines`、`deleted_lines`、`old_to_new_line_map` |

`Hunk` 字段不可缺失或取 `None`，退化状态固定采用 unified diff 的 `0,0` 语义：

| 场景 | old 侧 | new 侧 | changed lines | `old_to_new_line_map` |
|------|--------|--------|---------------|-----------------------|
| 新增文件 / snapshot（N 行） | `old_start=0, old_count=0` | `new_start=1, new_count=N` | old=`[]`，new=`1..N` | `{}` |
| 删除文件（N 行） | `old_start=1, old_count=N` | `new_start=0, new_count=0` | old=`1..N`，new=`[]` | `{}` |
| 普通 hunk | 取 diff header 的整数 | 取 diff header 的整数 | 分别记录 `-/+` 行 | 只映射未修改 context 行 |

空文件新增/删除若 diff 只有元数据而没有内容 hunk，则 `hunks=[]`，不构造虚假的零长度 hunk。替换行没有可靠的一一语义关系，不得写入 `old_to_new_line_map`。

finding 的 `file` 使用 `normalized_path`：新增/修改/rename/snapshot 取新路径，删除取旧路径。`line` 默认表示新侧行号，扩展字段 `line_side` 默认为 `new`；仅 secrets 规则可对删除侧原始凭据生成 `line_side=old`、`line=<旧行号>` 的 finding，提示密钥可能仍存在于补丁/历史中并建议轮换。普通代码规则不报告已经删除的代码。不得用 `line=0` 或临近新行伪造删除侧位置。

**--repo-path 的 git 变更获取方式**（定死，避免 staged/unstaged 合并陷阱）：

1. 用一条 `git diff HEAD` 获取「工作区当前状态 vs 上一次 commit」的完整 diff——天然合并 staged + unstaged，禁止分别跑 `git diff` 和 `git diff --cached` 再手动合并（同一文件两份 diff 的 hunk 会重叠冲突）。
2. `git diff HEAD` 不含 untracked 新文件，须额外跑 `git ls-files --others --exclude-standard` 获取 untracked 列表，将其按真实 `status=added`、`review_scope=full_file` 处理；内容读取与 synthetic hunk 构造可复用 `--files`，但不得把 untracked 的 status 写成 snapshot。
3. 所有 Git 调用必须使用 argv 数组并固定工作目录，禁止 `shell=True`；路径在读取与 staging 前必须 `resolve` 并验证仍位于 repo 根目录内，拒绝指向仓库外的 symlink、junction 或其他重解析点。

**--repo-path 默认忽略规则**（可经 ReviewConfig 配置，默认值如下）：

- `.gitignore` 内文件：自动忽略——`--exclude-standard` 已实现该语义，无需额外过滤。
- 二进制文件：忽略。diff 内 binary 变更按边界跳过；untracked 文件做二进制嗅探（内容含 NUL 字节即跳过）。
- 虚拟环境与构建目录**显式兜底清单**（不依赖用户是否配好 .gitignore）：`.git/`、`.venv/`、`venv/`、`node_modules/`、`build/`、`dist/`、`__pycache__/`、`*.egg-info/`、`.tox/`、`.mypy_cache/`、`.pytest_cache/`。
- untracked 文件的规则适用范围**按类别区分，不得只收 .py**：Python 类规则（security/async/resource/db/missing-tests）仅作用于 `.py` 文件；secrets 规则作用于**所有文本文件**（含 `.env`、`.yaml`、`.json`、`.ini`、`.toml`、`.txt` 等）——明文密钥最常出现在未跟踪的配置文件里，只扫 Python 会直接威胁 AC5。
- 输入限额集中在 `ReviewConfig`：`max_input_file_bytes=1 MiB`、`max_input_files=500`、`max_input_bytes=10 MiB`、`max_diff_lines=50,000`。单文件超限跳过并记 warnings；文件数、总字节数或总行数超限在 staging 前由 Filter 标 `needs_human_review`，不得先复制或执行后再依赖超时收拾。
- 宿主仓库不得可写挂载进沙箱；只复制审查所需的最小输入集到任务 workspace。`--files` 仅接受显式命名、位于当前输入根目录内的普通文件，同样执行 realpath 与限额检查。

diff 解析必须覆盖边界：rename、binary、CRLF、`\ No newline at end of file`、删除文件、新增文件。新增文件的 unified diff 若包含从第 1 行开始且无缺口的全部新增内容，可重建 `full_text` 并启用 AST；否则 `full_text=None`，按 diff heuristic 分析。

**原始输入边界**：解析器和沙箱内 secrets 规则允许在受控内存/任务 workspace 中读取原始内容，以完成真实密钥检测；解析期间不得记录代码行、环境变量或密钥值。进入 LLM、日志、Telemetry、数据库、finding evidence、sandbox 摘要和报告前必须脱敏。任务 workspace 在 `finally` 中清理，清理失败只记录不含敏感路径/内容的 warning。

### 2.2 CR Skill（R1）

`examples/skills_code_review_agent/skills/code-review/`（自包容，随示例目录整体拷贝可用）：

- `SKILL.md`：YAML frontmatter（name=code-review）+ 用法说明 + 工作流描述
- `rules/`：6 类规则文档（security / async-errors / resource-leak / missing-tests / secrets / db-lifecycle），每篇含规则清单、rule_id、severity、置信度、`requires_full_file` 标记、示例
- `references/security-boundaries.md`：原始输入信任域、禁止路径、网络、环境变量、预算、脱敏和失败语义的自检说明
- `scripts/manifest.json`：机器可读执行清单，是脚本 allowlist、参数模板和执行预算的唯一判定源
- `scripts/parse_diff.py`：沙箱内 diff 解析入口（读 `work/inputs/diff.json`，输出解析结果到 `out/`）
- `scripts/run_checks.py`：沙箱内规则检查入口（输出 findings JSON 到 `out/findings.json`）
- `scripts/lib/`：纯标准库实现——`diff_parser.py`、`rule_engine.py`、`rules_security.py`、`rules_async.py`、`rules_resource.py`、`rules_db.py`、`rules_tests.py`、`secret_rules.py`（检/脱同源正则表 + 熵检测）

`manifest.json` 每个条目至少包含 `script_id`、`entrypoint`、`sha256`、允许参数的名称/类型/枚举/长度、`timeout_seconds`、`max_output_bytes`、`requires_network`。Agent/pipeline 只能请求 `script_id + structured_args`，Filter 根据 manifest 生成 argv；禁止提交任意 shell 字符串。Skill staging 后必须校验 entrypoint realpath 位于 Skill 根目录内且摘要一致。`SKILL.md` 只解释工作流并引用 manifest，不承担机器授权。

### 2.3 规则引擎（6 类，Python-only）

| 类别 | category 值 | 检测手段 | 置信度 |
|------|------------|---------|--------|
| 安全（SQL 注入 f-string 拼接、命令注入 os.system/subprocess shell=True、eval/exec） | security | 正则 + 全文件可得时 AST 确认 | AST 确认 ≥0.9；纯 diff 正则 0.7–0.85 |
| 敏感信息（AWS AKIA、GitHub PAT ghp_/github_pat_、Slack、OpenAI sk-、JWT、PEM 私钥、DB 连接串、赋值型 password/token/secret） | secrets | 检/脱同源正则表 + Shannon 熵 | ≥0.9 |
| 异步错误（async def 内 time.sleep、协程未 await、事件循环内阻塞 IO） | async-errors | 正则 + AST | 0.6–0.9 |
| 资源泄漏（open/aiohttp ClientSession/socket 未 with 或未 close） | resource-leak | 正则 + hunk 跨行 + AST | 0.6–0.9 |
| DB 生命周期（连接未关、事务未 commit/rollback、游标泄漏） | db-lifecycle | 同上 | 0.6–0.9 |
| 测试缺失（新增/修改非测试源码但变更集内无对应 test 文件变化） | missing-tests | 变更集形状启发式 | 锁 0.5–0.8，永进 needs_human_review |

候选侧别：security/async-errors/resource-leak/db-lifecycle/missing-tests 只分析新侧内容；secrets 同时扫描新增/上下文输出中的新侧内容和被删除的旧侧内容。旧侧命中表示凭据可能已经进入补丁或 Git 历史，finding 必须明确 `line_side=old`，修复建议以轮换/吊销为主，不能描述为“当前文件仍硬编码该值”。

**规则覆盖边界与盲区策略**（实现和评测口径都以此为准）：

| 类别 | 规则覆盖能力 | 已声明的盲区（不检出，不算漏报缺陷） |
|------|------------|-----------------------------------|
| secrets | 强：正则 + 熵检测，跨语言；公开代理语料脱敏检出率目标 ≥95% | 拆分/编码/运行时拼接、自定义短 token、私有格式可能漏检 |
| missing-tests | 文件级结构比对，不涉及代码语义；仅作为人工复核提示 | 非标准测试目录、动态生成测试、集成测试映射、已有覆盖关系可能误判 |
| resource-leak | 较强：AST 可识别 open/connect 后无 close/with 的经典模式 | 跨函数传递的句柄、仅异常路径泄漏（需控制流分析） |
| db-lifecycle | 较强：识别已知 DB 库 API（connect/commit/rollback）调用模式 | 连接池误用、嵌套事务（需调用上下文理解） |
| async-errors | 中等：AST 可查协程创建后未 await/未传 gather/create_task 的直接模式 | 变量先赋值、之后才 await（需数据流分析） |
| security | 中等：已知危险 API（直接/限定名 eval/exec、os.system/popen、subprocess shell helper、SQL f-string/拼接/format/%）模式 | 运行时函数别名、变量传播得到的 shell 参数、动态属性名和业务逻辑漏洞（权限绕过、认证逻辑错误） |

**盲区处理原则**：规则覆盖不到的场景保持为声明盲区，本期不用 LLM 顶上。理由：一旦 LLM 参与检出判断，检出率/误报率（AC2）无法稳定复现。盲区清单写入各规则文档和 README；CI 硬门禁语料只覆盖明确声明支持的模式，另设 blind-spot stress corpus 作为观测项，记录漏检但不冒充正式门禁通过。P/R/F1 只统计 findings 桶。

**按规则类别降误报**：security/async-errors/resource-leak/db-lifecycle 等代码结构规则可忽略仅出现在注释、docstring 或普通字符串中的 API 名称；secrets 规则必须扫描字符串字面量、配置文件和注释，注释中的疑似示例密钥只能根据占位符特征降置信或进入人工复核，不得统一跳过。diff 上下文行（非 `+/-` 行）不作为 finding 主定位行。

**AST 退化策略（经 `requires_full_file` 规则元信息实现）**：每条规则在元信息中声明 `requires_full_file: true|false`。unified diff 只含 hunk 片段，对残缺代码跑 `ast.parse()` 大概率语法错误，因此：

- 全文件内容可得（--repo-path / --files，或可完整重建的新增文件 diff）：`requires_full_file=true` 的 AST 规则正常启用。对 `review_scope=changed_lines`，AST 节点范围必须与 `new_changed_lines` 相交才可产出当前审查 finding；上下文可以引用未修改行，但主定位必须锚定变更行。对 `review_scope=full_file`（--files、untracked、真实新增文件），`new_changed_lines` 覆盖全文，交集约束按设计退化为全量扫描，允许报告任意行，报告必须明确标注该 scope。`review_scope=deleted_lines` 不运行普通 AST 代码规则。
- 纯 diff 输入（--diff-file 且无原始文件可读）：`requires_full_file=true` 的规则**自动跳过 AST 路径**（禁止尝试解析残缺片段导致异常），仅保留正则 + 行级启发部分，置信度整体下调一档（0.7–0.85）；无正则等价物的纯 AST 规则直接不产出，或产出低置信项进 needs_human_review。
- 全文件 AST 解析失败：记录 parse warning，将该文件的 `analysis_mode` 降为 `diff_heuristic`，继续运行其他规则，不得终止整次 review。

### 2.4 结构化 finding（R4）

必须字段：`severity`（critical/high/medium/low/info）、`category`、`file`、`line`、`title`、`evidence`（已脱敏）、`recommendation`、`confidence`（0–1）、`source`（rule-engine/ast/heuristic）。扩展字段：`line_side`（new/old，默认 new）、`rule_id`、`bucket`、`dedup_key`、`extra`（JSON，含 also_matched）。删除侧仅允许 secrets 使用 `line_side=old`；评测匹配仍使用 `(file,line,category)`，需要区分侧别的边界测试额外断言 `line_side`。

### 2.5 去重与降噪（R6）

- 去重键：三元组 `(file, line, category)`。重复候选依次按 severity、confidence、evidence 具体程度选主项，其余 rule_id 去重后按稳定字典序合入 `extra.also_matched`。最终输出固定按 severity、file、line、category、rule_id 排序，保证相同输入重复运行得到相同 JSON。
- 四桶路由（边界不得重叠）：`0.80 ≤ confidence ≤ 1.00` → `findings`；`0.50 ≤ confidence < 0.80` → `needs_human_review`；`0.00 ≤ confidence < 0.50` → `suppressed`（仅保存脱敏审计计数和原因摘要，不进报告主体）。
- `warnings` 桶与代码问题分离，专放运行告警：沙箱失败、输出截断、Filter 拦截、规则执行异常、local 沙箱降级提示。
- confidence 必须来自可复现的证据强度；不得为满足 Recall 指标按 severity 设置置信度保底。明确危险 API、完整 AST 结构或强格式密钥可以自然得到高置信，模糊正则与上下文不足的候选必须进入人工复核或 suppressed。

### 2.6 沙箱执行与安全边界（R2 + R7）

- 三后端：`create_sandbox_runtime("container"|"cube"|"local")`，默认 `container`。SDK `WorkspaceCapabilities.network_allowed` 仅作为运行时可能具备网络能力的粗粒度描述，不得当作当前实例网络已开启或已断开的证明；Filter 必须检查最终生效且可验证的网络配置。`container` 仅在确认实际 `network_mode=none` 时放行；当前 SDK 未提供可验证 Cube 出口策略的接口，因此 `cube` 在本期默认 deny，只有配置受信任模板且系统能验证其无出口网络策略或受控网关约束时方可放行，用户口头或布尔确认不构成证明；`local` 仅用于显式选择的 dev 降级，并在 warnings 记录隔离与网络策略不可强制证明的告警。
- 预算（ReviewConfig 默认值，全部可配置）：

| 配置项 | 默认值 |
|--------|--------|
| max_sandbox_runs | 10 |
| per_run_timeout_seconds | 30（覆盖 SDK skill_run 的 300s 默认） |
| sandbox_time_budget_seconds | 90 |
| review_deadline_seconds | 110 |
| max_output_bytes_per_run | 1 MiB |
| max_output_bytes_per_review | 2 MiB |
| network_policy | deny |

- `network_policy=deny` 是本项目本期的 fail-closed 安全决策，不是 SDK 对 Cube 的使用限制。题目允许对白名单网络做受控放行，但本期所有预注册脚本均为 `requires_network=false`，不实现仅凭用户确认或未验证配置开放网络的旁路。
- Filter 必须在每次执行前做“先拒后跑”的预算预检：预估本次运行加入后是否超过次数、单次超时、单次输出或总时间预算；不满足时返回 deny/needs_human_review，不得先执行再只依赖 timeout 截止。`sandbox_time_budget_seconds=90` 为沙箱累计预算，给解析、落库和报告预留时间以满足 120 秒总门禁。
- 环境变量「构造而非透传」：仅 LANG/LC_ALL/PYTHONUNBUFFERED 等无敏感值变量允许传入；PATH/PYTHONPATH 由应用在沙箱内构造；WORKSPACE_DIR/SKILLS_DIR/WORK_DIR/OUTPUT_DIR/RUN_DIR 由 runtime 注入；API Key、token 及其余宿主环境变量一律不传。
- 失败记录：超时、非零退出、输出截断、OSError 全部落 `cr_sandbox_run` 行（status/exit_code/timed_out/error_type/脱敏摘录）。

### 2.7 Filter 治理（R8）

`SandboxGovernanceFilter` 基于 SDK `BaseFilter` / `run_filters` 链，按固定顺序执行前置检查：

1. 接收 `script_id + structured_args`，拒绝任意命令字符串、shell 元字符和未知脚本。
2. 从 `scripts/manifest.json` 解析 entrypoint 与参数模板；校验参数类型、枚举、长度、重复参数和未知参数。
3. 校验 staged entrypoint realpath 位于 Skill 根目录内，文件 SHA-256 与 manifest 一致；不一致直接 deny。
4. 校验所有输入/输出路径均位于任务 workspace 内，禁止绝对宿主路径、`..`、symlink/junction 逃逸。
5. 校验 `requires_network=false` 与 runtime 网络能力；本期 manifest 中所有脚本均不得请求网络。
6. 校验环境变量仅来自 2.6 的构造白名单，值不得包含密钥模式。
7. 预检运行次数、单次超时、单次/总输出和剩余时间预算。
8. 高风险内容模式扫描作为纵深防御（如脚本内容出现 `rm -rf`、`curl|sh` 或动态拉取执行，即使脚本已注册也 deny）。
9. runtime 有效网络状态校验（不得只读取 `WorkspaceCapabilities.network_allowed` 作决定）：
   - `cube`：当前 SDK 只能说明运行时可能允许网络，不能证明具体实例无出口；本期 **默认 deny**。仅当受信任模板或受控网关已配置，且治理层能取得机器可验证的无出口/目的地约束证明时方可 allow；仅有用户确认时仍不得执行。
   - `local`：宿主进程无法提供与沙箱等价的网络隔离证明；仅在用户已**显式**传 `--sandbox local`（或 evaluate 显式选择 local）时治理门 **allow**，同时必须把「隔离与网络策略不可强制证明」降级告警写入 warnings（不得静默当成生产等价）。
   - `container`：创建参数默认 `network_mode=none`，但该值可被 `host_config` 覆盖；Filter/工厂必须验证本次实际生效配置仍为 `none` 后才 allow，被覆盖或无法验证时 deny。

`FilterAction ∈ {ALLOW, DENY, NEEDS_HUMAN_REVIEW}`；DENY / NEEDS_HUMAN_REVIEW 短路，不进沙箱、不回退执行，拦截原因先脱敏再写入报告和 `cr_filter_event`。该枚举与 finding 的 `FindingBucket.NEEDS_HUMAN_REVIEW` 是不同领域概念，字段和统计必须分开。

### 2.8 数据库存储（R5）

SQLAlchemy ORM + 可移植列类型；SQLite 默认（`out/review.db`），换 MySQL/PG 只改 URL。5 表：

| 表 | 关键字段 |
|----|---------|
| cr_review_task | id、status（running/completed/completed_with_warnings/failed）、input_type/ref、diff_summary(JSON，仅元数据+脱敏摘要，见下）、config(JSON)、error_* |
| cr_sandbox_run | task_id、status(ok/failed/timeout/blocked/error)、exit_code、timed_out、filter_action、脱敏后 stdout/stderr 摘录、error_type、duration_ms |
| cr_filter_event | task_id、stage、target、action、rule、reasons(JSON) |
| cr_finding | 9 必须字段 + rule_id + bucket + dedup_key + extra(JSON)；evidence 必须已脱敏 |
| cr_report | task_id(unique)、schema_version、rule_pack_version、config_digest、input_sha256、summary、severity_stats(JSON)、filter_summary(JSON)、sandbox_summary(JSON)、metrics(JSON)、report(完整 JSON，已脱敏) |

**原始 diff 落库策略（先脱敏，后落库）**：

- **默认禁止**在数据库中保存原始 unified diff / patch 全文。原始内容由宿主安全读取，只可短暂存在于受控内存、任务临时目录和隔离 workspace，任务结束在 `finally` 中清理；不得记录到普通日志或异常文本。
- `diff_summary` 只存元数据：SHA-256、字节数、文件数、hunk 数、增删行统计、`review_scope` 分布、脱敏后的文件路径摘要；禁止含未脱敏代码行。
- finding evidence、recommendation、Filter reasons、sandbox stdout/stderr 摘录、error 信息、Telemetry 属性、最终 report **一律脱敏后再写入**。
- 若确需回放完整变更，仅允许存**脱敏副本**，且须显式配置开关开启（默认关闭）；该副本仍不得含明文密钥。

`ReviewStore` ABC + `get_task_bundle(task_id)` 一次返回 task+runs+events+findings+report；`init-db` 幂等（create_all）。五表 MVP 不再拆表，但 JSON 字段必须带 schema version；至少为 task status、run task_id、event task_id/action、finding task_id/severity/category 和 report task_id 建索引。数据库 URL 可由 CLI `--db-url` 或配置提供，默认仍为 `sqlite:///out/review.db`。

### 2.8.1 失败语义（部分失败、整体继续）

Filter 拦截或沙箱执行失败时：

1. 被标为 `deny` / `needs_human_review` 的脚本**严禁进入沙箱**（AC7）。
2. 其余已放行的检查继续执行（例如多次 `skill_run` / 多个检查命令中，一项失败不阻断其余项）。本期默认链路是单次 `run_checks.py`：若该次被拦或失败，则无沙箱侧 findings，但仍继续后处理 → 落库 → 出报告，**不回退宿主执行规则**。
3. 超时、非零退出、输出超限全部记入 `cr_sandbox_run`（含 error_type 与失败摘要），并进入 warnings 桶。
4. 任务状态：
   - `completed`：全流程成功，无 Filter 拦截、无沙箱失败、无运行告警。
   - `completed_with_warnings`：仍能生成最终报告，但存在 Filter 拦截、沙箱失败/超时/截断或其他运行告警。
   - `failed`：仅用于无法继续形成有效交付的关键失败——输入解析失败、DB 初始化失败、关键写库失败、报告无法生成。

永不因单次检查失败让整个评审任务崩溃（AC4）。

### 2.9 监控审计（R9）

`MetricsCollector` 在报告冻结时生成不可变 snapshot，落 `cr_report.metrics`（验收主路径），同时在关键阶段打 SDK telemetry span（code_review.total/.parse/.sandbox/.postprocess/.llm）。

snapshot 至少包含：`total_duration_ms`、`sandbox_duration_ms`、`llm_duration_ms`、`tool_call_count`、`sandbox_run_count`、`filter_block_count`、`filter_review_count`、`finding_count`、`warning_count`、`needs_human_review_count`、`suppressed_count`、`severity_distribution`、`category_distribution`、`error_type_distribution`、`runtime_type`、`python_version`、`platform`。

Telemetry span 属性采用白名单：只允许脱敏 task id、状态、阶段耗时、计数、枚举型 error code 和 runtime 类型；严禁写入 diff/evidence/recommendation/stdout/stderr 原文、环境变量值和本地绝对路径。无 OTel 环境时 span 自动成为零副作用。

### 2.10 报告（八段式，JSON + Markdown 双格式）

`review_report.json` + `review_report.md`，固定八段：

1. Findings 摘要（按 severity 排序）
2. 严重级别统计
3. 人工复核项（needs_human_review 桶）
4. 运行告警（warnings 桶）
5. Filter 拦截摘要
6. 沙箱执行摘要
7. 监控指标
8. 结论与可执行修复建议（编号、按严重级别排序）

`review_report.json` 是规范源，顶层至少包含 `schema_version`、`rule_pack_version`、`config_digest`、`input_sha256`、`task_id`、`input_summary`（含 source_kind 与各文件 review_scope）、四桶结果、Filter 摘要、sandbox 摘要、metrics snapshot 和 final conclusion。Markdown 的位置展示在 `line_side=old` 时必须明确标为旧侧行号，不能让读者误认为当前文件仍存在该行。生成流程固定为：

1. 构建报告对象并通过 `schemas/review_report.schema.json` 校验。
2. 对完整对象执行最终敏感信息扫描；命中明文时阻止持久化并将任务标为 failed。
3. 用同目录临时文件 + 原子替换写入 JSON。
4. Markdown 通过 `ReportRenderer` 仅从已校验 JSON 确定性渲染，写入前再次扫描并原子替换。
5. 数据库保存同一报告对象的脱敏内容或摘要，不重新计算另一份统计。

`ReportRenderer` 为扩展协议；本期实现 JSON 与 Markdown renderer，SARIF renderer 留在第 7 章。

### 2.11 CLI 与运行模式

`run_agent.py` 五个子命令：

- `review --diff-file|--repo-path|--files|--fixture [--dry-run] [--sandbox container|cube|local] [--model-mode fake|real|off] [--trace] [--log-level DEBUG|INFO|WARNING] [--fail-on-severity high|critical] [--db-url URL] [--output-dir DIR]`：直接调用唯一 `ReviewPipeline`，用于 CI 和确定性自动化。
- `user-query "<natural-language review intent>" --diff-file|--repo-path|--files|--fixture [--dry-run] [--sandbox container|cube|local] [--model-mode fake|real|off] [--trace] [--log-level DEBUG|INFO|WARNING] [--fail-on-severity high|critical] [--db-url URL] [--output-dir DIR]`：始终经 SDK `LlmAgent + SkillToolSet` 触发受控 Skill 链；自然语言仅表达意图，四种输入必须由结构化参数显式指定。
- `show <task_id>`：输出全链路 bundle
- `list`：列出历史任务
- `init-db`：幂等初始化

`--dry-run` = fake model（固定模板走与 real 完全相同的 LlmAgent+Runner 链路），**不**切换 sandbox。无 Docker 时必须同时显式传 `--sandbox local`，否则严格 container 默认会直接报错。零 Key + 无 Docker 的推荐命令：`python run_agent.py review --fixture 01_clean_simple --dry-run --sandbox local`。pytest 单测注入 fake runtime 是第三条路径，不冒充 CLI dry-run。

四种输入由互斥参数组强制只能选择一个。本期不提供 `--command`、`--run-tests` 或 `--llm-denoise`；任意命令和目标仓库测试不得通过隐藏参数进入当前实现。

`review` 直接调用唯一 `ReviewPipeline`；`user-query` 是唯一公开的 Agent 入口，SDK
`LlmAgent + SkillToolSet` 必须产生可观察的 `skill_load("code-review") → skill_run(...)`
工具调用，再由受控 `skill_run` 适配器委托同一 pipeline，不能产生第二套检测或持久化逻辑。
宿主在创建 Agent 前验证四选一结构化输入、路径、大小、编码和 diff 格式；不得让模型从自由文本推测
任意文件路径、命令、环境变量或未登记脚本。无效输入以退出码 2 拒绝，且不调用模型、Filter 或沙箱。
`skill_run` 对模型只暴露一次性 review request id；固定 Skill、script_id、argv、输入/输出路径、
环境、超时和输出限额必须由宿主结合 manifest 构造，原始 diff、宿主路径和命令字符串不得进入
模型上下文。未先成功 `skill_load`、Filter 非 ALLOW 或 request id 无效时，`skill_run` 必须零
沙箱副作用。成功的 CLI JSON 必须包含 `task_id`、状态、实际 sandbox、入口类型以及
`report_files.json` / `report_files.markdown` 的完整输出位置，方便人工和 CI 直接定位产物；路径
只输出到当前终端，绝不写入 report、数据库、Telemetry 或日志。维护者的完整 PowerShell 命令、
Docker 前置检查、16 个 fixture、模型模式和故障排查统一见
`examples/skills_code_review_agent/OPERATIONS.md`；真实模型的三项白名单变量由该目录 `.env` 读取，
runtime 类型、网络策略和输出目录必须显式通过 CLI 参数设置，不得藏在 `.env`。

`--trace` 是显式终端诊断模式：以 stderr JSON Lines 流式显示受控 query 解析、SDK
`skill_load` / `skill_run`、Filter、sandbox、Pipeline 和持久化状态；stdout 仍只输出最终 CLI JSON。
trace 字段只能包含固定事件名、安全枚举、计数、状态和布尔值，禁止输出模型私有推理、原始 query/diff、
代码/evidence、request id、命令、环境变量值和宿主路径；trace 不写入报告、数据库或 Telemetry。默认 `INFO`
日志也仅写 stderr，显示阶段、计数、固定状态码、耗时、实际 container ID 与终端可见的报告位置；`DEBUG`
可额外显示仓库相对文件路径、script_id 和已脱敏输出摘要。所有日志级别均禁止原始 diff、代码、evidence、
工具完整参数、workspace/request ID、环境变量和凭据。SDK 原始 INFO 固定降为 WARNING，避免暴露源码绝对路径和 workspace 标识。

**CLI 退出码约定**（`review` 子命令；`show`/`list`/`init-db` 成功 0、致命错误 2）：

| 退出码 | 含义 |
|--------|------|
| 0 | 审查完成且成功生成报告，含 `completed` 与 `completed_with_warnings` |
| 1 | 审查完成且报告已生成，但 findings 桶中存在达到 `--fail-on-severity` 阈值的正式 finding |
| 2 | 致命执行错误：输入无法解析、DB 关键写入失败、报告无法生成等（对应任务状态 `failed`） |

补充规则：

- Filter 拦截、沙箱脚本失败、`needs_human_review` / `suppressed` / `warnings` **默认不改变退出码**，只体现在任务状态与报告中（与 2.8.1 失败语义一致）。
- `--fail-on-severity` 默认关闭（等价于永不因 finding 返回 1）；CI 可显式设为 `high` 或 `critical`（判定为 severity ≥ 该阈值的 findings 桶条目）。只看 findings 桶，不看人工复核与运行告警。
- `evaluate.py` **独立**用非零退出码表示评测门禁失败（4.4 硬阈值任一不达标），**不复用** review CLI 的 finding 退出语义（evaluate 失败 ≠ 「有 high finding」）。

## 3. 技术栈

### 3.1 运行环境

- Python `>=3.10`（与项目 `pyproject.toml` 一致），开发与 CI 推荐 3.12；示例代码和 Skill 脚本不得使用 3.12 专属语法。开发环境使用仓库根 `.venv`。
- 沙箱生产默认 Docker（container runtime）；开发主机已具备 Docker 与真实模型 Key，container 路径与 real 模式都必须实测

### 3.2 依赖原则

- `skills/code-review/scripts/` 下只允许 Python 标准库（保证沙箱内零安装可跑）
- `code_review/` 应用层可用 SDK 及其既有依赖（SQLAlchemy、pydantic）；不新增其他第三方依赖
- 模型接入：`trpc_agent_sdk.models.OpenAIModel`，读 `TRPC_AGENT_API_KEY/BASE_URL/MODEL_NAME`

### 3.3 SDK 对接要点（已核实，实施时按此写）

| 对接点 | 位置 | 处理 |
|--------|------|------|
| skill_run 默认超时 300s | `trpc_agent_sdk/skills/tools/_skill_run.py` L437 | 经 run_tool_kwargs 覆盖为 30s |
| 工具结果 stdout/stderr 各截断 16KB | `_skill_run.py` 输出处理 | 与本项目 1MiB/2MiB 文件预算是两层限制，实现与文档明确区分 |
| workspace 输出限额 | `trpc_agent_sdk/code_executors/_types.py` L251-257 `WorkspaceOutputSpec.max_files/max_file_bytes/max_total_bytes` | 显式设置为本项目预算 |
| container 创建参数默认断网 | `trpc_agent_sdk/code_executors/container/_container_cli.py` L160 `network_mode="none"`；其 `describe()` 仍返回 `network_allowed=True` | `network_allowed` 不作为有效状态证明；验证本次实际配置未覆盖 `network_mode=none` 后才放行 |
| cube 声明 network_allowed=True | `trpc_agent_sdk/code_executors/cube/_runtime.py` L456；当前配置类型未暴露可验证出口策略 | 视为“可能具备网络能力”而非“网络已开启”；本期无可验证无出口/受控网关证明时默认拒绝 |
| local 声明 network_allowed=True | `trpc_agent_sdk/code_executors/local/_local_ws_runtime.py` L702 | 仅显式 dev 降级放行，并将隔离与网络策略不可强制证明写入 warnings |
| Skill 仓库 | `trpc_agent_sdk.skills.create_default_skill_repository` + `SkillToolSet` | 两个入口都经 SkillRepository 解析/stage skill |
| Filter | `trpc_agent_sdk` 的 `BaseFilter` / `run_filters` | governance.py 基于真实 Filter 链实现 |

### 3.4 能力复用边界（SDK 复用 vs 自研，实现时必须遵守）

> 原则：**框架能力全部复用 SDK，只有「代码评审」业务领域的逻辑自研**。禁止绕开 SDK 机制自己造轮子（这是两个已有 PR #212/#201 被质疑最多的点）；也禁止把 SDK 已有能力重写一遍。

| 能力 | SDK 提供的部分（直接复用） | 我们自己写的部分 |
|------|--------------------------|----------------|
| Skills | `create_default_skill_repository`、`SkillToolSet`、`skill_load`/`skill_run` 工具、skill stage 进 workspace 的整套机制（`trpc_agent_sdk/skills/`） | 只写 skill 的**内容**：SKILL.md、6 篇规则文档、`scripts/` 下的检查脚本 |
| 沙箱执行 | container / cube / local 三种 workspace runtime、超时参数、输出截断、`WorkspaceOutputSpec` 限额及能力描述（`trpc_agent_sdk/code_executors/`） | `sandbox.py` 作为薄工厂选择后端、填充预算和环境变量，并向治理层提供本次最终生效网络配置/证明；不得用 `network_allowed` 代替有效状态校验 |
| Filter 治理 | `BaseFilter` / `run_filters` 的过滤器链机制 | manifest allowlist、参数模板、脚本摘要、禁止路径、环境/网络、预算和 runtime 能力的**判定逻辑** |
| 监控审计 | telemetry 模块的 span/trace 机制（`trpc_agent_sdk/telemetry/`） | `MetricsCollector` 轻量汇总器（生成 2.9 定义的不可变指标快照并落库） |
| Agent 入口 | `LlmAgent`、`Runner`、`OpenAIModel`、Session 服务 | 只写 prompts 和组装代码 |
| 数据库 | 复用 SDK 已有的 SQLAlchemy 依赖（SDK `storage/_sql.py` 即基于它）与可移植列类型写法 | 5 张 `cr_*` 表自己定义——SDK 的 storage 表是给 Session/Memory 用的，没有现成「评审任务」表，属于题目要求的「设计并实现最小 schema」 |

真正**从零自研**的只有三块，均为题目明确要求的业务交付物：

1. 规则引擎（`scripts/lib/` 的正则 + AST 检测）
2. 脱敏模块（检/脱同源正则表 + 熵检测）
3. 评审领域数据处理（去重分桶、finding 结构、报告八段式）

对应的排期硬约束：C1 治理必须走真实 `BaseFilter` 链、C2 沙箱必须走 SDK workspace runtime、D2 Agent 入口必须经 SkillRepository 加载 skill——这三个任务的验收标准均以此为准，不得用纯 Python 直调绕开。

### 3.5 设计模式

- 配置驱动：所有预算、阈值、路径集中在 `ReviewConfig`（dataclass/pydantic），不硬编码
- 可插拔：`ReviewStore` ABC（换 SQL 后端）、`create_sandbox_runtime` 工厂（换沙箱后端）、`ReportRenderer`（换报告格式）、`model-mode` 三态（换模型行为）
- 失败即数据：任何执行异常转记录行 + warnings，不抛出到 CLI 顶层
- 可复现：ChangeSet、ExecutionManifest、ReviewReport schema 和不可变 MetricsSnapshot 都有显式版本/摘要，稳定排序后再持久化

## 4. 测试约定

### 4.1 目录与框架

- pytest；所有测试代码、测试辅助和测试数据统一放在 `examples/skills_code_review_agent/tests/`
- `tests/unit/`：单个确定性模块接口测试；不依赖 Docker、API Key 或网络，外部依赖使用 fake 或临时本地替代
- `tests/integration/`：多个模块或本地适配器的协作测试，包括 SQLite、Filter 链、Skill 脚本、沙箱和 pipeline
- `tests/e2e/`：从 CLI / evaluate 输入到 JSON、Markdown、数据库 bundle、指标和退出码的完整闭环
- `tests/fixtures/`：只存测试输入与预期数据，不存可执行测试；公开样本放 `diffs/`，评测语料放 `corpus/`
- `tests/support/`：只存被两个以上测试层复用的 fake、builder 和公共断言，不复制产品逻辑
- 命名：`test_<module>.py`；8 条公开样本的系统测试用 `tests/e2e/test_fixtures_e2e.py`
- container 实测用 `@pytest.mark.container` 标记（无 Docker 环境 skip）
- real 模型实测用 `@pytest.mark.real_llm` 标记（无 Key 环境 skip）
- 必选 fixture 缺失必须失败，不得 skip；所有写入使用 `tmp_path`、临时 SQLite 或任务 workspace，不污染业务目录

### 4.2 Fake 注入约定

- 沙箱：测试经构造函数注入 fake workspace runtime（预置 stdout/exit_code/超时行为），不 monkeypatch 内部函数
- 模型：fake model 走与 real 完全相同的调用路径，返回固定模板
- 数据库：单测用 `sqlite:///:memory:` 或 tmp_path 下临时文件

### 4.3 公开 fixture（8 条 simple + 8 条 complex，AC1 最低硬性交付仍为 simple 8 条）

数据位于 `tests/fixtures/diffs/`，由 `tests/e2e/test_fixtures_e2e.py` 通过公开入口执行：

| fixture | 内容 | 预期 |
|---------|------|------|
| 01_clean_simple | 无问题 diff | 0 findings，报告正常生成 |
| 02_security_simple | SQL 注入 f-string + subprocess shell=True | ≥2 条 security findings（high/critical） |
| 03_async_leak_simple | async 内 time.sleep + ClientSession 未关 | async-errors + resource-leak 各 ≥1 |
| 04_db_lifecycle_simple | 连接未 close、事务未 commit | ≥1 条 db-lifecycle |
| 05_missing_tests_simple | 改源码不改测试 | needs_human_review 含 missing-tests 项，findings 桶为空该类 |
| 06_duplicate_finding_simple | 同文件同行同类多规则命中 | 去重后 1 条，extra.also_matched 非空 |
| 07_sandbox_failure_simple | 注入沙箱失败（--inject-sandbox-failure 或 fake runtime） | 0 findings + warnings 记录 + status=completed_with_warnings，报告照常渲染 |
| 08_secret_redaction_simple | 字符串/配置中含 AWS Key、GitHub PAT、password，并含注释占位符对照 | 真实格式产生 secrets finding；占位符降噪；报告、DB、日志和沙箱摘要字节级无明文 |

每条 `_simple` fixture 另配一条同名前缀、`_complex` 后缀的真实工程样例。complex diff 每条包含
60–150 行新增代码、至少两个文件，并混合正常实现、真实风险和关键词干扰项；测试必须继续验证精确类别、
分桶、去重、JSON/Markdown/SQLite bundle 以及明文泄漏扫描。8 条 simple fixture 用于快速定位基础链路回归；
`evaluate.py` 的 AC1/AC2 公开代理口径仍只统计 simple 8 条，避免改变既有硬门禁分母。

### 4.4 评测语料与 CI 硬门禁（AC2 代理）

`evaluate.py` 是本地/CI 的离线评测硬门禁，**不拒绝**；但必须澄清口径，避免把公开语料门禁误写成「官方隐藏样本 AC2」。

**语料规模（定死，禁止「若干」这种模糊表述）**：

- 正样本 ≥20（6 类覆盖，不含 2.3 声明盲区场景）
- 干净负样本 ≥10（高置信 FP 期望为 0）
- 密钥语料 ≥48 条 + 良性列表（0 FP）
- 另含边界样本：纯 diff changed-lines vs `--files` full-file 两种审查口径、fixture 载荷类型保持、残缺 hunk、binary/rename、新增/删除 `0,0` 退化值、context-only 行映射和删除侧 secret 定位
- 匹配键：`(file, line, category)`，与去重三元组一致；只匹配 findings 桶（needs_human_review / suppressed / warnings 不计入 P/R）
- 另建 blind-spot stress corpus，覆盖拆分密钥、动态拼接、跨函数资源传递、非标准测试目录等已声明盲区；只输出观测结果，不混入硬门禁分母，也不得从评测产物中静默消失

**硬门禁阈值（任一不达标 → 非零退出码）**：

| 指标 | 阈值 | 对应验收 |
|------|------|---------|
| 8 条公开 fixture 全部成功产出 JSON + MD + DB 记录 | 8/8 | AC1 |
| 高危问题 Recall（critical/high，代理语料） | ≥ 0.80 | AC2 代理 |
| findings 桶 finding-level 误报占比 `FP/(TP+FP)` | ≤ 0.15 | AC2 代理 |
| 脱敏检出率 | ≥ 0.95 | AC5 |
| 8 条 public fixture 的独立 fake Agent 审查墙钟时间 | 每条 ≤ 120 s | AC6 |
| Precision / Recall / F1 | 输出到摘要；F1 **不设单独硬阈值**（由上两项 Recall/FP 约束即可，避免三重冲突） | 观测指标 |

**明确不是硬门禁的**：

- 官方「隐藏样本」AC2 本身——CI 拿不到隐藏集；门禁只能证明**公开代理语料**达标，README 验收表须写明「AC2 以代理语料佐证」。
- LLM 降噪/补审相关指标——本期默认 off，不进门禁。
- container / real LLM 实测——用 pytest mark，无环境时 skip，不阻塞普通 CI。

**输出与回归历史**：评测强制 `model_mode=fake`，不接受 real 或本期不存在的 LLM 降噪参数。摘要写 `eval_summary.json`（必选），记录 schema/rule-pack/config digest、Python、平台、runtime 和墙钟时间；可选 `--write-db` 写入独立 SQLite 形成回归历史（默认关闭，且不得污染业务 review.db）。

**evaluate.py 的 sandbox 口径（与 CLI 生产默认解耦）**：

| 路径 | sandbox | model | 用途 |
|------|---------|-------|------|
| `evaluate.py`（普通 CI / 本地门禁，默认） | **显式 `local`** | fake | 8 条 fixture 各自经 Agent+Skill 独立运行且每条 ≤120s；聚合耗时仅观测；摘要记录 runtime/OS/是否有 Docker |
| `evaluate.py --sandbox container` | container | fake | 额外结果；Docker 可用时跑，**不与 local 基准耗时直接比较** |
| `run_agent.py review`（生产默认） | **严格 container** | fake\|real\|off | 无 Docker 直接报错；不受 evaluate 默认影响 |
| pytest 单元 / pipeline 单测 | 注入 fake workspace | fake/off | 测编排与落库，不冒充「脚本真执行」 |
| `@pytest.mark.container` / cube 集成 | container / cube | fake | Docker 可用时跑，不可用 skip，**不阻塞普通 CI** |

硬约束：

1. **evaluate 默认 local 必须是显式选择**（代码与文档都写成 `--sandbox local`），不是 `--dry-run` 偷偷换沙箱——CLI 的 dry-run 仍只代表 fake model，沙箱语义不变。
2. **evaluate 默认路径禁止用 fake workspace**：8 条 fixture 必须各自以独立 `user-query` 任务真跑 Agent 的 `skill_load → skill_run` 与仓库自带可信 Skill 脚本，并各自验证 JSON、Markdown、SQLite 和单条时延；公开代理语料可直跑可信 Skill 脚本计算规则指标。仅允许执行本仓库 `skills/code-review/scripts/` 与 fixtures，禁止用户自定义命令混入门禁路径。
3. local 模式下 Filter 仍运行，并把「隔离与网络策略不可强制证明」降级告警写入 warnings；cube 默认拒绝的原因是当前 SDK 无法提供具体实例无出口/受控网关的可验证证明，而不是 `network_allowed=True` 字段本身。container 集成测试必须验证实际生效的 `network_mode=none`。

**与 pytest 的分工**：pytest 负责 unit、integration 与 fixture 驱动的 e2e；`evaluate.py` 负责跨 fixture 的聚合指标门禁。CI 建议顺序：`pytest examples/skills_code_review_agent/tests/ -q`（跳过 container/real_llm）→ `python examples/skills_code_review_agent/evaluate.py --sandbox local`（model=fake + sandbox=local）。

### 4.5 关键安全测试

- 超时击杀：注入 sleep 超过 per_run_timeout 的脚本，断言 timed_out=True 且任务不崩
- 输出截断：产出超限输出，断言截断标志 + warnings
- 金丝雀环境变量：宿主设 `TRPC_AGENT_API_KEY=canary-xyz`，断言沙箱子进程环境与所有落库内容不含该值
- 治理哨兵：未注册脚本、摘要不匹配、未知/重复/超长参数、shell 元字符和预算超限请求均被 deny，副作用哨兵未触发、沙箱运行数为 0
- 路径边界：覆盖 `../`、绝对宿主路径、指向 repo 外的 symlink/junction、超大输入，断言 staging 前拒绝且未读取目标内容
- 检测后脱敏：真实格式密钥在原始 fixture 中能生成 secrets finding，但 evidence、recommendation、Filter reasons、异常、stdout/stderr、Telemetry、JSON/MD/DB 均无明文
- 字符串/注释作用域：结构类 API 仅出现在注释/字符串时不报；字符串中的真实密钥必须检出；明显占位符不进入高置信 findings
- changed-line AST：完整文件中的历史遗留问题不在 changed lines 时不报；新增文件可重建全文时启用 AST；残缺 hunk 自动降级且不崩
- full-file scope：同一文件通过 `--files` 输入时允许报告任意行，ChangeSet/JSON/MD 明确显示 `status=snapshot` 与 `review_scope=full_file`；不得把它的结果冒充增量审查
- hunk 退化状态：新增/snapshot 断言 old=`0,0`，删除断言 new=`0,0`，字段非空；映射只含 context 行；删除侧 secret 使用真实 old line 和 `line_side=old`
- 报告一致性：JSON 通过 schema，MD 与 DB 统计均来自同一报告对象；模拟中断后不存在半写文件
- 明文扫描：e2e 后对 review_report.json/.md、sqlite 文件和捕获日志做字节级扫描，断言无明文密钥

### 4.6 测试哲学

只测外部行为（CLI 出入参、报告内容、DB 行、返回结构），不测内部实现细节；每条 AC 至少有一个专门测试；断言用具体值，不用「不抛异常」当通过标准。

## 5. 架构设计

### 5.1 分层

```
入口层    run_agent.py (CLI)          agent/ (LlmAgent + SkillToolSet)
              │                              │
              │                    skill_load → 受控 skill_run
              └──────────┬───────────────────┘
                         ▼
应用层    code_review/pipeline.py  ReviewPipeline.run()  ← 唯一检测链路
              │
              ├─ inputs.py / config.py（ChangeSet + 输入边界）
              ├─ governance.py（manifest 驱动的 Filter 治理门）
              ├─ sandbox.py（container|cube|local 工厂）
              ├─ dedup.py / redaction.py / llm_enhancer.py
              ├─ metrics.py / report.py（snapshot + ReportRenderer）
              └─ store/（SQLAlchemy 5 表 + ReviewStore ABC）
                         │
技能层    skills/code-review/scripts/lib/  ← 单一真相源（纯标准库）
          沙箱内直接执行；宿主 local 模式 importlib 加载同一份
```

### 5.2 目录树（交付清单，任务完成的文件级依据）

```
examples/skills_code_review_agent/
├── README.md
├── run_agent.py
├── agent/
│   ├── __init__.py
│   ├── agent.py
│   └── prompts.py
├── code_review/
│   ├── __init__.py
│   ├── config.py
│   ├── pipeline.py
│   ├── inputs.py
│   ├── governance.py
│   ├── sandbox.py
│   ├── redaction.py
│   ├── dedup.py
│   ├── llm_enhancer.py
│   ├── report.py
│   ├── metrics.py
│   └── store/
│       ├── __init__.py
│       ├── models.py
│       ├── review_store.py
│       └── init_db.py
├── skills/code-review/
│   ├── SKILL.md
│   ├── references/
│   │   └── security-boundaries.md
│   ├── rules/
│   │   ├── security.md
│   │   ├── async-errors.md
│   │   ├── resource-leak.md
│   │   ├── missing-tests.md
│   │   ├── secrets.md
│   │   └── db-lifecycle.md
│   └── scripts/
│       ├── manifest.json
│       ├── parse_diff.py
│       ├── run_checks.py
│       └── lib/
│           ├── __init__.py
│           ├── diff_parser.py
│           ├── rule_engine.py
│           ├── rules_security.py
│           ├── rules_async.py
│           ├── rules_resource.py
│           ├── rules_db.py
│           ├── rules_tests.py
│           └── secret_rules.py
├── evaluate.py
├── schemas/
│   └── review_report.schema.json
├── sample_output/
│   ├── review_report.json
│   └── review_report.md
└── tests/
    ├── README.md
    ├── unit/
    │   ├── test_config.py
    │   ├── test_diff_parser.py
    │   ├── test_redaction.py
    │   ├── test_rules.py
    │   ├── test_rules_ast.py
    │   ├── test_dedup.py
    │   └── test_metrics.py
    ├── integration/
    │   ├── test_inputs.py
    │   ├── test_store.py
    │   ├── test_report.py
    │   ├── test_skill_scripts.py
    │   ├── test_governance.py
    │   ├── test_sandbox_safety.py
    │   ├── test_pipeline.py
    │   ├── test_llm_enhancer.py
    │   └── test_agent_entry.py
    ├── e2e/
    │   ├── test_cli.py
    │   ├── test_fixtures_e2e.py
    │   └── test_evaluate.py
    ├── fixtures/
    │   ├── diffs/        # 8 条公开 simple fixture + 8 条 complex 配对样例
    │   └── corpus/       # 标注评测语料
    └── support/          # 共享 fake、builder 和断言
```

### 5.3 Pipeline 八阶段

1. 建任务：`cr_review_task(status=running)`，记录输入类型、config snapshot、schema/rule-pack version 和 config digest。
2. 安全读取与解析：校验输入根、路径和体积，在受控宿主内存/任务目录读取原始内容，按输入形态提取带 `review_scope` 的 ChangeSet；`--files` 标为 snapshot/full_file，fixture 保留声明的载荷语义，新增/删除 hunk 使用固定 `0,0` 契约。此阶段不破坏性脱敏，也不记录代码原文。
3. Filter 治理门：解析 execution manifest，校验 script/摘要/参数/路径/环境/网络/runtime，并在执行前预检预算；DENY/NEEDS_HUMAN_REVIEW 短路并记录脱敏原因。
4. 沙箱执行：stage skill 与最小输入集 → 再校验 realpath/hash → 预算受控运行注册脚本。secrets 规则对原始内容检测，沙箱在输出 evidence/stdout/stderr 前首次脱敏；失败转 warnings，不回退宿主。
5. 后处理：宿主对所有输出二次脱敏 → changed-line 过滤 → 三元组稳定去重 → 无重叠阈值分桶。
6. LLM 增强（fake|real|off）：仅接收脱敏数据，只改写 recommendation/summary/复核提示，不得改变 canonical finding 集合及其 severity/confidence/bucket。
7. 冻结与持久化：生成不可变 metrics snapshot 和 canonical ReviewReport，最终泄漏扫描通过后写 findings/filter_events/sandbox_runs/report；任何明文命中阻止持久化。
8. 报告与清理：schema 校验 → JSON 原子写入 → 从 JSON 确定性渲染 MD 并原子写入 → telemetry 白名单打点 → `finally` 清理 workspace。

### 5.4 四信任域与三层脱敏

| 信任域 | 可见原始 diff | 允许输出 |
|--------|--------------|---------|
| 受控宿主输入层 | 是，仅任务内存/临时目录 | ChangeSet 元数据和送入隔离 workspace 的最小输入集；禁止日志 |
| 隔离沙箱 | 是，仅本次任务 | 已脱敏 findings、stdout/stderr 摘要 |
| LLM | 否 | 仅脱敏后的 finding 和摘要素材 |
| 持久化/报告/Telemetry | 否 | 已脱敏结构化数据、计数、枚举和摘要 |

三层脱敏：

1. 沙箱内：secrets 规则先对原始内容完成检测，产出 evidence/stdout/stderr 时首次脱敏（`lib/secret_rules.py`）。
2. 宿主：对 finding 的 evidence/recommendation、Filter reasons、异常和沙箱摘要进行二次脱敏。
3. 出口：JSON、Markdown、数据库和允许的 Telemetry 属性在写入前做完整对象扫描；发现明文则阻止写入并标记 failed。

检测规则与脱敏共享同一正则表（secret_rules.py 单一真相源），杜绝「检出未脱/脱了未检」。

## 6. 项目排期

> **排期原则**
> - 只按本文档设计落地：以 5.2 目录树为交付清单，每个任务都在文件系统产生可见变化
> - 每个任务 ≈1h 一个可验收增量，给出验收标准 + 测试方法，尽量 TDD
> - 先打通离线闭环（解析 → 规则 → 落库 → 报告 → dry-run），再上安全边界（Filter + 沙箱），最后 Agent 入口与评测
> - 外部依赖（Docker、真实模型）在单元测试一律 fake 注入；container/real 实测放专门任务

### 阶段总览

1. **阶段 A：工程骨架与离线内核** — 目录、配置、diff 解析、规则引擎、脱敏（纯标准库可测）
2. **阶段 B：落库闭环** — SQLAlchemy 5 表、去重分桶、报告、CLI、dry-run 全链路
3. **阶段 C：安全边界** — Filter 治理链、沙箱三后端、安全测试、container 实测
4. **阶段 D：Agent 入口与评测** — LlmAgent+SkillToolSet、LLM 增强、8 fixtures、evaluate.py
5. **阶段 E：收尾** — 指标核对、README、设计说明、sample_output、全绿

### 📊 进度跟踪表 (Progress Tracking)

> **状态说明**：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成

#### 阶段 A：工程骨架与离线内核

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 | 测试方法 |
|---------|---------|------|---------|---------|---------|
| A1 | 目录骨架 + ReviewConfig + schema + 分层 pytest 基座 | [x] | 2026-07-24 | 5.2 目录树全部空模块就位；tests/unit、integration、e2e、fixtures、support 分层存在且 fixture 不在项目顶层；ReviewConfig 含 2.1 输入上限、2.6 预算默认值和版本字段；review_report schema 可加载；pytest 可发现并跑通冒烟测试 | tests/unit/test_config.py：测试分层目录、默认值/环境覆盖/config_digest 稳定性断言；schema 语法校验 |
| A2 | diff 解析器与 ChangeSet（scripts/lib/diff_parser.py） | [x] | 2026-07-25 | 按 2.1 字段契约解析 unified diff；覆盖 rename/binary/CRLF/no-newline/删除/新增/snapshot、review_scope、old/new changed lines；新增/snapshot old=`0,0`、删除 new=`0,0`，字段非空；old_to_new 只映射 context；完整新增文件可重建 full_text | tests/unit/test_diff_parser.py：逐边界断言 status/scope、规范路径、`0,0`、context-only 映射、analysis_mode 和 input_sha256 |
| A3 | 检/脱同源密钥模块（scripts/lib/secret_rules.py + code_review/redaction.py） | [x] | 2026-07-25 | ≥12 种密钥模式 + Shannon 熵；detect 与 redact 共用同一正则表；检测读取原始值，任何输出使用 `[REDACTED:<类型>]`；新侧与删除旧侧均扫描，旧侧定位带 line_side=old；recommendation/reasons/error/stdout/stderr 均可统一扫描 | tests/unit/test_redaction.py：≥48 条真实格式语料检出率 ≥95%、≥10 条良性语料；字符串/配置/删除侧密钥、注释占位符和所有旁路字段无明文 |
| A4 | 规则引擎框架 + 安全类规则（rule_engine.py + rules_security.py） | [x] | 2026-07-27 | Rule 协议（rule_id/category/severity/confidence/match）；SQLi f-string、shell=True、eval/exec 可检出；仅结构类规则忽略注释/docstring/普通字符串，secrets 不走该通用过滤 | tests/unit/test_rules.py：正样本命中；危险 API 仅出现在注释/字符串时 0 FP；字符串真实密钥仍命中 |
| A5 | 异步 + 资源泄漏规则（rules_async.py + rules_resource.py） | [x] | 2026-07-26 | async 内 time.sleep、未 await、open/ClientSession 未 with/close 可检出（含 hunk 跨行） | tests/unit/test_rules.py 扩展：各规则正/负样本 |
| A6 | DB 生命周期 + 测试缺失规则（rules_db.py + rules_tests.py） | [x] | 2026-07-26 | 连接未关/事务未 commit 可检出；missing-tests 为变更集级启发式，置信度锁 0.5–0.8 | tests/unit/test_rules.py 扩展：missing-tests 断言 confidence<0.8 恒成立 |
| A7 | AST 增强层（requires_full_file + review_scope 约束） | [x] | 2026-07-26 | changed_lines scope 只报 AST 节点与新变更行相交的问题；full_file scope 明确扫描全文；deleted_lines 不跑普通 AST；纯残缺 diff 不 ast.parse；失败降级 + warning | tests/unit/test_rules_ast.py：增量模式历史问题不报、snapshot 模式同一问题可报、变更行命中、新增文件 AST、删除/残缺/语法错误稳定处理 |
| A8 | 输入层与安全 staging（code_review/inputs.py） | [x] | 2026-07-26 | 四种输入互斥并统一产出 ChangeSet；`--files` 固定 snapshot/full_file，fixture 保留 diff/full-file 载荷类型，repo untracked 为 added/full_file；Git argv 禁 shell；realpath、symlink/junction 和输入总量在 staging 前检查；原始内容仅留受控任务域 | tests/integration/test_inputs.py：四输入与 scope/status；fixture diff hunk 不被改写；.env 检出、忽略目录/二进制；路径/超限拒绝；日志无原始密钥 |
| A9 | CR Skill、执行 manifest 与沙箱入口 | [x] | 2026-07-26 | SKILL.md frontmatter 合规；6 篇规则文档声明能力/盲区；security-boundaries.md 完整；manifest 声明 script_id/entrypoint/hash/参数/预算/网络；run_checks.py 读输入输出已脱敏 findings.json | tests/integration/test_skill_scripts.py：manifest schema/摘要校验；subprocess 直跑注册脚本；findings 9 字段且输出无明文 |

#### 阶段 B：落库闭环

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 | 测试方法 |
|---------|---------|------|---------|---------|---------|
| B1 | SQLAlchemy 5 表 + ReviewStore ABC + init_db | [x] | 2026-07-26 | 2.8 的 5 表模型和索引；report 保存 schema/rule-pack/config/input 版本摘要；SqlReviewStore 支持 SQLite 默认和 URL 切换；get_task_bundle 聚合返回；init-db 幂等 | tests/integration/test_store.py：CRUD、索引/版本字段、bundle 完整性、重复 init-db、脱敏 JSON 字段 |
| B2 | 稳定去重与四桶路由（code_review/dedup.py） | [x] | 2026-07-26 | 三元组去重；按 severity/confidence/evidence 具体度选主项；also_matched 稳定合并；边界无重叠；warnings 只收运行告警 | tests/unit/test_dedup.py：候选乱序输入仍生成相同 JSON；0.50/0.80/1.00 边界和同行同类合并断言 |
| B3 | MetricsCollector + telemetry span（code_review/metrics.py） | [x] | 2026-07-27 | 2.9 的 immutable snapshot 字段完整；span 属性仅走白名单；无 OTel 环境零副作用 | tests/unit/test_metrics.py：三桶/suppressed/Filter 两类计数；snapshot 冻结；敏感文本和绝对路径无法进入 span |
| B4 | Canonical JSON + Markdown renderer（code_review/report.py） | [x] | 2026-07-27 | JSON schema 校验、稳定排序、最终泄漏扫描、原子写入；input_summary 显示 source/scope；MD 仅从 JSON 渲染并区分 old/new 行号；八段完整；ReportRenderer 可扩展 | tests/integration/test_report.py：scope 与 line_side 渲染、JSON/MD/DB 统计一致、重复渲染字节一致、空 findings、原子写入和明文阻止 |
| B5 | ReviewPipeline 八阶段编排（code_review/pipeline.py，fake runtime + model off 先行） | [x] | 2026-07-27 | 5.3 八阶段串通；原始输入仅存在于受控宿主/沙箱；沙箱先检测再脱敏，宿主二次脱敏，出口扫描；异常按 2.8.1 收敛；finally 清理 workspace | tests/integration/test_pipeline.py：真实格式密钥能检出但 task/findings/report/log 无明文；清理成功/失败语义；DB 无原始 diff 全文 |
| B6 | CLI 四子命令 + dry-run 链路（run_agent.py） | [x] | 2026-07-27 | review/show/list/init-db 可用；四输入互斥；支持 `--db-url`；`--dry-run --sandbox local` 零 Key/无 Docker 跑通；仅 dry-run 不换 sandbox；退出码 0/1/2；本期拒绝 command/run-tests/llm-denoise 参数 | tests/e2e/test_cli.py：review→show→list；临时 DB URL；零 Key local <120s；无 Docker strict container exit=2；fail-on-severity 边界 |

#### 阶段 C：安全边界

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 | 测试方法 |
|---------|---------|------|---------|---------|---------|
| C1 | Manifest 驱动 Filter 治理链（code_review/governance.py） | [x] | 2026-07-27 | 基于真实 BaseFilter/run_filters；按 2.7 顺序校验 script/hash/参数/路径/环境/网络/预算/runtime；网络决策读取最终生效配置/可验证证明而非仅凭 capability；FilterAction 与 FindingBucket 分型；非 ALLOW 短路且原因脱敏落库 | tests/integration/test_governance.py：未注册脚本、hash 不符、参数/shell/path/预算逃逸均被拒且副作用为 0；container capability 为 true 但实际 network_mode=none 可放行；cube 无证明默认拒绝、仅用户确认仍拒绝 |
| C2 | 沙箱工厂、staging 与预算（code_review/sandbox.py） | [x] | 2026-07-27 | container 严格默认且实际 `network_mode=none` 可验证；宿主 repo 不可写挂载；staging 后复验 realpath/hash；per-run 与累计预算预检；WorkspaceOutputSpec 限额；环境构造而非透传 | tests/integration/test_sandbox_safety.py：只读/最小 staging、网络配置默认值与覆盖拒绝、超时、输出截断、累计预算、金丝雀环境变量 |
| C3 | 沙箱失败即数据 + container 实测 | [x] | 2026-07-27 | blocked/timeout/nonzero/truncated/cleanup_error 均形成脱敏 run/warning；任务可出报告则 completed_with_warnings；Docker 下 02/08 fixture 真容器跑通且 network_mode=none 未覆盖 | tests/integration/test_sandbox_safety.py 扩展 + @pytest.mark.container；捕获输出全量明文扫描 |

#### 阶段 D：Agent 入口与评测

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 | 测试方法 |
|---------|---------|------|---------|---------|---------|
| D1 | LLM 增强层（code_review/llm_enhancer.py，fake|real|off） | [x] | 2026-07-27 | fake 与 real 走相同 LlmAgent+Runner 路径；仅改写 recommendation/summary/复核提示；输入全量脱敏；不得改变 finding identity/rule/severity/confidence/bucket/dedup；有 Key 也不自动 real | tests/integration/test_llm_enhancer.py：canonical finding 对象前后逐字段一致；仅允许文本增强字段变化；LLM 输入无明文 |
| D2 | Agent 入口（agent/agent.py + prompts.py，LlmAgent+SkillToolSet） | [x] | 2026-07-28 | 经 SkillRepository 发现 code-review skill；Agent 真实产生 `skill_load → skill_run` 工具调用，且受控 `skill_run` 只接受一次性 request id，由宿主按 manifest 构造固定执行计划；未 load、无效 request 或 Filter 非 ALLOW 均零沙箱副作用；Agent 与 CLI 共享同一 manifest、Filter、sandbox、storage 和 ReviewPipeline，原始 diff/宿主路径/命令不进模型，输出 canonical finding 集合一致 | tests/integration/test_agent_entry.py：工具事件顺序、双入口 finding 一致性、无效 request/跳过 load/未注册脚本零执行；tests/e2e/test_cli.py：自然语言 fixture query 生成 JSON+MD+DB |
| D3 | 8 条公开 fixture + e2e（tests/fixtures/diffs/ + tests/e2e/test_fixtures_e2e.py） | [x] | 2026-07-27 | 4.3 表 8 条全交付；逐条断言 findings/桶/状态/JSON+MD+DB；08 号验证“真实密钥能检出且所有出口无明文”及注释占位符降噪 | pytest tests/e2e/test_fixtures_e2e.py 参数化 8/8 通过 + 日志/文件/DB 字节级扫描 |
| D4 | 评测语料 + evaluate.py CI 硬门禁 | [x] | 2026-07-28 | 4.4 语料规模与 blind-spot 观测集达标；匹配键 (file,line,category)；8 条 fixture 各自经 fake+local Agent/Skill 运行并各自 ≤120s，高危 Recall≥0.8、finding-level FP 占比≤0.15、脱敏≥0.95；聚合评测耗时只观测；摘要含单条时延、版本/配置/环境；默认不写 DB；README 明示 AC2 为代理 | python examples/skills_code_review_agent/evaluate.py --sandbox local（期望 exit=0）+ tests/e2e/test_evaluate.py：断言 8 条 Agent 时延/工具序列，禁止 real/LLM 降噪参数，门禁失败 exit 非零 |

#### 阶段 E：收尾

| 任务编号 | 任务名称 | 状态 | 完成日期 | 验收标准 | 测试方法 |
|---------|---------|------|---------|---------|---------|
| E1 | real 模型实测 + sample_output | [x] | 2026-07-27 | real 模式仅显式开启并用真实 Key 跑通 02_security_simple fixture；sample_output JSON 通过 schema，MD 由该 JSON 渲染，样例不含环境特定绝对路径或敏感值 | @pytest.mark.real_llm 用例 + schema/稳定渲染/明文扫描 |
| E2 | README + 300–500 字设计说明 + 验收总检 | [x] | 2026-07-27 | README 含用法/AC 代理口径/安全信任域/manifest/沙箱 local 指引/输出限制；设计说明覆盖题目全部主题；风险表完整；AC1–AC8 逐条核对 | 全量 pytest + flake8 + schema 校验 + AC 对照表逐项打勾 |
| E3 | 8 条 complex fixture + 成对 E2E | [x] | 2026-07-27 | 8 条 simple fixture 全部保留；每类新增 1 条 60–150 行新增代码、至少双文件且包含正常实现/风险/干扰项的 complex diff；16 条均验证 JSON+MD+DB，complex 逐条保持类别、分桶、去重和脱敏契约；evaluate 仍使用 simple 8 条门禁 | tests/e2e/test_fixtures_e2e.py：8 条 complex 逐条聚焦通过 + 8 条 simple 回归 + 普通全量回归 |
| E4 | fixture simple/complex 命名迁移 | [x] | 2026-07-27 | 16 条 fixture 仅以 `_simple` 或 `_complex` 命名；CLI、Agent、Container、real-model、evaluate、文档、QA 与精确 E2E 断言全部使用新名称；evaluate 仍只统计 simple 8 条，禁止旧名别名残留 | tests/e2e/test_fixtures_e2e.py：16 条通过；tests/e2e/test_evaluate.py：8/8 simple 门禁；全量普通回归 |
| E5 | 维护者运行手册 + Agent CLI 入口 + 显式产物路径 | [x] | 2026-07-28 | README 直达完整 `OPERATIONS.md`；手册同时给出 Windows PowerShell 与 Linux/macOS Bash 命令，覆盖 `.env.example`、Docker/本地/Cube 前置、CLI 四输入、16 fixture、fake/real/off、direct/Agent 入口、DB/报告查询、pytest/evaluate/lint 和故障排查；公开 `user-query` 经 SDK Agent+SkillToolSet 复用同一 pipeline，支持四种结构化输入并在 Agent 前拒绝无效输入；`--via-agent`/`ask` 不保留；INFO 安全显示实际 container ID、阶段、计数、耗时和报告位置，SDK 原始 INFO 降为 WARNING；review 成功 JSON 返回完整 JSON/Markdown 产物位置 | tests/e2e/test_cli.py：direct/`user-query` 都生成报告并返回入口/路径，四输入、无效输入零 Agent/Filter/沙箱副作用、INFO 无敏感字段且 container ID 可见；tests/e2e/test_release_docs.py：维护手册、配置模板和 PowerShell/Bash 关键命令链接存在；维护者按 OPERATIONS.md 手工执行 local/container/real 路径 |

## 7. 未来规划

以下明确不在本期交付，留作后续迭代：

- **SARIF v2.1.0 输出**：基于本期 `ReportRenderer` 和 canonical ReviewReport 接 GitHub Code Scanning，不另建报告数据模型
- **沙箱内运行目标仓库单元测试**（执行边界已定，实施时照此）：仅 `--repo-path` 模式可用（--diff-file 无仓库可跑）；整体默认 off，显式 `--run-tests targeted` 开启。启用后默认只运行 diff 中新增/修改的测试文件及路径映射可定位的相关测试；无网络容器内 `pytest -q`，单次 ≤30s，不允许在线安装依赖；找不到相关测试**不自动跑全量**；全量测试或用户自定义测试命令必须显式请求、注册为受控参数模板并再次经过 Filter。结果分类固定为：测试断言失败 → sandbox 摘要 + needs_human_review；缺依赖/插件失败/ImportError/环境不兼容 → warnings；超时/容器错误 → warnings + sandbox error；未收集或无法映射测试 → needs_human_review。以上均**不进 findings 桶**、不参与 AC2，也不导致 review 崩溃
- **外部扫描器（bandit、semgrep）进沙箱**：R2 的可选执行目标，当前以规则脚本 + diff 解析满足要求
- **LLM 语义补审（盲区折中方案）**：对规则未检出但可疑的代码（2.3 声明盲区：跨函数资源传递、异常路径泄漏、数据流类异步错误、业务逻辑漏洞）做 LLM 二次审阅。硬约束：结果**只能标 needs_human_review，永远不进正式 findings、不参与检出率/误报率统计**——补语义盲区但不破坏规则层的确定性保证。需预录制 fixture 体系支撑 fake 模式
- **LLM 降噪二分类**：只能给 canonical finding 添加脱敏的“疑似误报、建议人工复核”辅助说明，不得直接删除 finding 或改写 severity/confidence/bucket/dedup；若未来要允许改变正式结果，必须另起 schema/rule-pack 版本和独立评测合同
- **A2A / AG-UI 服务化**：多轮对话与流式事件
- **RAG 编码规范知识库 + 跨会话长期记忆**：历史评审经验沉淀
- **多语言规则**：JS/Go 等语言的安全与资源规则
- **在线评测平台**：隐藏样本集持续回归与指标看板

### 7.1 风险登记表

| 风险 | 触发信号 | 本期缓解 |
|------|---------|---------|
| 正则规则误报 | 干净样本 finding-level FP 占比上升 | AST 确认、注释/字符串作用域过滤、降低置信度、稳定例外规则 |
| AST 报告历史问题 | finding 主定位不在 new_changed_lines | AST 节点范围强制与 changed lines 相交 |
| manifest 与脚本漂移 | staged 文件 SHA-256 不一致 | Filter 在运行前 deny 并记录脱敏事件 |
| 恶意路径或大输入耗尽资源 | realpath 越界、文件/字节/行数超限 | staging 前拒绝或标人工复核，不读取越界目标 |
| 敏感信息经旁路泄漏 | 最终输出扫描命中明文 | 阻止报告/DB 持久化，任务 failed，保留无明文错误码 |
| container 不可用 | runtime 初始化失败 | 生产默认明确报错；local 仅显式开发 fallback 并写 warning |
| 外部依赖导致测试噪声 | ImportError、插件/环境错误 | 本期不运行目标仓库测试；未来按上方失败类型分桶 |
