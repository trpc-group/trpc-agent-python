# Script Safety Guard

> LLM 生成脚本的预执行安全护栏，基于静态分析 + 规则引擎的轻量级方案。

---

## 一、定位与目标

Script Safety Guard 解决的核心问题是：**AI Agent 生成的脚本在执行前，如何自动识别并拦截危险行为？**

设计目标：

| 目标 | 说明 |
|------|------|
| 零信任执行 | LLM 输出的任何脚本默认不可信，必须经过安全检查 |
| 三级决策 | 不是简单的"通过/拒绝"，而是 ALLOW / NEEDS_HUMAN_REVIEW / DENY 三档 |
| 零配置可用 | 内置合理默认策略，开箱即用，无需额外配置文件 |
| 可观测 | 每次检查自动产出审计日志 + 结构化报告 |
| 可扩展 | 新增规则只需继承 + 装饰器注册，无需修改引擎代码 |
| 业务不阻断 | Guard 自身异常时 fail-open，绝不因安全模块故障导致主流程中断 |

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────┐
│                   应用层（Agent / Tool）               │
│                                                     │
│   ┌──────────────┐           ┌──────────────────┐   │
│   │ Filter Chain │           │  CodeExecutor    │   │
│   │  适配器模式   │           │   Wrapper 模式   │   │
│   └──────┬───────┘           └────────┬─────────┘   │
│          │                            │             │
└──────────┼────────────────────────────┼─────────────┘
           │                            │
           ▼                            ▼
┌─────────────────────────────────────────────────────┐
│              ScriptSafetyGuard（编排引擎）             │
│                                                     │
│  ┌────────┐  ┌──────────┐  ┌────────┐  ┌────────┐  │
│  │ Parser │  │ Registry │  │ Policy │  │ Output │  │
│  │代码解析 │  │ 规则注册表│  │策略配置 │  │报告输出 │  │
│  └────────┘  └──────────┘  └────────┘  └────────┘  │
│                    │                                │
│         ┌─────────┼─────────┐                      │
│         ▼         ▼         ▼                      │
│  ┌───────────────────────────────────┐             │
│  │         规则集 (Rules)             │             │
│  │  FS / NET / PROC / DEP / RES / SEC│             │
│  └───────────────────────────────────┘             │
└─────────────────────────────────────────────────────┘
           │                    │
           ▼                    ▼
    ┌─────────────┐     ┌──────────────┐
    │ report.json │     │ audit.jsonl  │
    │  完整报告    │     │  审计日志流   │
    └─────────────┘     └──────────────┘
```

---

## 三、核心链路

一次完整的安全检查分为以下步骤：

### Step 1：代码解析

- **Python**：通过 AST 解析生成语法树，获得精确的函数调用、导入关系、字面量等结构信息。若 AST 解析失败（如语法错误），生成一条 GUARD-001 Finding 但不中断流程。
- **Bash**：按行切分 + 正则匹配（Bash 无标准 AST 工具），覆盖常见危险模式。

### Step 2：构造扫描上下文

将源码、AST 树、工作目录、环境变量、工具元数据封装为统一的 ScanContext，供所有规则消费。

### Step 3：规则匹配与执行

从 RuleRegistry 中筛选支持当前语言的规则，逐条执行 `scan()` 方法。每条规则独立运行，互不影响，某条规则异常时仅记录错误，不影响其他规则。

### Step 4：决策聚合

采用 **Strictest-wins** 策略：

```
最终决策 = max(所有 Finding 的 decision)

