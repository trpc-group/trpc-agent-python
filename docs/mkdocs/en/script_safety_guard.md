# Script Safety Guard

> A lightweight pre-execution security guardrail for LLM-generated scripts, based on static analysis + rule engine.

---

## 1. Purpose & Goals

Script Safety Guard addresses a core problem: **How to automatically identify and block dangerous behaviors in AI Agent-generated scripts before execution?**

Design goals:

| Goal | Description |
|------|-------------|
| Zero-trust execution | Any script output by an LLM is untrusted by default and must pass security checks |
| Three-level decision | Not a simple "pass/reject" but ALLOW / NEEDS_HUMAN_REVIEW / DENY |
| Zero-config ready | Built-in reasonable default policies, works out of the box |
| Observable | Each check automatically produces audit logs + structured reports |
| Extensible | Adding new rules only requires inheritance + decorator registration |
| Non-blocking | Guard fails open on internal errors, never blocks the main flow due to security module failures |

---

## 2. Overall Architecture

```
┌─────────────────────────────────────────────────────┐
│              Application Layer (Agent / Tool)         │
│                                                     │
│   ┌──────────────┐           ┌──────────────────┐   │
│   │ Filter Chain │           │  CodeExecutor    │   │
│   │  Adapter Mode│           │  Wrapper Mode    │   │
│   └──────┬───────┘           └────────┬─────────┘   │
│          │                            │             │
└──────────┼────────────────────────────┼─────────────┘
           │                            │
           ▼                            ▼
┌─────────────────────────────────────────────────────┐
│           ScriptSafetyGuard (Orchestration Engine)   │
│                                                     │
│  ┌────────┐  ┌──────────┐  ┌────────┐  ┌────────┐  │
│  │ Parser │  │ Registry │  │ Policy │  │ Output │  │
│  │  Code  │  │   Rule   │  │ Config │  │ Report │  │
│  │ Parsing│  │ Registry │  │        │  │        │  │
│  └────────┘  └──────────┘  └────────┘  └────────┘  │
│                    │                                │
│         ┌─────────┼─────────┐                      │
│         ▼         ▼         ▼                      │
│  ┌───────────────────────────────────┐             │
│  │          Rules                    │             │
│  │  FS / NET / PROC / DEP / RES / SEC│             │
│  └───────────────────────────────────┘             │
└─────────────────────────────────────────────────────┘
           │                    │
           ▼                    ▼
    ┌─────────────┐     ┌──────────────┐
    │ report.json │     │ audit.jsonl  │
    │ Full Report │     │ Audit Stream │
    └─────────────┘     └──────────────┘
```

---

## 3. Core Pipeline

A complete security check consists of the following steps:

### Step 1: Code Parsing

- **Python**: Parses via AST to produce a syntax tree, obtaining precise function calls, import relationships, literals, and other structural information. If AST parsing fails (e.g., syntax error), a GUARD-001 Finding is generated without interrupting the flow.
- **Bash**: Line splitting + regex matching (Bash has no standard AST tooling), covering common dangerous patterns.

### Step 2: Build Scan Context

Wraps source code, AST tree, working directory, environment variables, and tool metadata into a unified ScanContext consumed by all rules.

### Step 3: Rule Matching & Execution

Filters rules from the RuleRegistry that support the current language, executing each rule's `scan()` method sequentially. Each rule runs independently; if one rule throws an exception, it only logs the error without affecting other rules.

### Step 4: Decision Aggregation

Uses the **Strictest-wins** strategy:

```
Final Decision = max(all Finding decisions)

Priority: DENY > NEEDS_HUMAN_REVIEW > ALLOW
```

Meaning: if any single rule returns DENY, the overall decision is DENY. This ensures the security baseline cannot be bypassed.

### Step 5: Result Output

Produces two outputs simultaneously (both controllable via policy switches):

| Output Type | Format | Purpose |
|-------------|--------|---------|
| Report | Single JSON file | Complete check report (all Finding details) |
| Audit | JSONL append stream | Compact audit record (decision summary only, for monitoring/compliance) |

---

## 4. Rule System

### 4.1 Six Risk Categories

