# System One TypeSafe 与 Laya 示例

本示例用同一组客服工单问题，分别调用官方 TypeSafe API 和本地 Laya HTTP 服务。问题类型覆盖 `Choice`、`Score` 和 `Noul`。Laya 路径还会打印 checkpoint 路由，并对两条工单调用 `system_one_batch`。

## 关键特性

- **同一组问题，两个客户端**：`SystemOneClient.typesafe` 调用官方 API，`SystemOneClient.laya` 调用 `laya-serve`
- **三种答案**：部门用 `Choice`，紧急程度用 `Score`，是否退款用 `Noul`，再用 `choice_answer`、`score_answer`、`noul_answer` 按问题名取出
- **Laya 可省略鉴权和模型**：不设置 `LAYA_API_KEY` 时不发送 `Authorization`；不设置 `LAYA_MODEL` 时由服务端选择 checkpoint
- **Laya 批量**：`system_one_batch` 一次提交两条工单。官方后端不提供该接口，本示例只在 Laya 路径调用它
- **缺配置即跳过**：`TYPESAFE_API_KEY` 为空时跳过 TypeSafe，`LAYA_BASE_URL` 为空时跳过 Laya，另一侧仍会运行

## 调用结构

```text
run.py
├── questions: department (Choice), urgency (Score), refund (Noul)
├── typesafe: SystemOneClient.typesafe -> system_one
└── laya: SystemOneClient.laya -> system_one, system_one_batch
                                 └── response.routing
```

关键文件：

- [examples/systemone_model/config.py](./config.py)：读取两个客户端的环境变量
- [examples/systemone_model/run.py](./run.py)：构造问题、调用客户端并打印答案
- [examples/systemone_model/.env](./.env)：环境变量占位

## 关键代码解释

### 1) 官方 TypeSafe 客户端（`run.py`）

- `SystemOneClient.typesafe(api_key=..., base_url=..., default_model=...)` 创建客户端
- `api_key` 来自 `TYPESAFE_API_KEY`。`base_url` 和 `default_model` 留空时，由官方 SDK 使用它自己的默认地址和模型 `jev-latest`
- 用 `async with` 保证客户端关闭
- 对一条工单调用 `system_one`

### 2) Laya 客户端（`run.py`）

- `SystemOneClient.laya(base_url, api_key=..., default_model=...)` 创建客户端
- `base_url` 是服务根地址，例如 `http://127.0.0.1:8000`
- 单条结果若带 `routing`，打印 checkpoint 名称和仓库
- 随后用同一组问题调用 `system_one_batch`

### 3) 读取答案（`run.py`）

- `choice_answer(response, "department").choice` 是选中的部门标签
- `score_answer(response, "urgency").score` 是紧急程度分数
- `noul_answer(response, "refund").noul` 是是否退款的 0/1 答案

## 环境要求

- Python3.10+，推荐 Python3.12
- TypeSafe 路径需要可访问官方 API 的 key
- Laya 路径需要已启动的 `laya-serve`。安装项目时已包含 `typesafe-sdk`

## 构建步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
./build.sh
source .venv/bin/activate
```

## 运行步骤

### 配置环境变量

在 [examples/systemone_model/.env](./.env) 中配置（或通过 `export`）：

| 变量 | 必填 | 说明 |
|------|------|------|
| `TYPESAFE_API_KEY` | TypeSafe 必填 | 官方 API key。为空时跳过 TypeSafe |
| `TYPESAFE_BASE_URL` | 否 | 官方 API 地址。为空时使用 SDK 默认地址 |
| `TYPESAFE_DEFAULT_MODEL` | 否 | 默认模型。为空时使用 `jev-latest` |
| `LAYA_BASE_URL` | Laya 必填 | Laya 服务根地址，例如 `http://127.0.0.1:8000` |
| `LAYA_API_KEY` | 否 | 为空时不发送 `Authorization` |
| `LAYA_MODEL` | 否 | 为空时由 Laya 选择 checkpoint |

### 运行命令

```bash
cd examples/systemone_model
python3 run.py
```

## 运行结果（示例）

下面是一次成功调用时的输出形态。部门标签、分数和 `noul` 的具体取值由服务端模型决定。

```text
============================================================
Backend: typesafe
============================================================
state: We were billed twice. Please refund the duplicate.
department: billing
urgency: 1.4
refund: 1
model: jev-latest
usage: input_tokens=120 output_tokens=0
----------------------------------------
============================================================
Backend: laya
============================================================
state: We were billed twice. Please refund the duplicate.
department: billing
urgency: 1.7
refund: 1
model: laya-rl-agent
usage: input_tokens=300 output_tokens=0
routing: model=multilingual repo=convaiinnovations/laya-multilingual
--- batch ---
[1/2]
state: We were billed twice. Please refund the duplicate.
department: billing
urgency: 1.7
refund: 1
model: laya-rl-agent
usage: input_tokens=300 output_tokens=0
routing: model=multilingual repo=convaiinnovations/laya-multilingual
[2/2]
state: The dashboard has been down since this morning.
department: technical
urgency: 2.0
refund: 0
model: laya-rl-agent
usage: input_tokens=280 output_tokens=0
routing: model=english repo=convaiinnovations/laya-english
----------------------------------------
```

## 结果分析（是否符合要求）

跑通后可以核对这几件事：

- **两个客户端都能回答同一组问题**：TypeSafe 与 Laya 都打印 `department`、`urgency`、`refund`
- **答案类型正确**：部门是 `criteria` 里的标签，紧急程度是分数，退款是 `noul`
- **Laya 带回路由**：`routing` 给出 checkpoint 名称；服务端报告仓库时一并打印
- **批量条数一致**：`system_one_batch` 的两条结果与提交的两条工单一一对应
- **缺配置不会拖垮另一条路径**：只配置其中一个后端时，另一条打印 `[SKIP]`

## 适用场景建议

- 验证 System One 问题构造，以及 TypeSafe / Laya 两个客户端的单次调用：适合使用本示例
- 验证 Laya 的 checkpoint 路由和 `system_one_batch`：适合使用本示例
- 需要把分类结果接到 `LlmAgent` 工具循环里：本示例只覆盖客户端调用，可把 `run.py` 里的问题构造和答案读取搬进自己的工具函数
