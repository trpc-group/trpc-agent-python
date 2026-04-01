# TeamAgent 取消功能示例

本示例演示 TeamAgent 的取消功能，展示如何协作式地取消正在运行的Team智能体执行。

## 功能说明

本示例展示了 TeamAgent 的取消机制，包含三个真实场景:
- **Leader规划期间取消**: 在Team Leader委派任务前触发取消
- **Member执行期间取消**: 在Team Member执行过程中触发取消

Team会在下一个检查点(Leader或Member)停止，并保存部分进度、活动上下文和取消事件到Team记忆中。

## 环境要求

Python版本: 3.10+(强烈建议使用3.12)

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 在 `.env` 文件中设置环境变量(也可以通过export设置):
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/team_with_cancel/
python3 run_agent.py
```

## 预期行为

本示例演示三个场景:

1. 场景1:在Leader规划过程取消 → 保存Leader部分响应和取消记录
2. 场景2:在Member执行过程取消 → 保存Member部分响应到Team记忆

输出如下所示:

```bash
📋 Scenario 1: Cancel During Leader planning (TeamAgent)
--------------------------------------------------------------------------------
🆔 Session ID: 71b89a00...
📝 User Query 1: Introduce yourself in details.

⏳ Waiting for first 10 events...
🤖 Team: 
[content_team_with_cancel] I am your content team leader, responsible for coordinating tasks between researchers and writers to create high-quality content tailored
⏳ [Received 10 events, triggering cancellation...]
 to your needs
⏸️  Requesting cancellation after 10 events...
[2026-01-13 14:17:25][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:98][1358209] Run marked for cancellation (app_name: content_team_cancel_demo)(user: demo_user)(session: 71b89a00-0ac8-4e65-937f-3566c338872f)

⏳ [Received 11 events, triggering cancellation...]
. Here's[2026-01-13 14:17:25][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:215][1358209] Cancelling run for session 71b89a00-0ac8-4e65-937f-3566c338872f
[2026-01-13 14:17:25][INFO][trpc_agent][trpc_agent/teams/_team_agent.py:459][1358209] TeamAgent 'content_team_with_cancel' cancelled during leader planning

⏳ [Received 12 events, triggering cancellation...]
[2026-01-13 14:17:25][INFO][trpc_agent][trpc_agent/runners.py:351][1358209] Run for session 71b89a00-0ac8-4e65-937f-3566c338872f was cancelled, Run for session 71b89a00-0ac8-4e65-937f-3566c338872f was cancelled
⏳ [Received 13 events, triggering cancellation...]

❌ Team execution was cancelled: Run for session 71b89a00-0ac8-4e65-937f-3566c338872f was cancelled

[2026-01-13 14:17:25][INFO][trpc_agent][trpc_agent/runners.py:147][1358209] Cancel completed for user_id demo_user, session 71b89a00-0ac8-4e65-937f-3566c338872f
✓ Cancellation requested: True

💡 Result: Leader's partial response and cancellation record saved to team memory

📝 User Query 2: what happened?

🤖 Team: 
[content_team_with_cancel] It seems the previous task was interrupted by a cancellation before I could fully introduce myself. Let me complete that introduction now.

---

I am your content team leader, responsible for coordinating tasks between researchers and writers to create high-quality content tailored to your needs. Here's a detailed introduction:

1. **Role**: I oversee the content creation process, ensuring that research is thorough and writing is engaging and well-structured.
2. **Team Members**:
   - **Researcher**: An expert at finding and gathering accurate, relevant information.
   - **Writer**: A skilled professional who crafts compelling, well-organized content.
3. **Process**:
   - I analyze your requests to determine what needs to be done.
   - I delegate tasks to the appropriate team members (researcher or writer).
   - I combine their work to deliver a polished final result.
4. **Cancellation Handling**: If a task is interrupted, I can resume from where we left off in the next conversation.

Let me know how I can assist you further!
💡 Result: Team can respond with context from previous cancelled run
--------------------------------------------------------------------------------

📋 Scenario 2: Cancel During Member Tool Execution (TeamAgent)
--------------------------------------------------------------------------------
🆔 Session ID: 042335a7...
📝 User Query 1: Research renewable energy and write a short article about it. Research should be simple.

