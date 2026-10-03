# LlmAgent 视觉理解（Vision）示例

本示例演示如何基于 `LlmAgent` 与支持视觉输入的模型（VLM）构建一个**带图片的多轮问答**助手：第一轮让模型阅读图片、提取求解所需的关键信息（如振幅、周期、相位等），**不解题**；第二轮让模型结合第一轮的信息与图片中的题目文本，完成解题。

## 关键特性

- **多模态消息**：用户消息中混合文本与图片（`Part.from_text` + `Part.from_bytes`），无需手写 base64 编码或 JSON 消息结构
- **多轮上下文**：两轮对话共享同一 `session_id`，模型可引用第一轮输出作为第二轮上下文
- **两轮分工**：第一轮提示词明确"只提取信息、不解题"，第二轮才要求解题，使两轮输出职责清晰、第一轮结果精简
- **流式事件处理**：通过 `runner.run_async(...)` 消费 partial/full event，打印模型输出
- **框架自动转换**：图片字节 → `inline_data` → base64 data URI → OpenAI `image_url` 格式（`detail=high` 保留高清细节）

## Agent 层级结构说明

本例是单 Agent 示例，不涉及多 Agent 分层路由：

```text
vision_agent (LlmAgent)
├── model: OpenAIModel（支持视觉输入的模型）
├── instruction: 视觉问答助手提示词
└── session: InMemorySessionService（两轮对话共享同一 session_id）
```

关键文件：

- [examples/llmagent_with_vision/agent/agent.py](./agent/agent.py)：构建 `LlmAgent`（挂载视觉模型）
- [examples/llmagent_with_vision/agent/config.py](./agent/config.py)：环境变量读取（API Key / Base URL / Model Name）
- [examples/llmagent_with_vision/agent/prompts.py](./agent/prompts.py)：系统提示词
- [examples/llmagent_with_vision/run_agent.py](./run_agent.py)：测试入口，执行两轮对话
- [examples/llmagent_with_vision/images/sample_problem.jpg](./images/sample_problem.jpg)：示例图片（带三角函数图像的数学题）

## 关键代码解释

这一节用于快速定位"多模态消息构造、多轮会话、事件输出"三条核心链路。

### 1) Agent 组装与模型配置（`agent/agent.py` + `agent/config.py`）

- 使用 `LlmAgent` 组装视觉问答助手，不挂载任何工具（纯视觉问答）
- 通过 `OpenAIModel` 接入 OpenAI 兼容的视觉模型端点
- 模型名 / Base URL / API Key 从环境变量读取（`TRPC_AGENT_*`）

### 2) 多模态消息构造（`run_agent.py`）

- 读取本地图片文件字节，通过 `Part.from_bytes(data=..., mime_type="image/jpeg")` 构造图片 part
- 与 `Part.from_text(text=...)` 混合，组成 `Content(parts=[...])` 作为用户消息
- `OpenAIModel` 在内部自动将图片字节转为 OpenAI `image_url` 格式，无需手写

### 3) 两轮对话与流式事件处理（`run_agent.py`）

- 两轮对话复用同一个 `session_id`，模型能引用第一轮的图像信息总结
- 两轮提问分工明确：第一轮只要求总结题目与提取图形关键信息（禁止解题），第二轮才要求完整解题
- 使用 `runner.run_async(...)` 消费事件流：
  - `event.partial=True` 时打印文本分片（流式输出）
  - 完整事件中打印工具调用 / 工具返回（本示例未挂载工具，正常只走分片路径）

## 环境与运行

### 环境要求

- Python 3.12+（实测在 Python 3.13 上运行通过）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/llmagent_with_vision/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME` — 需为支持视觉输入的模型（OpenAI 兼容的 VLM 均可）

### 运行命令

```bash
cd examples/llmagent_with_vision
python3 run_agent.py
# 或使用 uv：
# uv run run_agent.py
```

## 运行结果（实测）

以下为实测输出：

```text
🆔 Session ID: 4f24c8d0...
📝 Turn 1: Extract key information from the image
🤖 Assistant: Based on the image provided, here is the summary of the problem and the extracted key information from the figure.

**Problem Text Summary:**
*   **Function:** $f(x) = \cos(\omega x + \varphi)$
*   **Constraints:** $\omega > 0$ and $0 < \varphi < \pi$.
*   **Given:** A partial graph of the function is shown.
*   **Objective:** Calculate the value of $f\left(\frac{\pi}{3}\right)$.
*   **Options:**
    *   A. $-1$
    *   B. $-\frac{\sqrt{3}}{2}$
    *   C. $-\frac{\sqrt{2}}{2}$
    *   D. $-\frac{1}{2}$

**Extracted Information from the Figure:**
*   **Amplitude:** The maximum y-value shown is **1**, indicating an amplitude of $A=1$.
*   **Maximum Point:** There is a peak (maximum) located at $x = -\frac{\pi}{6}$. At this point, $f(x) = 1$.
*   **Zero Crossing:** The graph intersects the x-axis at $x = \frac{7\pi}{12}$. Looking at the slope, the function is increasing at this point (crossing from negative to positive).
*   **Geometric Relationship:**
    *   The horizontal distance from the maximum peak ($x = -\frac{\pi}{6}$) to the subsequent rising zero crossing ($x = \frac{7\pi}{12}$) represents $\frac{3}{4}$ of the period ($T$).
    *   Distance $\Delta x = \frac{7\pi}{12} - \left(-\frac{\pi}{6}\right) = \frac{7\pi}{12} + \frac{2\pi}{12} = \frac{9\pi}{12} = \frac{3\pi}{4}$.
