# SkillExecTool 示例

本示例使用单个 `interactive-report` Skill，验证 `SkillExecTool` 的交互式
stdin、命令执行、输出文件收集和 Artifact 保存。

## 关键特性

- `skill_load` 加载唯一 Skill。
- `skill_exec` 启动交互式 Python 程序，并通过初始 `stdin` 回答两个问题。
- `output_files=["out/report.txt"]` 验证进程结束后的文件收集。
- `InMemoryArtifactService` 验证 `save_as_artifacts` 和 `artifact_files`。

## Agent 层级结构说明

- 根节点：`LlmAgent`（`skill_exec_agent`）。
- 挂载 `SkillToolSet` 和本地技能仓库，无子 Agent。

## 关键代码解释

- `run_agent.py`：要求模型按固定参数调用 `skill_exec`，并打印工具结果。
- `agent/tools.py`：使用本地 Workspace Runtime 构造 `SkillToolSet`。
- `skills/interactive-report/`：唯一 Skill，包含交互脚本和产物说明。

## stdin 如何工作

`run_agent.py` 要求模型在调用 `skill_exec` 时传入：

```json
{
  "command": "python3 scripts/create_report.py",
  "stdin": "release-1.2.0\n2\n"
}
```

`SkillExecTool` 会把该字符串作为进程启动时的初始 stdin。交互脚本连续调用两次
`input()`：

1. `release-1.2.0` 回答 `Release name`。
2. `2` 回答 `Report mode`，表示生成详细报告。

其效果等价于：

```bash
printf 'release-1.2.0\n2\n' | python3 scripts/create_report.py
```

这里演示的是启动时一次性提供 stdin。对于程序启动后才出现的动态问题，应先通过
`skill_exec` 获取 `session_id`，再使用 `skill_write_stdin` 分次输入，并通过
`skill_poll_session` 获取后续输出和最终产物。

## 环境要求

- Python3.10+，推荐 Python3.12

## 构建步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
./build.sh
source .venv/bin/activate
```

## 运行步骤

### 配置环境变量

通过环境变量或当前目录的 `.env` 配置：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`
- 可选：`SKILLS_ROOT` 指向技能根目录

### 运行命令

```bash
cd examples/skills_with_exec_tool
python3 run_agent.py
```

## 预期结果

```txt
[Invoke Tool: skill_load(...)]
[Invoke Tool: skill_exec({
  "skill": "interactive-report",
  "command": "python3 scripts/create_report.py",
  "stdin": "release-1.2.0\n2\n",
  "output_files": ["out/report.txt"],
  "save_as_artifacts": true
})]
[Tool Result: {
  "status": "exited",
  "exit_code": 0,
  "result": {
    "output_files": [{
      "name": "out/report.txt",
      "content": "Release: release-1.2.0\nMode: detailed\n..."
    }],
    "artifact_files": [{
      "name": "skill-exec-demo/out/report.txt",
      "version": 0
    }]
  }
}]
```

验证通过需要同时满足：

- `status=exited` 且 `exit_code=0`。
- `output_files` 包含非空的 `out/report.txt`。
- `artifact_files` 包含 `skill-exec-demo/out/report.txt`。

## 适用场景建议

- 交互式 CLI、安装向导、选择菜单等需要 stdin/TTY 的 Skill。
- 长时间运行、需要分段输出或最终收集产物的 Skill。
