# 多Agent从上一次Agent继续执行示例

本示例演示 RunConfig 中 `start_from_last_agent` 功能的使用。

## 功能说明

当 RunConfig 中设置 `start_from_last_agent=True` 时，会话中的后续问题将由上一次活跃的子Agent处理

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 在 `.env` 文件中设置环境变量（也可以通过export设置）:
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/multi_agent_start_from_last/
python3 run_agent.py
```

## 预期行为

本示例在同一个会话中发送3条消息：

1. "I'm interested in your smart speakers" → coordinator路由到 sales_consultant
2. "What about the display products?" → 直接由 sales_consultant 处理（不再经过coordinator）
3. "Are there any discounts available?" → 同样直接由 sales_consultant 处理

输出如下所示：

```
((venv) ) [root@VM-94-249-tencentos /data/work/ai/trpc-agent-dev/trpc-agent-dev1/examples/multi_agent_start_from_last]$ python3 ./run_agent.py
Multi-Agent Start From Last Agent Example
Shows how follow-up queries stay with the last active sub-agent

============================================================
Multi-Agent Demo: start_from_last_agent=True
============================================================

Session ID: 6e4c64d7...

This demo shows how follow-up questions stay with the
last active agent instead of routing back to the coordinator.

------------------------------------------------------------

[Turn 1] User: I'm interested in your smart speakers. What do you have?
----------------------------------------
[coordinator] Tool: transfer_to_agent({'agent_name': 'sales_consultant'})
[coordinator] Result: {'transferred_to': 'sales_consultant'}...
[sales_consultant] Tool: get_product_info({'product_type': 'speakers'})
[sales_consultant] Result: {'result': 'Smart Speaker Pro - Voice control, AI assistant, multi-room audio - $199'}...
[sales_consultant] We offer the **Smart Speaker Pro**, which features voice control, an AI assistant, and multi-room audio capabilities. It's priced at **$199**. Let me know if you'd like more details or assistance!

[Turn 2] User: What about the display products?
----------------------------------------
[sales_consultant] Tool: get_product_info({'product_type': 'displays'})
[sales_consultant] Result: {'result': 'Smart Display 10 - 10-inch touch screen, video calls, smart home hub - $399'}...
[sales_consultant] We also have the **Smart Display 10**, which includes a 10-inch touch screen, video call capabilities, and functions as a smart home hub. It's priced at **$399**. Would you like more information or a comparison between the two products?

[Turn 3] User: Are there any discounts available?
----------------------------------------
[sales_consultant] Currently, we don't have any active discounts on the Smart Speaker Pro or the Smart Display 10. However, I can keep you updated if any promotions become available in the future. Let me know if you'd like to proceed with a purchase or need further assistance!

============================================================
Demo completed!
============================================================
```
