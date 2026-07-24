# filters/policy.py —— 确定性 fail-closed 判定链 + policy.json 真加载
import json
import re
from pathlib import Path

from agent.models import FilterDecision


def load_policy(path: str | None = None) -> dict:
    """加载 policy.json（真 json.load，反 PR138 死文件）

    Args:
        path: policy.json 文件路径，默认为相对于本文件的绝对路径

    Returns:
        dict: 策略配置字典
    """
    if path is None:
        # 默认使用相对于本文件的绝对路径，确保从任意工作目录都能找到 policy.json
        path = Path(__file__).parent / "policy.json"
    with open(path) as f:
        return json.load(f)


class CommandPolicy:
    """命令策略评估器 —— 确定性 fail-closed 有序判定链

    判定链顺序（fail-closed：任一条件命中即返回）：
    1. 禁止路径 → deny
    2. 高危命令 → needs_human_review
    3. 非白名单网络域名 → deny
    4. 超预算沙箱调用 → deny
    5. 默认 → allow
    """

    def __init__(self, policy: dict):
        """初始化命令策略

        Args:
            policy: 从 policy.json 加载的策略配置
        """
        self.p = policy

    def evaluate(self, command: str, ctx: dict) -> FilterDecision:
        """评估命令是否允许执行

        Args:
            command: 待执行的命令字符串
            ctx: 上下文信息，包含 call_index 等字段

        Returns:
            FilterDecision: 过滤决策结果
        """
        # 1. 禁止路径检查（最高优先级，防止敏感文件泄露）
        # 使用边界匹配避免误匹配（如 .environment 不应命中 .env）
        # 边界包括：路径分隔符、引号、空格、字符串起止
        for fp in self.p["forbidden_paths"]:
            # 构造边界匹配正则：路径片段前后必须有边界
            # 边界字符集：路径分隔符(/、\)、引号(" ' `)、空格、字符串起止(^ $)
            escaped_fp = re.escape(fp)
            pattern = rf'(^|[/\s"\']){escaped_fp}($|[/\s"\'])'
            if re.search(pattern, command):
                return FilterDecision(stage="pre_sandbox",
                                      decision="deny",
                                      reason=f"禁止路径 {fp}",
                                      command_redacted=command[:80])

        # 2. 高危命令检查（需要人工审查）
        # 对完整命令词使用边界匹配，对 shell 操作符保留子串匹配
        # shell 操作符：单字符或双字符操作符
        shell_operators = {";", "&&", "|", "||"}
        for hc in self.p["high_risk_commands"]:
            # shell 操作符保留子串匹配（| sh 中的 | 是管道，; 是命令分隔符）
            if hc in shell_operators:
                if hc in command:
                    return FilterDecision(stage="pre_sandbox",
                                          decision="needs_human_review",
                                          reason=f"高危操作符 {hc}",
                                          command_redacted=command[:80])
            elif hc == "| sh":
                # 特殊处理 | sh 组合
                if "| sh" in command or "|sh" in command:
                    return FilterDecision(stage="pre_sandbox",
                                          decision="needs_human_review",
                                          reason=f"高危命令 {hc}",
                                          command_redacted=command[:80])
            else:
                # 完整命令词使用边界匹配（避免 rm -rf 误匹配 rm -rf-safe）
                # 边界：空格、管道、分号、字符串起止
                escaped_hc = re.escape(hc)
                pattern = rf'(^|[\s|;]){escaped_hc}($|[\s|;])'
                if re.search(pattern, command):
                    return FilterDecision(stage="pre_sandbox",
                                          decision="needs_human_review",
                                          reason=f"高危命令 {hc}",
                                          command_redacted=command[:80])

        # 3. 网络域名白名单检查
        for m in re.findall(r"https?://([^/\s]+)", command):
            if m not in self.p["network_whitelist"]:
                return FilterDecision(stage="pre_sandbox",
                                      decision="deny",
                                      reason=f"非白名单网络 {m}",
                                      command_redacted=command[:80])

        # 4. 沙箱调用预算检查（>= 确保 call_index 达到 max_sandbox_runs 时即 deny）
        if ctx.get("call_index", 0) >= self.p["max_sandbox_runs"]:
            return FilterDecision(
                stage="pre_sandbox",
                decision="deny",
                reason="超预算沙箱调用",
                command_redacted=command[:80]
            )

        # 5. 可执行文件白名单校验（命令首词必须在白名单内，fail-closed）
        # W5 修复: allowed_executables 此前完全未生效，任何非高危命令均 allow
        allowed = self.p.get("allowed_executables", [])
        first_word = command.strip().split()[0] if command.strip() else ""
        if allowed and first_word not in allowed:
            return FilterDecision(
                stage="pre_sandbox",
                decision="deny",
                reason=f"非白名单可执行文件 {first_word}",
                command_redacted=command[:80]
            )

        # 6. 默认允许
        return FilterDecision(stage="pre_sandbox", decision="allow", reason="", command_redacted="")