优先级：DENY > NEEDS_HUMAN_REVIEW > ALLOW
```

即：只要有一条规则判定 DENY，整体就是 DENY。这保证了安全底线不被绕过。

### Step 5：结果输出

同时产出两种输出（均可通过策略开关控制）：

| 输出类型 | 格式 | 用途 |
|---------|------|------|
| Report | 单个 JSON 文件 | 完整的检查报告（含所有 Findings 详情） |
| Audit | JSONL 追加流 | 精简审计记录（仅决策摘要，用于监控/合规） |

---

## 四、规则体系

### 4.1 六大风险分类

| 分类 | 编码前缀 | 关注领域 |
|------|---------|---------|
| 文件操作 | FS-xxx | 危险路径访问、破坏性删除 |
| 网络 | NET-xxx | 非白名单外联、原始 Socket |
| 进程 | PROC-xxx | 子进程执行、Shell 注入 |
| 依赖 | DEP-xxx | 包安装、不受信源 |
| 资源 | RES-xxx | Fork bomb、无限循环、过度内存 |
| 密钥 | SEC-xxx | 硬编码凭证、环境变量泄露 |

### 4.2 当前规则清单

| Rule ID | 严重级别 | 检查内容 | 默认决策 |
|---------|---------|---------|---------|
| FS-001 | HIGH | 访问禁止路径（/etc/shadow, ~/.ssh/ 等） | DENY |
| FS-002 | MEDIUM | 破坏性文件操作（rm -rf, shutil.rmtree） | NEEDS_HUMAN_REVIEW |
| NET-001 | HIGH | 非白名单域名的网络请求 | NEEDS_HUMAN_REVIEW |
| NET-002 | MEDIUM | 原始 socket / 低级网络 API | NEEDS_HUMAN_REVIEW |
| PROC-001 | HIGH | 非允许列表的子进程执行 | NEEDS_HUMAN_REVIEW |
| PROC-002 | HIGH | Shell 注入风险（os.system, eval, shell=True） | DENY |
| DEP-001 | MEDIUM | 包安装操作（pip install, npm install） | NEEDS_HUMAN_REVIEW |
| DEP-002 | HIGH | 从不受信源安装（URL, git+, curl\|bash） | DENY |
| RES-001 | HIGH | Fork bomb / 无限循环 | DENY / NEEDS_HUMAN_REVIEW |
| RES-002 | MEDIUM | 过度资源消耗（大内存分配, dd 大文件） | NEEDS_HUMAN_REVIEW |
| SEC-001 | HIGH | 硬编码密钥/凭证（AWS Key, GitHub Token 等） | DENY |
| SEC-002 | MEDIUM | 环境变量泄露（print(os.environ)） | NEEDS_HUMAN_REVIEW |

### 4.3 规则的输出结构

每条规则产出零到多个 **Finding**，每个 Finding 包含：

| 字段 | 含义 |
|------|------|
| rule_id | 规则编号（如 PROC-002） |
| category | 风险分类 |
| severity | 严重级别（high / medium / low） |
| decision | 该发现建议的决策 |
| confidence | 置信度（0.0 ~ 1.0） |
| evidence | 触发证据（已脱敏） |
| line_number | 代码行号 |
| description | 问题的自然语言描述（模板 + 动态上下文） |
| recommendation | 修复建议（规则作者预定义的最佳实践） |

**说明**：`description` 和 `recommendation` 由规则作者在编写规则时预定义为模板字符串，运行时通过 f-string 填入具体上下文（函数名、路径名等）。这是经典的静态分析方式（类似 ESLint、Bandit），不依赖 LLM 生成。

---

## 五、策略配置

### 5.1 配置文件格式

```yaml
version: "1.0"

network:
  allowed_domains:          # 白名单域名（支持 glob 通配符）
    - "*.example.com"
    - "api.openai.com"
  override: false           # false=追加到默认列表，true=完全替换

process:
  allowed_commands:         # 允许的子进程命令
    - "python"
    - "node"
  override: false

file_operations:
  forbidden_paths:          # 禁止访问的路径
    - "/etc/shadow"
    - "~/.ssh/"
  override: false

resources:
  max_timeout_seconds: 300
  max_output_size_mb: 100

output:
  report:
    enabled: true
    dir: "./.safety_reports"
    filename_template: "{tool_name}_{timestamp}_report.json"
  audit:
    enabled: true
    file: "./.safety_reports/audit.jsonl"