| Category | Code Prefix | Focus Area |
|----------|-------------|------------|
| File Operations | FS-xxx | Dangerous path access, destructive deletion |
| Network | NET-xxx | Non-whitelisted outbound connections, raw sockets |
| Process | PROC-xxx | Subprocess execution, shell injection |
| Dependency | DEP-xxx | Package installation, untrusted sources |
| Resource | RES-xxx | Fork bombs, infinite loops, excessive memory |
| Secrets | SEC-xxx | Hardcoded credentials, environment variable leakage |

### 4.2 Current Rule Inventory

| Rule ID | Severity | Check Content | Default Decision |
|---------|----------|---------------|-----------------|
| FS-001 | HIGH | Access to forbidden paths (/etc/shadow, ~/.ssh/, etc.) | DENY |
| FS-002 | MEDIUM | Destructive file operations (rm -rf, shutil.rmtree) | NEEDS_HUMAN_REVIEW |
| NET-001 | HIGH | Network requests to non-whitelisted domains | NEEDS_HUMAN_REVIEW |
| NET-002 | MEDIUM | Raw socket / low-level network APIs | NEEDS_HUMAN_REVIEW |
| PROC-001 | HIGH | Subprocess execution outside allowed list | NEEDS_HUMAN_REVIEW |
| PROC-002 | HIGH | Shell injection risk (os.system, eval, shell=True) | DENY |
| DEP-001 | MEDIUM | Package installation (pip install, npm install) | NEEDS_HUMAN_REVIEW |
| DEP-002 | HIGH | Installation from untrusted sources (URL, git+, curl\|bash) | DENY |
| RES-001 | HIGH | Fork bomb / infinite loops | DENY / NEEDS_HUMAN_REVIEW |
| RES-002 | MEDIUM | Excessive resource consumption (large memory alloc, dd large file) | NEEDS_HUMAN_REVIEW |
| SEC-001 | HIGH | Hardcoded secrets/credentials (AWS Key, GitHub Token, etc.) | DENY |
| SEC-002 | MEDIUM | Environment variable leakage (print(os.environ)) | NEEDS_HUMAN_REVIEW |

### 4.3 Finding Output Structure

Each rule produces zero or more **Findings**, each containing:

| Field | Meaning |
|-------|---------|
| rule_id | Rule identifier (e.g., PROC-002) |
| category | Risk category |
| severity | Severity level (high / medium / low) |
| decision | Recommended decision for this finding |
| confidence | Confidence score (0.0 ~ 1.0) |
| evidence | Triggering evidence (sanitized) |
| line_number | Code line number |
| description | Natural language description (template + dynamic context) |
| recommendation | Remediation advice (predefined best practice by rule author) |

**Note**: `description` and `recommendation` are predefined as template strings by rule authors, filled with specific context (function names, path names, etc.) via f-strings at runtime. This is classic static analysis (similar to ESLint, Bandit) and does not rely on LLM generation.

---

## 5. Policy Configuration

### 5.1 Configuration File Format

```yaml
version: "1.0"

network:
  allowed_domains:          # Whitelisted domains (supports glob wildcards)
    - "*.example.com"
    - "api.openai.com"
  override: false           # false=append to default list, true=fully replace

process:
  allowed_commands:         # Allowed subprocess commands
    - "python"
    - "node"
  override: false

file_operations:
  forbidden_paths:          # Forbidden access paths
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

### 5.2 Policy Discovery Priority

Guard automatically discovers policy files at startup, priority from high to low:

1. Path specified by environment variable `TOOL_SAFETY_POLICY_PATH`
2. `$CWD/tool_safety_policy.yaml`
3. `$CWD/.safety/tool_safety_policy.yaml`
4. `$CWD/config/tool_safety_policy.yaml`
5. Built-in default policy (hardcoded)

### 5.3 Merge Semantics

- **List fields** (e.g., `allowed_domains`): `override: false` → user list appends to default list with deduplication; `override: true` → user list fully replaces default list
- **Scalar fields** (e.g., `max_timeout_seconds`): user value directly overrides default value

### 5.4 Whitelist vs. Rule Parameters

| Config Item | Semantics | Effect |
|-------------|-----------|--------|
| `network.allowed_domains` | **Whitelist pass-through** | Matching domains skip checks entirely, no Finding produced |
| `process.allowed_commands` | **Rule parameter** | Passed to rule for judgment, lowers risk level on match |
| `file_operations.forbidden_paths` | **Rule parameter** | Passed to rule as the dangerous paths set |

Only `allowed_domains` has "pass-through" semantics; the rest are input parameters to rules.

---

## 6. Integration Modes

Two integration modes are provided to fit different architectural scenarios:

### Mode 1: Filter Chain Adapter (Recommended)

Suitable for projects using the tRPC Agent Filter mechanism.

```
Tool Call → Filter Chain → [ScriptSafetyFilter] → Tool Execution
                                    │
                                    ├── decision=ALLOW → pass through
                                    ├── decision=DENY → block, return error
                                    └── decision=NEEDS_HUMAN_REVIEW
                                            │
                                            ├── block_on_review=True → block
                                            └── block_on_review=False → pass (log only)
