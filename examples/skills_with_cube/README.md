# Skills Cube 与 stage_inputs 示例

本示例演示在腾讯云 Agent 沙箱 Cube 工作区中执行 `skill_run`，并通过 `host://`、`workspace://`、`skill://` 等输入方案演示 `stage_inputs` 如何把本地输入上传/复制到远端沙箱。

## 关键特性

- `create_cube_sandbox_client(CubeClientConfig(auto_recover=True))`：创建远端 Cube 沙箱；底层 sandbox 过期/不存在时，`CubeSandboxClient` 会自动创建新 sandbox 并重试当前操作一次
- `agent/tools.py` 中 `build_cube_skill_run_payload` 生成固定形态的 `skill_run` 负载供模型调用
- `host://` 输入会从运行示例的本机路径上传到 Cube 沙箱，不依赖 Docker bind mount
- `run_agent.py` 会准备示例 `/tmp/skillrun-inputs/sales.csv`，demo 结束后销毁 Cube 沙箱

## Agent 层级结构说明

- 根节点：`LlmAgent`，挂载 `SkillToolSet`（Cube 运行时 + 技能仓库）
- 无子 Agent

## 关键代码解释

- `agent/tools.py`：通过 `CubeClientConfig(auto_recover=True)` 创建 `CubeSandboxClient`，再通过 `create_cube_workspace_runtime` 创建 workspace runtime
- `agent/agent.py`：异步创建 agent，并把 workspace runtime 返回给 runner 做最终销毁
- `run_agent.py`：组装含 `inputs` 数组的 JSON 提示词，驱动单次 `skill_run` 演示，并在 finally 中销毁沙箱

## 环境与运行

- Python 3.12；仓库根目录安装 Cube extra：`pip install -e '.[cube]'`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`
- 配置 Cube 环境变量：`CUBE_TEMPLATE_ID`、`E2B_API_URL`、`E2B_API_KEY`
- 可选：`SKILLS_ROOT`、`CUBE_EXECUTE_TIMEOUT`（默认 `30`）、`CUBE_IDLE_TIMEOUT`（默认 `600`）

```bash
cd examples/skills_with_cube
python3 run_agent.py
```

### 验证 sandbox 重建后的 Skill runtime 恢复

为了验证业务主动重建 sandbox 后，Skill 工具链仍能继续使用当前 workspace runtime，可以开启下面的环境变量：

```bash
SKILLS_WITH_CUBE_RECREATE_BETWEEN_RUNS=1 python3 run_agent.py
```

该模式会连续发起两次相同的 `skill_run` 请求：第一次正常运行；第二次请求前通过 workspace runtime 主动重建 Cube sandbox。只要第二次请求也能正常完成，就说明 Skill 工具链可以继续使用新的 runtime，而不是继续访问过期 sandbox。

### 验证 sandbox 失效后的自动恢复

为了验证更接近真实场景的自动恢复路径，可以开启下面的临时测试开关：

```bash
SKILLS_WITH_CUBE_KILL_BETWEEN_RUNS=1 python3 run_agent.py
```

该模式同样会连续发起两次相同的 `skill_run` 请求。第一次请求成功后，示例会直接 kill 远端 Cube sandbox，但保留本地 `CubeSandboxClient` 中的旧句柄。第二次请求继续使用旧句柄访问远端 sandbox，此时 Cube 会返回类似 `Code.unknown: The requested resource does not exist` 的错误；如果 `auto_recover=True` 生效，日志中会出现：

```txt
Cube sandbox expired; recreating sandbox client: Code.unknown: The requested resource does not exist
Cube sandbox client using sandbox: <new-sandbox-id>
```

随后第二次 `skill_run` 仍应返回 `exit_code=0`，说明旧 sandbox 被平台/外部销毁后，`CubeSandboxClient` 已自动恢复并继续执行当前操作。

注意：`SKILLS_WITH_CUBE_KILL_BETWEEN_RUNS` 是为了验证恢复机制而加入的临时代码，会使用私有句柄直接 kill 远端 sandbox。正式提交示例或生产代码时可以删除该测试开关及对应 helper。

## 期望运行结果

```txt
[START] skills_with_cube
...
created Cube sandbox ...
...
🔧 [Invoke Tool:: skill_run({... 'inputs': [
  'host:///tmp/skillrun-inputs/sales.csv',
  'workspace://skills/python-math/SKILL.md',
  'skill://python-math/scripts/fib.py',
], ...})
📊 [Tool Result: {
  'stdout': '', 'stderr': '', 'exit_code': 0,
  'output_files': [
    {'name': 'out/fib.txt', 'content': '0\n1\n1\n2\n3\n5\n8\n13\n21\n34\n', ...},
    {'name': 'out/staged_inputs_tree.txt', 'content':
      'work/inputs:\nsales.csv\n---\nwork/staged_inputs:\nfib.py\npython-math_skill.md\n', ...},
  ],
  ...
}]
...
```

## 结果分析（是否符合要求）

符合本示例测试要求：Cube 沙箱成功创建并完成 `skill_run` 调用链；`host://` / `workspace://` / `skill://` 三种 input scheme 都成功落入远端工作区，输出文件 `out/fib.txt` 和 `out/staged_inputs_tree.txt` 正常产出，进程以 `exit_code=0` 结束。

如果使用 `SKILLS_WITH_CUBE_KILL_BETWEEN_RUNS=1`，还需要确认第二次请求中出现自动恢复日志，并且第二次 `skill_run` 仍然成功。这表示真实的“旧 sandbox 不存在 -> client 自动重建 -> 当前操作重试”链路通过。

## 适用场景建议

- 需要在远端 Cube 沙箱内执行技能、并验证本地输入上传到沙箱时参考本示例
- 调试 `workspace://` 时应确保源文件已存在于当前 workspace，再复制或链接到目标路径
- 长生命周期 agent 建议开启 `CubeClientConfig(auto_recover=True)`，避免 Cube sandbox 因超时或平台清理后导致后续技能调用持续失败
- 自动恢复会创建全新的 sandbox，远端 workspace 内容不会自动从旧 sandbox 迁移；Skill staging 和 workspace 创建逻辑需要能在新 sandbox 上重新执行
