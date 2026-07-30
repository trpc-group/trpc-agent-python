# Skill Hub 示例

本示例演示 `trpc_agent_sdk.skills.hub`（Skill Hub）：在启动 `LlmAgent` 之前，通过 `SkillSpecsConfig`（打包 `SkillSpec` + `install_path`）+ `create_default_skill_repository(additional_skill_specs=...)` 从 GitHub 上按需拉取一个技能（Anthropic 官方的 `skill-creator`），写入本地技能目录，再像本地技能一样交给 `SkillToolSet` 使用。

## 什么是 Skill Hub

Skill Hub（`trpc_agent_sdk.skills.hub`）把"从各种来源发现并获取 skill"统一到同一个接口后面。每个来源都是一个 `SkillSource` 适配器，对外提供一致的 `search` / `inspect` / `fetch` 三种能力：

| 适配器 | 来源 |
| --- | --- |
| `GitHubSource` | GitHub 仓库目录（本示例使用） |
| `WellKnownSkillSource` | 站点 `.well-known/skills` |
| `HermesIndexSource` | Hermes 内置 skill index |
| `SkillsShSource` | skills.sh |
| `ClawHubSource` | ClawHub registry |
| `ClaudeMarketplaceSource` | Claude Skills Marketplace |
| `LobeHubSource` | LobeHub |

`SkillSource.fetch()` 只返回内存中的 `SkillBundle`（`name` + `files` + `metadata`）。SDK 提供 `SkillSpec` 声明、`SkillSpecsConfig`（`specs` + `install_path`，`install_path` 省略时默认落到系统临时目录）和 `create_default_skill_repository(additional_skill_specs=...)`，在构造 repository 时把远程 skill 写入 `install_path`，再交给标准 `FsSkillRepository` 扫描。

## 关键特性

- `GitHubSource(GitHubAuth(token))` 无需认证即可拉取公开仓库（60 次/小时限额，足够本示例使用；设置 `GITHUB_TOKEN` 可提升到 5000 次/小时）
- 安装逻辑内部复用 SDK 导出的路径校验，避免恶意 `SkillBundle` 写出到目标目录之外
- 已安装的技能会被跳过，除非 `SkillSpec(replace_if_exists=True)`
- 拉取完成后，技能通过标准的 `create_default_skill_repository` + `SkillToolSet` 链路对 agent 可见，和本地技能没有区别

## Agent 层级结构说明

- 根节点：`LlmAgent`（`skill_hub_demo_agent`），挂载 `SkillToolSet` 与 `skill_repository`
- 无子 Agent；单智能体通过 `skill_load` / `skill_list_docs` 等技能工具完成任务

## 关键代码解释

- `agent/hub.py`：
  - `create_skill_tool_set()`：用 `SkillSpecsConfig` 打包 GitHub `SkillSpec` 与 `install_path`，调用 `create_default_skill_repository(additional_skill_specs=...)` 完成安装 + 索引
- `agent/agent.py`：`create_agent(skills_dir)` 把返回的 `skill_repository` / `skill_tool_set` 绑定到 `LlmAgent`
- `run_agent.py`：清空 `data/` 目录（保证每次都重新走一遍 Skill Hub 拉取流程），创建 agent，跑一轮 `skill_load` + 总结的对话，并打印实际下载到的文件列表

## 环境与运行

- Python 3.10+；仓库根目录执行 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`（可用 `.env`）
- 可选：`GITHUB_TOKEN`，用于提高 GitHub API 限额（本示例只读取公开仓库，不设置也能跑）

```bash
cd examples/skills_hub
python3 run_agent.py
```

运行后会在 `data/skills/.downloaded/hub/skill-creator/` 下看到从 GitHub 拉取的真实文件（`SKILL.md`、`scripts/`、`references/` 等）。