```

**Integration steps:**
1. Declare the safety filter in your tool definition
2. Optional: place a `tool_safety_policy.yaml` for custom policies
3. Done — the Filter will automatically scan tool arguments containing script content

### Mode 2: CodeExecutor Wrapper

Suitable for projects with custom code executors.

```
Agent calls execute_code()
    → SafeCodeExecutor.execute_code()
        → Guard.check(script)  ← pre-execution check
            → decision=ALLOW → delegate to inner executor for actual execution
            → decision=DENY → return error result directly, no execution
```

**Integration steps:**
1. Wrap your existing CodeExecutor with SafeCodeExecutor
2. All code passing through the executor is automatically scanned
3. No changes needed to business calling code

### Mode Selection Guide

| Scenario | Recommended Mode |
|----------|-----------------|
| Using tRPC Agent standard tool system | Filter Chain |
| Custom code execution engine | CodeExecutor Wrapper |
| Fine-grained control (specify which tools are enabled) | Filter Chain |
| Uniform interception of all code execution | CodeExecutor Wrapper |

---

## 7. Design Decisions & Trade-offs

### 7.1 Why Three-level Decision Instead of Binary?

In Agent scenarios, many operations are **not absolutely dangerous** — for example, accessing an unknown domain's API could be a legitimate business need or data exfiltration. Binary decisions lead to:
- Too strict → high false positives, poor user experience
- Too lenient → security becomes meaningless

NEEDS_HUMAN_REVIEW provides a **buffer zone**: flags the risk without blocking, letting the upper adapter decide whether to request human confirmation or pass with logging. This gives business owners flexibility.

### 7.2 Why Static Analysis Instead of Dynamic Sandbox?

| Dimension | Static Analysis | Dynamic Sandbox |
|-----------|----------------|-----------------|
| Latency | Milliseconds (1~5ms) | Seconds (container/VM startup) |
| False positive rate | Higher (conservative policy) | Lower (actual execution verification) |
| False negative rate | May miss dynamic constructs | Low (real behavior) |
| Resource consumption | Near zero | Requires isolated environment |
| Deployment complexity | Zero dependencies | Requires container runtime |

**Reasons for choosing static analysis:**
- Agent tool calls are high-frequency operations that cannot tolerate second-level latency
- Acts as the first line of defense (fast screening), not the only defense
- Zero-dependency deployment, no container runtime needed

### 7.3 Why Strictest-wins?

In security scenarios, **false negatives (missed threats) are far more costly than false positives (over-blocking)**. Strictest-wins ensures:
- Any single rule finding a serious issue won't be diluted by other rules' "no problem" conclusions
- No need for complex voting/weighting mechanisms; logic is simple and predictable
- Users resolve false positives via whitelist policies, not by modifying aggregation logic

### 7.4 Why Fail-open?

Guard's design principle is **security modules should not become availability risks**:
- Rule execution exception → log error + generate a NEEDS_HUMAN_REVIEW Finding, continue other rules
- Guard overall exception → log error, don't block tool calls
- AST parse failure → degrade to regex matching mode

This is the "safety guardrail" rather than "security gate" philosophy: provide protection without sacrificing availability.

### 7.5 Why JSONL for Audit Logs?

- **Append-friendly**: Each check only appends one line, no need to read/modify/rewrite the entire file
- **Stream processing**: Line-by-line parsing, suitable for log collection tools (Filebeat, FluentBit)
- **Concurrency safe**: No race conditions from multiple processes modifying the same JSON array
- **Industry standard**: ElasticSearch, BigQuery, Datadog natively support JSONL import

### 7.6 Why Sanitize Evidence?

Audit logs may flow to log platforms, alerting systems, and other external services. Raw code may contain:
- User business logic (intellectual property)
- Hardcoded secrets/credentials
- Internal system paths

Therefore, the evidence field applies: truncation (200 character limit) + secret pattern masking (replaced with `***`).

---

## 8. Relationship with Other Security Components

Script Safety Guard is not an isolated module but one layer in the Agent Runtime security system. Understanding its division of labor with other components is a prerequisite for using it correctly.

### 8.1 Component Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                          Agent Runtime                                  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    Filter Chain (Request Pipeline)                 │  │
│  │  ┌──────────┐  ┌────────────────────┐  ┌──────────┐             │  │
│  │  │ModelFilter│→│ScriptSafetyFilter  │→│ToolFilter│→ [Tool Exec] │  │
│  │  └──────────┘  └────────────────────┘  └──────────┘             │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                              │                                          │
│                    decision=ALLOW to proceed                            │
│                              ▼                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                CodeExecutor (Execution Layer)                      │  │
│  │   ┌──────────────────┐  ┌────────────────────┐  ┌─────────────┐ │  │
│  │   │UnsafeLocalExec   │  │ContainerExecutor   │  │CubeExecutor │ │  │
│  │   │(No sandbox, bare)│  │(Docker isolation)  │  │(Remote VM)  │ │  │
│  │   └──────────────────┘  └────────────────────┘  └─────────────┘ │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                              │                                          │
│                              ▼                                          │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                Telemetry (Observability Layer)                     │  │
│  │  OTel Span Attributes · Metrics (Counter/Histogram) · Audit JSONL │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

### 8.2 Component Responsibility Comparison

| Component | Nature | Timing | Core Responsibility |
|-----------|--------|--------|-------------------|
| **Script Safety Guard** | Static analysis engine (rule matching) | **Before** code execution | Determine whether code **intent** is dangerous |
| **Sandbox** | Runtime isolation environment | **During** code execution | Limit the actual **impact scope** of code behavior |
| **Filter Chain** | Request/response pipeline middleware | Before/after tool calls | **Integration carrier** for Safety Guard |
| **Telemetry** | Observability infrastructure | Throughout | One of Safety Guard's **output channels** |
| **CodeExecutor** | Code execution abstract interface | At execution time | Safety Guard's **protection target** |

### 8.3 Collaboration Details

#### Safety Guard ↔ Filter Chain

Filter Chain is Safety Guard's **integration point** in the Agent tool call pipeline. Safety Guard itself is purely an analysis engine (input: code, output: decision), while Filter is responsible for:
- Extracting script content from tool call arguments
- Invoking Guard to perform scanning
- Taking action based on the returned decision (pass / block / flag for review)

They have a "business logic" vs. "pipeline glue" relationship. Safety Guard is completely unaware of Filter's existence and can work independently in CodeExecutor Wrapper mode.

#### Safety Guard ↔ Telemetry

Telemetry is Safety Guard's **output consumer**, not a runtime dependency. Guard's check results flow into the Telemetry layer through:
- OTel Span attributes: recording decision, duration, finding count for each scan
- Metrics Counter: `safety_checks_total`, `safety_findings_total` for monitoring
- Audit JSONL: persistent audit trail

If Telemetry is unavailable, Guard works normally (fail-open principle); only observability degrades.

#### Safety Guard ↔ CodeExecutor

Safety Guard's core protection target is CodeExecutor. Regardless of which Executor implementation is used, Guard screens code **before handing it to the Executor**:

| CodeExecutor Implementation | Has Sandbox? | Safety Guard's Value |
|----------------------------|--------------|---------------------|
| `UnsafeLocalCodeExecutor` | ❌ No isolation | **Only defense** — once Guard is bypassed, code has full host control |
| `ContainerCodeExecutor` | ✅ Docker | **First line of defense** — prevents obviously dangerous code from starting containers, reduces attack surface |
| `CubeCodeExecutor` | ✅ Remote VM | **First line of defense** — reduces unnecessary remote execution overhead while providing audit trails |

#### Safety Guard ↔ Sandbox

This is the most easily confused relationship. They are **complementary but not interchangeable**:

| Dimension | Safety Guard (Static Analysis) | Sandbox (Runtime Isolation) |
|-----------|-------------------------------|----------------------------|
| Protection timing | Pre-execution | At-runtime |
| Protection method | Detects code **intent patterns** | Limits code's **actual capabilities** |
| Latency overhead | 1~5ms | Hundreds of ms to seconds (container/VM startup) |
| Deployment dependency | Zero (pure Python) | Requires Docker / remote VM service |
| Can be bypassed? | Yes (dynamic construction, encoding obfuscation) | Extremely difficult (OS-level isolation boundary) |

### 8.4 Why Safety Guard Cannot Replace Sandbox Isolation

This is a critical architectural insight. By analogy:

> **Safety Guard = Airport security screening** (checking luggage for dangerous items before boarding)
> **Sandbox = Blast-proof container in aircraft cargo** (even if screening misses something, an explosion won't damage the aircraft)

Specific reasons why it cannot be replaced:

**1. Dynamic Construction Bypass**

```python
# Safety Guard sees a getattr call — cannot identify actual purpose
func = getattr(__import__('os'), 'sys' + 'tem')
func('rm -rf /')
```

Static analysis can only see code's **textual form**, unable to trace runtime value flow. Sandbox doesn't care "how you construct it", only limits "what you can do".

**2. Encoding Obfuscation**

```python
import base64
eval(base64.b64decode('b3Muc3lzdGVtKCdybSAtcmYgLycp').decode())
```

Guard sees an `eval()` call (which triggers PROC-002), but if attackers use more covert execution paths (e.g., custom decoders), static analysis may fail to identify them. In a sandbox, even if eval executes, deletion is confined within the container.

**3. Cross-file/Cross-dependency Attacks**

```python
import malicious_lib  # Guard cannot trace what this library does internally
malicious_lib.do_something()
```

Guard only analyzes the current script, unable to recursively analyze imported third-party libraries. Sandbox isolation ensures that even if a library executes malicious code, impact is contained within the isolated environment.

**4. Unknown Attack Vectors (0-day)**

Guard can only detect **known patterns for which rules have been written**. When facing completely new attack techniques before the rule base is updated, Guard is powerless. Sandbox provides a **physical isolation boundary** that doesn't depend on prior knowledge of attack patterns.

**5. Resource Exhaustion Attacks**

```python
# Appears to be just a simple list operation
data = [0] * (10 ** 10)  # 40GB memory allocation
```

Guard can heuristically detect certain patterns (e.g., `10**10`), but cannot cover all code that causes resource exhaustion. Sandbox enforces hard limits on CPU, memory, and disk I/O via cgroups / VM resource quotas.

### 8.5 Correct Defense-in-Depth Model

Recommended production security layering:

```
LLM generates script
     │
     ▼