```

### 5.2 策略发现优先级

Guard 启动时自动查找策略文件，优先级从高到低：

1. 环境变量 `TOOL_SAFETY_POLICY_PATH` 指定的路径
2. `$CWD/tool_safety_policy.yaml`
3. `$CWD/.safety/tool_safety_policy.yaml`
4. `$CWD/config/tool_safety_policy.yaml`
5. 内置默认策略（硬编码在代码中）

### 5.3 合并语义

- **列表字段**（如 `allowed_domains`）：`override: false` → 用户列表追加到默认列表并去重；`override: true` → 用户列表完全替换默认列表
- **标量字段**（如 `max_timeout_seconds`）：用户值直接覆盖默认值

### 5.4 白名单与规则参数的区别

| 配置项 | 语义 | 效果 |
|--------|------|------|
| `network.allowed_domains` | **白名单直通** | 匹配的域名直接跳过检查，不产生 Finding |
| `process.allowed_commands` | **规则参数** | 传入规则供其判断，匹配时降低风险等级 |
| `file_operations.forbidden_paths` | **规则参数** | 传入规则作为危险路径集合 |

只有 `allowed_domains` 具有"直通"语义，其余均为规则的输入参数。

---

## 六、接入方式

提供两种接入模式，适配不同的架构场景：

### 模式一：Filter Chain 适配器（推荐）

适用于使用 tRPC Agent Filter 机制的项目。

```
工具调用 → Filter Chain → [ScriptSafetyFilter] → 工具执行
                                    │
                                    ├── decision=ALLOW → 放行
                                    ├── decision=DENY → 阻断，返回错误
                                    └── decision=NEEDS_HUMAN_REVIEW
                                            │
                                            ├── block_on_review=True → 阻断
                                            └── block_on_review=False → 放行（仅记录）
```

**接入步骤：**
1. 在工具定义中声明使用安全过滤器
2. 可选：放置 `tool_safety_policy.yaml` 自定义策略
3. 完成 —— Filter 会自动对含脚本内容的工具参数进行扫描

### 模式二：CodeExecutor Wrapper

适用于自定义代码执行器的项目。

```
Agent 调用 execute_code()
    → SafeCodeExecutor.execute_code()
        → Guard.check(script)  ← 预执行检查
            → decision=ALLOW → 委托给内部 executor 实际执行
            → decision=DENY → 直接返回错误结果，不执行
```

**接入步骤：**
1. 用 SafeCodeExecutor 包装现有的 CodeExecutor
2. 所有经过该执行器的代码自动进行安全扫描
3. 无需修改业务调用代码

### 两种模式的选择建议

| 场景 | 推荐模式 |
|------|---------|
| 使用 tRPC Agent 标准工具体系 | Filter Chain |
| 自定义代码执行引擎 | CodeExecutor Wrapper |
| 需要细粒度控制（指定哪些工具启用） | Filter Chain |
| 统一拦截所有代码执行 | CodeExecutor Wrapper |

---

## 七、设计决策与取舍

### 7.1 为什么是三级决策而非二元？

在 Agent 场景中，很多操作**不是绝对危险的**——比如访问一个未知域名的 API，可能是合理的业务需求，也可能是数据外泄。二元决策会导致：
- 过于严格 → 大量误拦，用户体验差
- 过于宽松 → 安全形同虚设

NEEDS_HUMAN_REVIEW 提供了一个**缓冲地带**：标记风险但不直接阻断，由上层适配器决定是请求人工确认还是放行并记录。这给了业务方灵活性。

### 7.2 为什么用静态分析而非动态沙箱？

| 维度 | 静态分析 | 动态沙箱 |
|------|---------|---------|
| 延迟 | 毫秒级（1~5ms） | 秒级（启动容器/VM） |
| 误报率 | 较高（保守策略） | 较低（实际执行验证） |
| 漏报率 | 可能漏过动态构造 | 低（真实行为） |
| 资源消耗 | 几乎为零 | 需要独立环境 |
| 部署复杂度 | 零依赖 | 需要容器运行时 |

**选择静态分析的原因：**
- Agent 工具调用是高频操作，不能承受秒级延迟
- 作为第一道防线（快速筛选），而非唯一防线
- 零依赖部署，无需容器运行时

### 7.3 为什么 Strictest-wins？

在安全场景中，**假阴性（漏放）比假阳性（误拦）的代价高得多**。Strictest-wins 确保：
- 任何一条规则发现严重问题，就不会被其他规则的"无问题"结论稀释
- 不需要复杂的投票/加权机制，逻辑简单且可预测
- 用户通过白名单策略解决误报，而非修改聚合逻辑

### 7.4 为什么 Fail-open？

Guard 的设计原则是**安全模块不应成为可用性风险**：
- 规则执行异常 → 记录错误 + 生成一条 NEEDS_HUMAN_REVIEW Finding，继续执行其他规则
- Guard 整体异常 → 记录错误，不阻断工具调用
- AST 解析失败 → 降级为正则匹配模式

这是"安全护栏"而非"安全门禁"的设计哲学：提供保护但不牺牲可用性。

### 7.5 为什么审计日志用 JSONL？

- **追加友好**：每次检查只需 append 一行，无需读取/修改/重写整个文件
- **流式处理**：逐行解析，适合日志采集工具（Filebeat、FluentBit）
- **并发安全**：不存在多进程同时修改同一 JSON 数组的竞态问题
- **行业标准**：ElasticSearch、BigQuery、Datadog 等原生支持 JSONL 导入

### 7.6 为什么证据要脱敏？

审计日志可能流转到日志平台、告警系统等外部服务。原始代码中可能包含：
- 用户的业务逻辑（知识产权）
- 硬编码的密钥/凭证
- 内部系统路径

因此 evidence 字段做了：截断（200 字符上限）+ 密钥模式 masking（替换为 `***`）。

---

## 八、与其他安全组件的关系

Script Safety Guard 不是孤立存在的模块，而是 Agent Runtime 安全体系中的一个层次。理解它与其他组件的分工协作关系，是正确使用它的前提。

### 8.1 组件全景

```
┌────────────────────────────────────────────────────────────────────────┐
│                          Agent Runtime                                  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      Filter Chain（请求管道）                      │  │
│  │  ┌──────────┐  ┌────────────────────┐  ┌──────────┐             │  │
│  │  │ModelFilter│→│ScriptSafetyFilter  │→│ToolFilter│→ [Tool 执行] │  │
│  │  └──────────┘  └────────────────────┘  └──────────┘             │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                              │                                          │
│                    decision=ALLOW 才继续                                 │
│                              ▼                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                CodeExecutor（代码执行层）                           │  │
│  │   ┌──────────────────┐  ┌────────────────────┐  ┌─────────────┐ │  │
│  │   │UnsafeLocalExec   │  │ContainerExecutor   │  │CubeExecutor │ │  │
│  │   │（无沙箱，本机裸跑）│  │（Docker 容器隔离）  │  │（远程 VM）   │ │  │
│  │   └──────────────────┘  └────────────────────┘  └─────────────┘ │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                              │                                          │
│                              ▼                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                Telemetry（可观测性层）                              │  │
│  │  OTel Span 属性 · Metrics (Counter/Histogram) · Audit JSONL       │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