⏳ Waiting for member tool execution to start...
🤖 Team: 
[content_team_with_cancel] I will delegate the research task to the researcher first to gather simple information about renewable energy. Then, I will pass that information to the writer to create a short article. Let me proceed with the delegation.
🔧 [content_team_with_cancel] Invoke Tool: delegate_to_member({'member_name': 'researcher', 'task': 'Research simple and key information about renewable energy, focusing on its types, benefits, and current trends. Keep the research concise and easy to understand.'})
📊 [content_team_with_cancel] Tool Result: {'result': '{"marker":"__TEAM_DELEGATION__","action":"delegate_to_member","member_name":"researcher","task":"Research simple and key information about renewable energy, focusing on its types, benefits, and current trends. Keep the research concise and easy to understand."}'}

🔧 [researcher] Invoke Tool: search_web({'query': 'types of renewable energy benefits and current trends 2023'})
⏳ [Member tool 'search_web' detected...]

[researcher] [Researcher Tool: searching for 'types of renewable energy benefits and current trends 2023'...]

⏸️  Member tool detected! Requesting cancellation during member execution...
[2026-01-13 14:17:32][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:98][1358209] Run marked for cancellation (app_name: content_team_cancel_demo)(user: demo_user)(session: 042335a7-f9cd-4126-8cad-6142afea6430)
[2026-01-13 14:17:33][WARNING][trpc_agent][trpc_agent/runners.py:149][1358209] Cancel wait timeout (1.0s) reached for user_id demo_user, session 042335a7-f9cd-4126-8cad-6142afea6430. The execution may still be running.
✓ Cancellation requested: True
[Researcher Tool: search completed for 'types of renewable energy benefits and current trends 2023']
📊 [researcher] Tool Result: {'query': 'types of renewable energy benefits and current trends 2023', 'results': [{'title': 'About types of renewable energy benefits and current trends 2023 - Overview', 'snippet': 'Comprehensive information about types of renewable energy benefits and current trends 2023...'}, {'title': 'Latest developments in types of renewable energy benefits and current trends 2023', 'snippet': 'Recent news and updates on types of renewable energy benefits and current trends 2023...'}], 'total_results': 2}
[2026-01-13 14:17:35][INFO][trpc_agent][trpc_agent/cancel/_cancel.py:215][1358209] Cancelling run for session 042335a7-f9cd-4126-8cad-6142afea6430
[2026-01-13 14:17:35][INFO][trpc_agent][trpc_agent/teams/_team_agent.py:459][1358209] TeamAgent 'content_team_with_cancel' cancelled during delegation to researcher
[2026-01-13 14:17:35][INFO][trpc_agent][trpc_agent/runners.py:351][1358209] Run for session 042335a7-f9cd-4126-8cad-6142afea6430 was cancelled, Run for session 042335a7-f9cd-4126-8cad-6142afea6430 was cancelled

❌ Team execution was cancelled: Run for session 042335a7-f9cd-4126-8cad-6142afea6430 was cancelled


💡 Result: Member's partial response recorded in team memory with cancellation context

📝 User Query 2: what happened?

🤖 Team: 
[content_team_with_cancel] The user's task is not finished. The research on renewable energy was interrupted by cancellation before any output was received. I will now continue the execution by delegating the research task again to the researcher.  

Let me proceed with this.
🔧 [content_team_with_cancel] Invoke Tool: delegate_to_member({'member_name': 'researcher', 'task': 'Research simple and key information about renewable energy, focusing on its types, benefits, and current trends. Keep the research concise and easy to understand.'})
📊 [content_team_with_cancel] Tool Result: {'result': '{"marker":"__TEAM_DELEGATION__","action":"delegate_to_member","member_name":"researcher","task":"Research simple and key information about renewable energy, focusing on its types, benefits, and current trends. Keep the research concise and easy to understand."}'}

🔧 [researcher] Invoke Tool: search_web({'query': 'types of renewable energy benefits and current trends 2023'})

[researcher] [Researcher Tool: searching for 'types of renewable energy benefits and current trends 2023'...]
[Researcher Tool: search completed for 'types of renewable energy benefits and current trends 2023']
📊 [researcher] Tool Result: {'query': 'types of renewable energy benefits and current trends 2023', 'results': [{'title': 'About types of renewable energy benefits and current trends 2023 - Overview', 'snippet': 'Comprehensive information about types of renewable energy benefits and current trends 2023...'}, {'title': 'Latest developments in types of renewable energy benefits and current trends 2023', 'snippet': 'Recent news and updates on types of renewable energy benefits and current trends 2023...'}], 'total_results': 2}
Here’s a concise overview of renewable energy, covering its types, benefits, and current trends:

...
--------------------------------------------------------------------------------

```