*   **Implied Parameters:**
    *   Period $T = \pi$ (since $\frac{3}{4}T = \frac{3\pi}{4}$).
    *   Angular frequency $\omega = \frac{2\pi}{T} = 2$.
    *   Phase shift $\varphi$: At the peak $x = -\frac{\pi}{6}$, the argument of the cosine is $0$ (or $2k\pi$). So, $2(-\frac{\pi}{6}) + \varphi = 0 \Rightarrow \varphi = \frac{\pi}{3}$, which satisfies the condition $0 < \varphi < \pi$.
----------------------------------------
🆔 Session ID: 4f24c8d0...
📝 Turn 2: Solve the problem
🤖 Assistant: **Problem Analysis:**

The problem asks us to find the value of $f\left(\frac{\pi}{3}\right)$ for the function $f(x) = \cos(\omega x + \varphi)$, given its graph and the constraints $\omega > 0$ and $0 < \varphi < \pi$.

**1. Extract Information from the Graph:**
*   **Maximum Point:** The graph reaches a peak (maximum value $y=1$) at $x = -\frac{\pi}{6}$.
*   **Zero Crossing:** The graph intersects the x-axis at $x = \frac{7\pi}{12}$. Looking at the curve, it is coming up from a minimum (a "valley") and crossing the axis with a positive slope. This point represents the start of the next upward cycle phase for the cosine function (specifically, the phase corresponding to $\frac{3\pi}{2}$).

**2. Determine the Period ($T$) and Angular Frequency ($\omega$):**
*   In a standard cosine wave $y = \cos(\theta)$, the sequence of key points starting from a maximum ($\theta=0$) is:
    *   Maximum ($\theta = 0$)
    *   Zero crossing (downward) ($\theta = \frac{\pi}{2}$)
    *   Minimum ($\theta = \pi$)
    *   Zero crossing (upward) ($\theta = \frac{3\pi}{2}$)
*   The horizontal distance from the Maximum to the subsequent Zero crossing (upward) corresponds to $\frac{3}{4}$ of the period ($T$).
*   From the graph, this distance is $\Delta x = \frac{7\pi}{12} - \left(-\frac{\pi}{6}\right)$.
*   $\Delta x = \frac{7\pi}{12} + \frac{2\pi}{12} = \frac{9\pi}{12} = \frac{3\pi}{4}$.
*   So, $\frac{3}{4}T = \frac{3\pi}{4} \implies T = \pi$.
*   The angular frequency is $\omega = \frac{2\pi}{T} = \frac{2\pi}{\pi} = 2$.

**3. Determine the Phase Shift ($\varphi$):**
*   At the maximum point $x = -\frac{\pi}{6}$, the argument of the cosine function must be an integer multiple of $2\pi$. Let's take the principal value ($k=0$).
*   $\omega x + \varphi = 0$
*   Substitute $\omega = 2$ and $x = -\frac{\pi}{6}$:
    $$2\left(-\frac{\pi}{6}\right) + \varphi = 0$$
    $$-\frac{\pi}{3} + \varphi = 0$$
    $$\varphi = \frac{\pi}{3}$$
*   Check the constraint: $0 < \frac{\pi}{3} < \pi$. This is valid.

**4. Define the Function:**
$$f(x) = \cos\left(2x + \frac{\pi}{3}\right)$$

**5. Calculate $f\left(\frac{\pi}{3}\right)$:**
*   Substitute $x = \frac{\pi}{3}$ into the function:
    $$f\left(\frac{\pi}{3}\right) = \cos\left(2\left(\frac{\pi}{3}\right) + \frac{\pi}{3}\right)$$
    $$f\left(\frac{\pi}{3}\right) = \cos\left(\frac{2\pi}{3} + \frac{\pi}{3}\right)$$
    $$f\left(\frac{\pi}{3}\right) = \cos\left(\frac{3\pi}{3}\right)$$
    $$f\left(\frac{\pi}{3}\right) = \cos(\pi)$$
*   We know that $\cos(\pi) = -1$.

**Conclusion:**
The value is $-1$, which corresponds to option A.

**Correct Answer:** **A**
----------------------------------------
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **多模态链路正常**：图片字节经 `Part.from_bytes` → `inline_data` → base64 data URI → OpenAI `image_url`（`detail=high`），模型准确读取了题目文本与图形特征
- **两轮分工正确**：第一轮输出仅为问题摘要与图形关键信息（含振幅、周期、ω、φ 等隐含参数提取），未进入最终求解；第二轮独立完成完整解题
- **解题结果正确**：由最大点与零点推导出 `ω = 2`、`φ = π/3`，代入得 `f(π/3) = cos(π) = -1`，与选项 A 匹配，并通过 y 截距与极小值位置交叉验证
- **多轮上下文生效**：第二轮结合第一轮总结与图片直接解题，无需重复读取图片信息

## 适用场景建议

- 图片 / 图表内容理解与描述
- 截图文字提取（OCR）、文档识别
- 视觉问答（VQA）与图像推理
- 数学图形理解（函数图像、几何图形）：提取图像关键信息 → 结合题目解题
- 需要"视觉 + 推理 + 多轮上下文"组合的 Agent 场景