### 8.2 各组件职责对比

| 组件 | 本质 | 作用时机 | 核心职责 |
|------|------|---------|---------|
| **Script Safety Guard** | 静态分析引擎（规则匹配） | 代码执行**前** | 判断代码**意图**是否危险 |
| **Sandbox（沙箱）** | 运行时隔离环境 | 代码执行**中** | 限制代码**行为**的实际影响范围 |
| **Filter Chain** | 请求/响应管道中间件 | 工具调用前后 | Safety Guard 的**接入载体** |
| **Telemetry** | 可观测性基础设施 | 全程 | Safety Guard 的**输出通道**之一 |
| **CodeExecutor** | 代码执行抽象接口 | 执行时 | Safety Guard 的**保护对象** |

### 8.3 协作关系详解

#### Safety Guard ↔ Filter Chain

Filter Chain 是 Safety Guard 在 Agent 工具调用链路中的**接入点**。Safety Guard 本身只是一个纯粹的分析引擎（输入代码，输出决策），而 Filter 负责：
- 从工具调用参数中提取待检查的脚本内容
- 调用 Guard 执行扫描
- 根据返回的决策采取动作（放行 / 阻断 / 标记审查）

两者是"业务逻辑"与"管道胶水"的关系。Safety Guard 完全不感知 Filter 的存在，也可以脱离 Filter 在 CodeExecutor Wrapper 模式下工作。

#### Safety Guard ↔ Telemetry

Telemetry 是 Safety Guard 的**输出消费者**，不是运行依赖。Guard 的检查结果通过以下通道流入 Telemetry 层：
- OTel Span 属性：记录每次扫描的 decision、耗时、finding 数量
- Metrics Counter：`safety_checks_total`、`safety_findings_total` 接入监控
- Audit JSONL：持久化审计留痕

如果 Telemetry 不可用，Guard 正常工作（fail-open 原则），只是可观测性降级。

#### Safety Guard ↔ CodeExecutor

Safety Guard 保护的核心对象就是 CodeExecutor。无论使用哪种 Executor 实现，Guard 都在代码**交给 Executor 之前**进行筛查：

