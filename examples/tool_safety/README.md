# Tool Script Safety Guard 示例

本目录提供一套可重复运行的公开验收样本，用于验证 Tool、Skill 和 CodeExecutor 在执行 Python 或 Bash 内容前的静态安全检查。样本只会被读取和扫描，测试不会执行其中的脚本。

## 快速运行

在仓库根目录执行：

```bash
python scripts/tool_safety_check.py examples/tool_safety/samples \
  --policy examples/tool_safety/tool_safety_policy.yaml \
  --report /tmp/tool_safety_report.json \
  --audit /tmp/tool_safety_audit.jsonl \
  --tool-name public_sample_scan
```

CLI 接受任意数量的 `.py`、`.sh`、`.bash` 文件或目录。目录会递归扫描，结果按路径稳定排序。标准输出和 `--report` 都是结构化 JSON；`--audit` 每个文件追加一条已脱敏 JSONL 事件。

仓库同时保留一份由上述 12 个样本真实生成的 [`tool_safety_report.json`](tool_safety_report.json) 和 [`tool_safety_audit.jsonl`](tool_safety_audit.jsonl)。报告不含原始脚本或环境变量值，审计文件恰好每个样本一条事件。

退出码遵循最严格决策：

| 退出码 | 决策 |
| --- | --- |
| `0` | `allow` |
| `1` | `deny` |
| `2` | `needs_human_review` |

多文件扫描按 `deny > needs_human_review > allow` 聚合。策略读取失败、输入不可读或扫描基础设施异常时，CLI 采用 fail-closed 并返回 `deny`。

## 公开样本

[`samples/manifest.yaml`](samples/manifest.yaml) 是唯一真值源，列出恰好 12 个样本的预期决策和至少应命中的规则。场景包括安全 Python、危险删除、读取 SSH 私钥、非白名单与白名单网络请求、subprocess、shell 注入、依赖安装、无限循环、敏感信息输出、Bash 管道和人工复核。

测试会逐个调用 CLI，并校验：

- 高危样本检出率不低于 90%；
- 安全样本误报率不高于 10%；
- 危险删除、读取密钥、非白名单外连检出率均为 100%；
- JSON 报告和 JSONL 审计可以被标准解析器读取。

## 策略修改

[`tool_safety_policy.yaml`](tool_safety_policy.yaml) 采用严格字段校验。未知字段、错误类型和非法阈值会直接失败，避免拼写错误造成静默放行。修改以下字段不需要改代码：

- `allowed_domains`：精确域名和子域名白名单；
- `allowed_commands`：允许的可执行文件 basename 或精确路径；basename 白名单不会放行 `./git` 等相对路径；
- `denied_paths`：禁止路径及 glob；
- `max_timeout_seconds`、`max_output_bytes`、`max_script_bytes`：资源上限；
- `long_sleep_seconds`、`max_concurrency`：可疑资源使用阈值；
- `rule_actions`：按 rule id 覆盖默认决策。

完整处理流程、接入方式和安全边界见 [`DESIGN.md`](DESIGN.md)。
