# Phase 7 — 接入真实 LLM（可选，配置在 `.env`）

## 做了什么

让 Code Review Agent **能够接入真实 LLM 做二次研判**，同时保证：
**没有真实模型 API Key 时，解析 / 沙箱 / 落库链路（含 `--dry-run`）照常工作，所有测试无 Key 跑通。**

### 新增 `llm/` 包
- **`llm/config.py`** — `LlmConfig` + `load_llm_config(env_path)`：用 `python-dotenv` 从项目根 `.env` 读取 `LLM_ENABLED` / `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` / `LLM_TIMEOUT`。
- **`llm/client.py`** — `RealLlm`（懒加载 `openai.AsyncOpenAI`，**任意 OpenAI 兼容端点**：OpenAI / Azure / 本地 vLLM / 内部网关）+ `FakeLlm`（no-op）+ 工厂 `get_llm_client(enable, env_path)`。**无 Key 或 `LLM_ENABLED=false` → 自动返回 FakeLlm**，绝不实例化真实 client。`_parse_verdicts` 对 code fence / 乱码做容错降级。
- **`llm/triage.py`** — `LlmTriage.run(dedupe_result, diff_text)`：只研判 `needs_human_review` 低置信度档位。
  - `false_positive` → 丢弃
  - `real` → 更新置信度、`source` 打 `llm` 标、模型解释追加进 `recommendation`、按新置信度重新分桶（≥0.8→findings / ≥0.6→warnings）
  - 调用失败 / 未启用 → 原样返回（**no-op 降级**）
  - 喂模型前的 diff 先经 `mask_secrets` 脱敏，密钥不外泄

### `agent.py` 接入
- 新增 CLI：`--enable-llm`（覆盖 `.env`）、`--llm-env`（指定 `.env` 路径）
- 在 dedupe（step6）与落库（step7）之间插入 **step 6.5 `l5b_llm_triage`**（trace_stage 包裹，`is_enabled=False` 时打印 disabled 并跳过）

### 配置文件
- `.env`（安全默认值：`LLM_ENABLED=false` + 空 Key）
- `.env.example`（全量变量 + 注释说明）
- `README.md` §3 注记 + 新增 **§4.5 LLM 二次研判（可选）**

## 验证
- **全量回归 175 用例 / 0 失败**（原 162 + 新增 Phase7 13）
  - 无 Key → FakeLlm、FakeLlm 透传/短路、`_parse_verdicts`（fence/乱码/clamp）、RealLlm 注入 mock 验证提级+丢弃+失败降级+脱敏外发
- 真实模式冒烟（fixture=security，**无 Key**）：日志 `llm triage: disabled (LLM_ENABLED=false or no API key)`，task done、报告产出、`exceptions={}`

## 用法
```bash
# 1. 复制并填写 .env
cp .env.example .env
# 编辑：LLM_ENABLED=true，填入 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL

# 2. 跑（.env 启用后自动接真实模型）
$VENV agent.py --diff-file ./my_change.diff --mode real --db-path ./cr.db

# 或 CLI 临时开启（覆盖 .env）
$VENV agent.py --fixture security --enable-llm
```
