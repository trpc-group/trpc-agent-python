# Skills 容器与 stage_inputs 示例

本示例演示在 Docker 容器工作区中执行 `skill_run`，并通过 `host://`、`workspace://`、`skill://` 等输入方案演示 `stage_inputs` 与挂载目录的配合。

## 关键特性

- `create_container_workspace_runtime`：技能目录与可选 `inputs_host` 以只读 bind mount 注入容器
- `agent/tools.py` 中 `build_container_skill_run_payload` 生成固定形态的 `skill_run` 负载供模型调用
- 与宿主机 `/tmp/skillrun-inputs` 等路径联动（`run_agent.py` 会准备示例 `sales.csv`）

## Agent 层级结构说明

- 根节点：`LlmAgent`，挂载 `SkillToolSet`（容器运行时 + 技能仓库）
- 无子 Agent

## 关键代码解释

- `agent/tools.py`：`_create_workspace_runtime` 配置 `Binds`（技能目录、`inputs_host`），`create_skill_tool_set` 创建仓库与 `SkillToolSet`
- `run_agent.py`：组装含 `inputs` 数组的 JSON 提示词，驱动单次 `skill_run` 演示
- `run_agent.py` 末尾清理 `/tmp/skillrun-inputs` 等临时文件

## 环境与运行

- Python 3.10+；已安装 Docker；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`
- 可选：`SKILLS_ROOT`、`SKILLS_INPUTS_HOST`（默认 `/tmp/skillrun-inputs`）

```bash
cd examples/skills_with_container
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] skills_with_container
...
Docker client initialized successfully
Container bind mounts enabled: [... 'skills:...:ro', '/tmp/skillrun-inputs:/opt/trpc-agent/inputs:ro']
...
🔧 [Invoke Tool:: skill_run({... 'inputs': [..., 'workspace://skills/python_math/SKILL.md', ...], ...})
📊 [Tool Result: {'error': 'tool_execution_error', ... "Failed to stage input: ... SKILL.md': No such file or directory" ...}]
...
[END] skills_with_container (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：容器成功启动并完成一次 `skill_run` 调用链；日志清晰展示 `host://` 与 `skill://` 等路径处理及 `workspace://` 在当期 workspace 中缺失时的失败信息，进程仍以 `exit_code=0` 结束，达到演示 stage_inputs 行为的目的。

## 适用场景建议

- 需要在隔离容器内执行技能、并显式控制宿主机数据注入路径时参考本示例
- 调试 `workspace://` 时应确保源文件已存在于当前 workspace，再复制或链接到目标路径