| CodeExecutor 实现 | 有无沙箱 | Safety Guard 的价值 |
|------------------|---------|-------------------|
| `UnsafeLocalCodeExecutor` | ❌ 无隔离 | **唯一防线**——一旦 Guard 被绕过，代码可完全控制宿主机 |
| `ContainerCodeExecutor` | ✅ Docker | **第一道防线**——阻止明显危险代码启动容器，降低攻击面和资源消耗 |
| `CubeCodeExecutor` | ✅ 远程 VM | **第一道防线**——减少不必要的远程执行开销，同时提供审计留痕 |

#### Safety Guard ↔ Sandbox

这是最容易混淆的关系。两者**互补但不可替代**：

| 维度 | Safety Guard（静态分析） | Sandbox（运行时隔离） |
|------|------------------------|---------------------|
| 防护时机 | 执行前（pre-execution） | 执行中（at-runtime） |
| 防护方式 | 检测代码的**意图模式** | 限制代码的**实际能力** |
| 延迟开销 | 1~5ms | 数百ms~数秒（启动容器/VM） |
| 部署依赖 | 零（纯 Python） | 需要 Docker / 远程 VM 服务 |
| 能否被绕过 | 可以（动态构造、编码混淆） | 极难（OS 级别隔离边界） |

### 8.4 为什么 Safety Guard 不能替代沙箱隔离

这是一个关键的架构认知。用类比来说：

> **Safety Guard = 机场安检**（登机前检查行李有无危险物品）
> **Sandbox = 飞机货舱的防爆容器**（即使安检遗漏，爆炸也不会损坏飞机主体）

具体的不可替代原因：

**1. 动态构造绕过**

```python
# Safety Guard 看到的是 getattr 调用 —— 无法识别实际目的
func = getattr(__import__('os'), 'sys' + 'tem')
func('rm -rf /')
```

静态分析只能看到代码的**文本形态**，无法追踪运行时的值流转。沙箱不关心你"怎么构造"，只限制你"能做什么"。

**2. 编码混淆**

```python
import base64
eval(base64.b64decode('b3Muc3lzdGVtKCdybSAtcmYgLycp').decode())
```

Guard 看到的是一次 `eval()` 调用（会触发 PROC-002），但如果攻击者使用更隐蔽的执行路径（如自定义解码器），静态分析可能无法识别。沙箱中即使 eval 执行了，删除操作也被限制在容器内部。

**3. 跨文件/跨依赖攻击**

```python
import malicious_lib  # Guard 无法追踪这个库内部做了什么
malicious_lib.do_something()
```

Guard 只分析当前脚本，无法递归分析 import 的第三方库。沙箱隔离确保即使库执行了恶意代码，影响也被限制在隔离环境内。

**4. 未知攻击向量（0-day）**

Guard 只能检测**已编写规则的已知模式**。面对全新的攻击手法，规则库来不及更新时 Guard 无能为力。沙箱提供的是**物理隔离边界**，不依赖对攻击模式的先验知识。

**5. 资源耗尽攻击**

```python
# 表面上只是一个简单的列表操作
data = [0] * (10 ** 10)  # 40GB 内存分配
```

Guard 可以启发式检测某些模式（如 `10**10`），但无法覆盖所有导致资源耗尽的代码。沙箱通过 cgroups / VM 资源配额硬性限制 CPU、内存、磁盘 I/O。

### 8.5 正确的安全纵深模型

生产环境的推荐安全纵深：

```
LLM 生成脚本
     │
     ▼
[Layer 1] Script Safety Guard — 静态预筛（1~5ms，零成本过滤 90%+ 已知危险）
     │
     ▼
[Layer 2] Human Review — 人工确认（可选，处理 NEEDS_HUMAN_REVIEW 的灰色地带）
     │
     ▼
[Layer 3] Sandbox Execution — 沙箱执行（Container / Cube，物理隔离边界）
     │
     ▼
[Layer 4] Telemetry + Audit — 全程记录（事后审计、异常检测、合规留痕）
```

- **Layer 1 解决效率问题**：绝大多数明显危险的脚本在这里被快速拦截，避免不必要的容器启动开销。
- **Layer 3 解决可靠性问题**：即使 Layer 1 和 Layer 2 全部失效，恶意代码的实际损害被限制在沙箱内部。
- **两者缺一不可**：只有 Guard 没有 Sandbox = 一旦被绕过就全军覆没；只有 Sandbox 没有 Guard = 每次都要启动容器，且缺少审计和前置过滤能力。

---

## 九、已知限制