[Layer 1] Script Safety Guard — Static pre-screening (1~5ms, zero-cost filtering of 90%+ known dangers)
     │
     ▼
[Layer 2] Human Review — Manual confirmation (optional, handles NEEDS_HUMAN_REVIEW gray areas)
     │
     ▼
[Layer 3] Sandbox Execution — Sandboxed execution (Container / Cube, physical isolation boundary)
     │
     ▼
[Layer 4] Telemetry + Audit — Full recording (post-hoc audit, anomaly detection, compliance trails)
```

- **Layer 1 solves efficiency**: The vast majority of obviously dangerous scripts are quickly intercepted here, avoiding unnecessary container startup overhead.
- **Layer 3 solves reliability**: Even if Layers 1 and 2 both fail, malicious code damage is confined within the sandbox.
- **Both are indispensable**: Guard without Sandbox = total compromise once bypassed; Sandbox without Guard = container startup every time, lacking audit and pre-filtering capabilities.

---

## 9. Known Limitations

| Limitation | Description | Mitigation |
|-----------|-------------|------------|
| Dynamic construction bypass | `getattr(os, 'sys' + 'tem')('rm -rf /')` cannot be captured by AST | Use with runtime sandbox |
| Bash analysis precision | Bash has no standard AST; relies on regex, may miss complex pipes/variable expansion | Cover common dangerous patterns, continuously supplement |
| Cross-file analysis | Only analyzes single scripts, cannot trace import dependency chains | Focus on direct calls; indirect calls handled by runtime protection |
| False positive rate | Conservative policy may over-block legitimate operations | Precisely exclude via policy whitelists |
| Python / Bash only | JavaScript, Go, etc. not yet supported | Rule framework reserves language extension capability |
| No contextual semantic understanding | Cannot determine "whether this code's intent is reasonable" | Inherent limitation of static analysis; requires human review supplement |

---

## 10. Extending with New Rules

### 10.1 Steps Overview

1. **Define rule metadata**: Rule ID, risk category, severity level, supported languages
2. **Write rule class**: Inherit BaseRule, implement `scan()` method
3. **Register rule**: Use `@register_rule` decorator
4. **Add import**: Import new module in `rules/__init__.py`
5. **Write tests**: Cover positive cases (should trigger) and negative cases (should not trigger)

### 10.2 Rule Writing Guidelines

| Principle | Description |
|-----------|-------------|
| Single responsibility | One rule focuses on one risk pattern; don't mix multiple detection logics |
| No side effects | `scan()` is a pure function; don't modify context or perform I/O |
| Exception safe | Exceptions within rules should not propagate outward; catch internally and return empty list |
| Policy-aware | Rules should read policy config (whitelists, thresholds), not hardcode values |
| Meaningful evidence | Provide enough context to help users understand the issue, but don't include full source code |
| Precise line_number | Provide accurate line numbers whenever possible for easy location |

### 10.3 Rule ID Naming Convention

```
{CATEGORY_PREFIX}-{3-digit number}

