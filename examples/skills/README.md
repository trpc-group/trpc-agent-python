# Skills 示例

本示例演示在单智能体 `LlmAgent` 中通过 `SkillToolSet` 与技能仓库使用 Agent Skills（`skill_load`、`skill_run` 等），依次完成文件摘要、Fibonacci、打包与 CSV 分析等多轮任务。

## 关键特性

- 本地工作区执行 `skill_run`，收集 stdout/stderr 与输出文件
- 支持 `SKILLS_ROOT` 指向技能目录（默认 `./skills`）
- `Runner` + `InMemorySessionService` 流式打印工具调用与结果

## Agent 层级结构说明

- 根节点：`LlmAgent`（`skill_run_agent`），挂载 `SkillToolSet` 与 `skill_repository`
- 无子 Agent；任务由该单智能体通过技能工具完成

## 关键代码解释

- `run_agent.py`：准备 `/tmp` 示例文件，对多条用户提示分别 `runner.run_async` 并打印事件
- `agent/tools.py`：`create_local_workspace_runtime` + `create_default_skill_repository` 构造 `SkillToolSet`
- `agent/agent.py`：将 `skill_tool_set` 与 `skill_repository` 绑定到 `LlmAgent`

## 环境与运行

- Python 3.10+；仓库根目录执行 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`（可用 `.env`）
- 可选：`SKILLS_ROOT` 指向技能根目录

```bash
cd examples/skills
python3 run_agent.py
```

## 运行结果（实测）


```
[START] skills
...
🔧 [Invoke Tool:: skill_load({'skill_name': 'user-file-ops'})]
...
🔧 [Invoke Tool:: skill_run({'skill': 'user-file-ops', 'command': 'bash scripts/summarize_file.sh work/inputs/user-notes.txt out/user-notes-summary.txt', ...
...
🔧 [Invoke Tool:: skill_run({'skill': 'python-math', 'command': 'python3 scripts/fib.py 10 > out/fib.txt', ...
📊 [Tool Result: {... 'content': '0\n1\n1\n2\n3\n5\n8\n13\n21\n34\n', ...}]
...
[END] skills (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：进程以 `exit_code=0` 结束；多会话下 `skill_load` / `skill_run` 均返回成功，Fibonacci 与摘要等输出与工具结果一致，说明技能链路与本地工作区执行正常。

## 适用场景建议

- 需要把「可版本管理的技能包 + 沙箱命令执行」交给大模型编排时使用
- 适合作为接入自有 `SKILLS_ROOT` 或扩展更多技能的起点