| 限制 | 说明 | 缓解方式 |
|------|------|---------|
| 动态构造绕过 | `getattr(os, 'sys' + 'tem')('rm -rf /')` 无法被 AST 静态捕获 | 搭配运行时沙箱使用 |
| Bash 分析精度 | Bash 无标准 AST，依赖正则匹配，复杂管道/变量展开可能漏过 | 覆盖常见危险模式，持续补充 |
| 跨文件分析 | 仅分析单个脚本，无法追踪 import 引入的依赖链 | 关注直接调用，间接调用由运行时防护 |
| 误报率 | 保守策略可能误拦合法操作 | 通过 policy 白名单精确排除 |
| 仅支持 Python / Bash | 暂不支持 JavaScript、Go 等语言 | 规则框架预留了语言扩展能力 |
| 无上下文语义理解 | 无法判断"这段代码的意图是否合理" | 这是静态分析的固有限制，需要人工审核补充 |

---

## 十、如何扩展新规则

### 10.1 步骤概览

1. **确定规则元数据**：Rule ID、风险分类、严重级别、支持的语言
2. **编写规则类**：继承 BaseRule，实现 `scan()` 方法
3. **注册规则**：使用 `@register_rule` 装饰器
4. **添加导入**：在 `rules/__init__.py` 中导入新模块
5. **编写测试**：覆盖正例（应触发）和反例（不应触发）

### 10.2 规则编写指南

| 原则 | 说明 |
|------|------|
| 单一职责 | 一条规则聚焦一个风险模式，不要混合多种检测逻辑 |
| 无副作用 | `scan()` 是纯函数，不修改 context，不执行 I/O |
| 异常安全 | 规则内部异常不应抛到外部，自行 catch 并返回空列表 |
| 支持策略 | 规则应读取 policy 配置（白名单、阈值等），而非硬编码 |
| 有意义的 evidence | 提供足够的上下文帮助用户理解问题，但不包含完整源码 |
| 精确的 line_number | 尽可能给出准确的行号，方便定位 |

### 10.3 Rule ID 命名规范

```
{CATEGORY_PREFIX}-{3位数字编号}

分类前缀：
  FS    → file_operations
  NET   → network
  PROC  → process
  DEP   → dependency
  RES   → resource
  SEC   → secrets
  GUARD → 内置守卫（保留）
```

---

## 十一、可观测性

### 11.1 输出通道

| 通道 | 格式 | 目的 |
|------|------|------|
| Python Logger | 结构化 JSON | 实时接入 SIEM / 日志平台 |
| Report 文件 | JSON | 单次检查的完整快照 |
| Audit 文件 | JSONL | 持久化审计留痕 |
| OTel Metrics | Counter + Histogram | 接入 Prometheus/Grafana 监控面板 |

### 11.2 关键指标

| 指标 | 类型 | 含义 |
|------|------|------|
| safety_checks_total | Counter | 检查总次数（按 decision 分标签） |
| safety_check_duration_ms | Histogram | 扫描耗时分布 |
| safety_findings_total | Counter | 发现总数（按 category + severity 分标签） |

---

## 十二、FAQ

**Q: 一个脚本触发了多条规则怎么办？**
A: 所有规则独立执行，收集全部 Findings，最终通过 Strictest-wins 取最严决策。报告中会列出所有 Findings，用户可以看到完整的风险画像。

**Q: 如何解决误报？**
A: 通过策略文件配置白名单。例如 `network.allowed_domains` 添加合法域名，或 `process.allowed_commands` 添加允许的命令。

**Q: Guard 扫描耗时会影响工具调用延迟吗？**
A: 典型扫描耗时 1~5ms（取决于脚本大小和规则数量），对比网络请求延迟可忽略不计。

**Q: 能否只启用部分规则？**
A: 当前版本所有已注册规则均会执行。如需禁用某条规则，可以通过 RuleRegistry 的 `unregister()` 方法在运行时移除。

**Q: NEEDS_HUMAN_REVIEW 的脚本到底能不能执行？**
A: 取决于接入层的 `block_on_review` 配置。设为 True 则阻断（更安全），设为 False 则放行但记录（更宽松）。

---

## 十三、路线图（未来方向）

- [ ] 规则禁用/启用开关（策略级别）
- [ ] JavaScript / TypeScript 语言支持
- [ ] 规则置信度加权聚合（可选策略）
- [ ] 自定义规则热加载（外部目录扫描）
- [ ] 与动态沙箱的联动（静态高风险 → 触发沙箱二次验证）
