# Skills 容器示例说明

本文档说明 `examples/skills_with_container/` 目录如何在容器环境下运行，并重点解释输入注入相关机制。

## 一、容器环境运行方式

本示例通过 `agent/tools.py` 中的 `create_container_workspace_runtime()` 构建容器运行时。核心流程如下：

1. 启动一个长期存活的容器（用于执行 `skill_run`）。
2. 为每次执行创建独立 workspace，例如：
   - `/tmp/run/ws_<session>_<timestamp>/skills`
   - `/tmp/run/ws_<session>_<timestamp>/work`
   - `/tmp/run/ws_<session>_<timestamp>/out`
3. `skill_run` 执行时，命令在该 workspace 内运行，并注入环境变量：
   - `SKILLS_DIR`
   - `WORK_DIR`
   - `OUTPUT_DIR`
   - `RUN_DIR`

运行入口：

```bash
cd examples/skills_with_container
python3 run_agent.py
```

## 二、注入文件路径映射关系

当前示例在 `agent/tools.py` 中会生成 `host_config["Binds"]`，并挂载到容器：

- `skill_spec`：`<skills_root>:/opt/trpc-agent/skills:ro`
- `inputs_host`（可选）：`<inputs_host>:/opt/trpc-agent/inputs:ro`

随后在每个 workspace 中，输入会被 stage 到 `dst` 指定位置。常见映射关系如下：

- 宿主机输入文件：`/tmp/skillrun-inputs/sales.csv`
- 容器挂载路径：`/opt/trpc-agent/inputs/sales.csv`
- workspace 目标路径（示例）：`<ws>/work/inputs/sales.csv`

说明：

- `work/inputs` 在 `auto_inputs=True` 时通常会链接到只读输入挂载目录。
- 需要写入的中间输入，建议放到 `work/staged_inputs` 等可写目录。

### 三者关系（建议按这条链路理解）

三者不是并列关系，而是同一份输入在不同阶段的路径表示：

1. **宿主机输入文件**：真实文件存在于你的机器上。  
   例如：`/tmp/skillrun-inputs/sales.csv`
2. **容器挂载路径**：Docker bind mount 后，该文件在容器内的可见路径。  
   例如：`/opt/trpc-agent/inputs/sales.csv`
3. **workspace 目标路径**：`stage_inputs` 最终交给技能命令读取的位置。  
   例如：`<ws>/work/inputs/sales.csv`

可视化链路：

```text
宿主机: /tmp/skillrun-inputs/sales.csv
   │  (bind mount: inputs_host -> /opt/trpc-agent/inputs)
   ▼
容器: /opt/trpc-agent/inputs/sales.csv
   │  (stage_inputs: src=host://..., dst=work/inputs/sales.csv)
   ▼
workspace: <ws>/work/inputs/sales.csv
```

对应的 `skill_run` 输入示例：

```json
{
  "src": "host:///tmp/skillrun-inputs/sales.csv",
  "dst": "work/inputs/sales.csv",
  "mode": "link"
}
```

在这个示例里，`src` 指向宿主机路径；运行时会先映射到容器路径，再按 `dst` 放入 workspace。

补充说明：

- `mode="link"` 时，`/opt/trpc-agent/inputs/sales.csv -> <ws>/work/inputs/sales.csv` 通常是链接关系。
- `mode="copy"` 时，通常是复制关系。
- 当 `auto_inputs=True` 时，`work/inputs` 目录本身可能已是链接到输入挂载目录；此时向 `work/inputs` 执行复制写入可能受只读挂载限制，建议把可写中间文件放到 `work/staged_inputs`。

## 三、`host://`、`workspace://`、`skill://` 的作用

`stage_inputs` 支持多种 `src` scheme。示例里主要用到以下三种：

- `host://`
  - 含义：从宿主机文件系统引入输入。
  - 示例：`host:///tmp/skillrun-inputs/sales.csv`
  - 典型场景：把用户本机数据集注入到容器执行环境。

- `workspace://`
  - 含义：从当前 workspace 已存在文件复制/链接为输入。
  - 示例：`workspace://skills/python_math/SKILL.md`
  - 典型场景：复用上一步产物或 workspace 内已有文件。

- `skill://`
  - 含义：从 `workspace/skills` 下的技能文件复制/链接为输入。
  - 示例：`skill://python_math/scripts/fib.py`
  - 典型场景：把技能脚本、模板等资源放到目标输入目录统一处理。

## 四、`inputs_host` 与 `skill_spec` 的作用

### 1) `skill_spec` 作用

`skill_spec` 负责把技能仓库目录挂载到容器的固定路径（只读）：

- 宿主机：`<skills_root>`
- 容器：`/opt/trpc-agent/skills`

作用：

- 让 `stage_directory` 和 `skill://` 在容器里可直接访问技能文件。
- 只读挂载可避免技能源码被运行时误改。

### 2) `inputs_host` 作用

`inputs_host` 负责把宿主机输入目录挂载到容器（只读）：

- 宿主机：例如 `/tmp/skillrun-inputs`
- 容器：`/opt/trpc-agent/inputs`

作用：

- 让 `host://` 输入可以走“挂载路径映射”而不是全量拷贝。
- 在 `mode=link` 时可实现近似零拷贝输入注入。

注意：

- `inputs_host` 未配置时，`host://` 会走 fallback copy，性能和可观测性较差。
- `work/inputs` 可能是只读链接，`copy` 目标不要写到该目录，建议写 `work/staged_inputs`。

## 五、推荐实践

1. 对只读外部输入使用 `host://... -> work/inputs/...`（`mode=link`）。
2. 对需要改写或二次加工的输入，使用 `dst=work/staged_inputs/...`。
3. `workspace://` 的源路径必须确保在当前会话已存在，否则会出现 `cannot stat`。
4. 复用技能内脚本时优先 `skill://`，并复制到可写目录后再执行。

## 六、示例目录结构

```text
examples/skills_with_container/
├── agent/
│   ├── agent.py
│   ├── tools.py
│   ├── config.py
│   └── prompts.py
├── run_agent.py
├── skills/
│   ├── python_math/
│   ├── file_tools/
│   └── user_file_ops/
└── README.md
```
