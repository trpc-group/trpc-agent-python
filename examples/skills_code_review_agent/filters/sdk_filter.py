# filters/sdk_filter.py —— 接 SDK BaseFilter，_before 短路 deny/review；模块门禁
from typing import Any

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter._base_filter import BaseFilter

from .policy import CommandPolicy


class CrGovernanceFilter(BaseFilter):
    """代码审查 Agent 治理 Filter

    在工具调用进沙箱前进行安全检查：
    - deny/needs_human_review → is_continue=False，工具调用进沙箱前即中断
    - allow → is_continue=True，正常执行

    关键设计：被阻命令不触发沙箱模块 import（pipeline 仅用 models.py 零依赖契约构造空结果）
    """

    def __init__(self, policy: dict):
        """初始化治理 Filter

        Args:
            policy: 从 policy.json 加载的策略配置
        """
        super().__init__()
        self.policy = CommandPolicy(policy)
        self._type = 0  # FilterType.TOOL
        self._name = "cr_governance_filter"

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """在工具调用前进行安全检查

        Args:
            ctx: Agent 上下文
            req: 工具调用请求，格式：{"tool_name": str, "command": str, ...}
            rsp: FilterResult，用于设置是否继续执行
        """
        # 只处理 skill_run 工具调用
        if not isinstance(req, dict) or req.get("tool_name") != "skill_run":
            return

        command = req.get("command", "")
        if not command:
            return

        # 构建上下文，从 ctx 或 req 中提取 call_index
        filter_ctx = {"call_index": getattr(ctx, "call_index", 0)}

        # 执行策略评估
        decision = self.policy.evaluate(command, filter_ctx)

        # 根据 decision 设置 is_continue
        if decision.decision in ("deny", "needs_human_review"):
            rsp.is_continue = False
            rsp.error = PermissionError(f"命令被 Filter 拦截: {decision.reason}")
        else:
            rsp.is_continue = True

    async def _after(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """在工具调用后执行（暂无逻辑）"""
        return

    async def _after_every_stream(self, ctx: AgentContext, req: Any, rsp: FilterResult):
        """每次流式响应后执行（暂无逻辑）"""
        return