Category prefixes:
  FS    → file_operations
  NET   → network
  PROC  → process
  DEP   → dependency
  RES   → resource
  SEC   → secrets
  GUARD → built-in guard (reserved)
```

---

## 11. Observability

### 11.1 Output Channels

| Channel | Format | Purpose |
|---------|--------|---------|
| Python Logger | Structured JSON | Real-time integration with SIEM / log platforms |
| Report file | JSON | Complete snapshot of a single check |
| Audit file | JSONL | Persistent audit trail |
| OTel Metrics | Counter + Histogram | Integration with Prometheus/Grafana dashboards |

### 11.2 Key Metrics

| Metric | Type | Meaning |
|--------|------|---------|
| safety_checks_total | Counter | Total check count (labeled by decision) |
| safety_check_duration_ms | Histogram | Scan duration distribution |
| safety_findings_total | Counter | Total findings (labeled by category + severity) |

---

## 12. FAQ

**Q: What happens when a script triggers multiple rules?**
A: All rules execute independently, collecting all Findings. The final decision is determined via Strictest-wins. The report lists all Findings so users can see the complete risk profile.

**Q: How to resolve false positives?**
A: Configure whitelists via policy files. For example, add legitimate domains to `network.allowed_domains`, or add allowed commands to `process.allowed_commands`.

**Q: Will Guard scan latency affect tool call response time?**
A: Typical scan latency is 1~5ms (depending on script size and rule count), negligible compared to network request latency.

**Q: Can I enable only a subset of rules?**
A: In the current version, all registered rules execute. To disable a specific rule, use the RuleRegistry's `unregister()` method at runtime.

**Q: Can scripts marked NEEDS_HUMAN_REVIEW actually execute?**
A: It depends on the integration layer's `block_on_review` configuration. Set to True for blocking (more secure), or False for pass-through with logging (more lenient).

---

## 13. Roadmap (Future Directions)

- [ ] Rule enable/disable switches (policy level)
- [ ] JavaScript / TypeScript language support
- [ ] Confidence-weighted aggregation (optional strategy)
- [ ] Custom rule hot-loading (external directory scanning)
- [ ] Integration with dynamic sandbox (static high-risk → trigger sandbox secondary verification)
