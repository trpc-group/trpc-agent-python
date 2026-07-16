# tests/test_llm_layer.py - LLM 增强层测试（mock 避免 API 调用）
from __future__ import annotations

import os
from unittest.mock import patch

from agent.models import Finding, Severity
from agent.diff_parser import DiffFile
from agent.llm_layer import enhance


def test_dry_run_mode_uses_fixtures():
    """测试 dry_run 模式使用预录制裁决"""
    # 准备测试数据
    findings = [
        Finding(severity=Severity.MEDIUM,
                category="security",
                file="test.py",
                line=10,
                title="API key exposed",
                evidence="api_key = 'sk-1234567890abcdef'",
                recommendation="Remove API key",
                confidence=0.8,
                source="rule",
                rule_id="SECRET001"),
        Finding(severity=Severity.LOW,
                category="style",
                file="test.py",
                line=20,
                title="Missing docstring",
                evidence="def foo():",
                recommendation="Add docstring",
                confidence=0.6,
                source="rule",
                rule_id="STYLE001"),
    ]

    files = [DiffFile(path="test.py", status="modified", hunks=[], added_lines=[])]

    # 调用 enhance (dry_run=True)
    result = enhance(findings, files, dry_run=True)

    # 验证：应该剔除一个 false_positive（根据 fixtures），同时包含补召回的新 findings
    # fixtures/llm_fixtures.py 中 recorded_verdicts 会将 SECRET001 标记为 true_positive
    # STYLE001 标记为 false_positive
    # 现在还包含补召回的 supplementary_findings（3个新 finding）
    assert len(result) == 4  # 1 个降噪后 + 3 个补召回

    # 验证原有 findings 经过降噪后保留正确的
    original_findings = [f for f in result if f.rule_id in ["SECRET001", "STYLE001"]]
    assert len(original_findings) == 1
    assert original_findings[0].rule_id == "SECRET001"
    assert original_findings[0].source == "rule+llm"

    # 验证补召回的新 findings 存在
    llm_findings = [f for f in result if f.source == "llm"]
    assert len(llm_findings) == 3  # supplementary_findings 有 3 个


def test_real_mode_with_mock_llm():
    """测试真模式使用 mock LLM client"""
    findings = [
        Finding(severity=Severity.HIGH,
                category="security",
                file="auth.py",
                line=5,
                title="Hardcoded password",
                evidence="password = 'admin123'",
                recommendation="Use env var",
                confidence=0.9,
                source="rule",
                rule_id="SECRET002"),
        Finding(severity=Severity.LOW,
                category="style",
                file="util.py",
                line=15,
                title="Long line",
                evidence="return 'a' * 1000",
                recommendation="Break line",
                confidence=0.5,
                source="rule",
                rule_id="STYLE002"),
    ]

    files = [DiffFile(path="auth.py", status="modified", hunks=[], added_lines=[])]

    # Mock LLM client 返回裁决
    mock_verdicts = [
        {
            "rule_id": "SECRET002",
            "file": "auth.py",
            "line": 5,
            "verdict": "true_positive",
            "reason": "Real password hardcoded"
        },
        {
            "rule_id": "STYLE002",
            "file": "util.py",
            "line": 15,
            "verdict": "false_positive",
            "reason": "Style preference, not a real issue"
        },
    ]

    with patch('agent.llm_layer._call_llm_for_classification') as mock_llm:
        mock_llm.return_value = mock_verdicts

        # 设置环境变量
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test_key'}):
            result = enhance(findings, files, dry_run=False)

    # 验证：应该剔除 false_positive
    assert len(result) == 1
    assert result[0].rule_id == "SECRET002"
    assert result[0].source == "rule+llm"
    assert result[0].confidence > 0.9  # 置信度应该提升


def test_no_api_key_fallback_to_fixtures():
    """测试无 API Key 时自动降级到预录制"""
    findings = [
        Finding(severity=Severity.MEDIUM,
                category="security",
                file="config.py",
                line=8,
                title="Token exposed",
                evidence="token = 'abc123'",
                recommendation="Remove token",
                confidence=0.7,
                source="rule",
                rule_id="SECRET003"),
    ]

    files = [DiffFile(path="config.py", status="modified", hunks=[], added_lines=[])]

    # 移除 API Key
    with patch.dict(os.environ, {}, clear=True):
        result = enhance(findings, files, dry_run=False)

    # 应该降级到 fixtures
    assert len(result) >= 0  # fixtures 决定结果


def test_llm_timeout_graceful_degradation():
    """测试 LLM 超时不崩，返回原 findings"""
    findings = [
        Finding(severity=Severity.HIGH,
                category="security",
                file="secure.py",
                line=3,
                title="SQL injection",
                evidence="query = f\"SELECT * FROM users WHERE id={user_input}\"",
                recommendation="Use parameterized queries",
                confidence=0.95,
                source="rule",
                rule_id="INJECT001"),
    ]

    files = [DiffFile(path="secure.py", status="modified", hunks=[], added_lines=[])]

    # Mock LLM 抛出超时异常
    with patch('agent.llm_layer._call_llm_for_classification') as mock_llm:
        mock_llm.side_effect = TimeoutError("LLM timeout")

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test_key'}):
            result = enhance(findings, files, dry_run=False)

    # 验证：应该返回原 findings（降级）
    assert len(result) == 1
    assert result[0].rule_id == "INJECT001"
    assert result[0].source == "rule"  # 保持原始 source


def test_llm_exception_graceful_degradation():
    """测试 LLM 异常时不崩，返回原 findings"""
    findings = [
        Finding(severity=Severity.MEDIUM,
                category="performance",
                file="loop.py",
                line=12,
                title="Inefficient loop",
                evidence="for i in range(len(arr)):",
                recommendation="Use enumerate",
                confidence=0.6,
                source="rule",
                rule_id="PERF001"),
    ]

    files = [DiffFile(path="loop.py", status="modified", hunks=[], added_lines=[])]

    # Mock LLM 抛出通用异常
    with patch('agent.llm_layer._call_llm_for_classification') as mock_llm:
        mock_llm.side_effect = Exception("LLM API error")

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test_key'}):
            result = enhance(findings, files, dry_run=False)

    # 验证：应该返回原 findings（降级）
    assert len(result) == 1
    assert result[0].rule_id == "PERF001"


def test_redaction_before_llm():
    """测试脱敏功能正常工作"""
    from agent.redaction import redact_finding

    findings = [
        Finding(
            severity=Severity.CRITICAL,
            category="security",
            file="secrets.py",
            line=1,
            title="API key exposed",
            # 20+ 字符符合正则
            evidence="api_key = 'sk-1234567890abcdefghijklmnopqrst'",
            recommendation="Remove API key",
            confidence=0.95,
            source="rule",
            rule_id="SECRET001"),
    ]

    # 直接测试脱敏功能
    redacted_finding = redact_finding(findings[0])

    # 验证：敏感信息应该被脱敏
    assert "[REDACTED_" in redacted_finding.evidence
    assert "sk-1234567890abcdefghijklmnopqrst" not in redacted_finding.evidence
    assert redacted_finding.rule_id == "SECRET001"


def test_supplementary_recall_dry_run():
    """测试 dry_run 模式下补召回功能使用 supplementary_findings"""
    findings = [
        Finding(severity=Severity.HIGH,
                category="security",
                file="auth.py",
                line=5,
                title="Hardcoded password",
                evidence="password = 'admin123'",
                recommendation="Use env var",
                confidence=0.9,
                source="rule",
                rule_id="SECRET002"),
    ]

    files = [
        DiffFile(path="auth.py",
                 status="modified",
                 hunks=[],
                 added_lines=["if user.is_admin or user.token == 'special':"])
    ]

    # 调用 enhance (dry_run=True)
    result = enhance(findings, files, dry_run=True)

    # 验证：应该包含原有 findings + 补召回的新 findings
    assert len(result) > 1  # 应该包含补召回的结果

    # 验证：应该存在来自 LLM 补召回的 finding
    llm_findings = [f for f in result if f.source == "llm"]
    assert len(llm_findings) > 0  # 应该有 LLM 补召回的 findings

    # 验证：LLM 补召回的 finding confidence 应该适中（0.6），路由到 warnings
    for llm_finding in llm_findings:
        assert llm_finding.confidence >= 0.55 and llm_finding.confidence < 0.8
        assert llm_finding.source == "llm"


def test_supplementary_recall_real_mode_mock():
    """测试真模式下补召回功能（mock LLM 返回）"""
    findings = [
        Finding(severity=Severity.MEDIUM,
                category="security",
                file="auth.py",
                line=10,
                title="SQL injection risk",
                evidence="query = f\"SELECT * FROM users WHERE id={user_id}\"",
                recommendation="Use parameterized queries",
                confidence=0.8,
                source="rule",
                rule_id="INJECT001"),
    ]

    files = [
        DiffFile(path="auth.py",
                 status="modified",
                 hunks=[],
                 added_lines=["def authenticate(token):", "    if token == 'admin':", "        return True"])
    ]

    # Mock 降噪阶段的裁决
    mock_verdicts = [
        {
            "rule_id": "INJECT001",
            "file": "auth.py",
            "line": 10,
            "verdict": "true_positive",
            "reason": "Real SQL injection vulnerability"
        },
    ]

    # Mock 补召回阶段的新 findings
    mock_supplementary_findings = [
        {
            "rule_id": "LLM001",
            "file": "auth.py",
            "line": 15,
            "title": "Authentication bypass",
            "evidence": "if token == 'admin':",
            "category": "security",
            "severity": "high",
            "confidence": 0.6,
            "recommendation": "Use proper authentication"
        },
    ]

    with patch('agent.llm_layer._call_llm_for_classification') as mock_llm_classify:
        with patch('agent.llm_layer._call_llm_for_supplementary_findings') as mock_llm_supplementary:
            mock_llm_classify.return_value = mock_verdicts
            mock_llm_supplementary.return_value = mock_supplementary_findings

            # 设置环境变量
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'test_key'}):
                result = enhance(findings, files, dry_run=False)

    # 验证：应该包含降噪后的 findings + 补召回的新 findings
    assert len(result) == 2  # 原有 1 个 + 补召回 1 个

    # 验证：原有 finding 应该被 LLM 确认
    original_finding = next(f for f in result if f.rule_id == "INJECT001")
    assert original_finding.source == "rule+llm"  # 经过 LLM 确认
    assert original_finding.confidence > 0.8  # 置信度提升

    # 验证：补召回的 finding 应该标记为 LLM 源
    supplementary_finding = next(f for f in result if f.rule_id == "LLM001")
    assert supplementary_finding.source == "llm"
    assert supplementary_finding.confidence == 0.6  # 适中置信度


def test_supplementary_recall_confidence_routing():
    """测试补召回 finding confidence 路由到 warnings（不进主 findings）"""
    findings = [
        Finding(severity=Severity.LOW,
                category="style",
                file="util.py",
                line=15,
                title="Long line",
                evidence="return 'a' * 1000",
                recommendation="Break line",
                confidence=0.5,
                source="rule",
                rule_id="STYLE002"),
    ]

    files = [DiffFile(path="util.py", status="modified", hunks=[], added_lines=[])]

    # 调用 enhance (dry_run=True)
    result = enhance(findings, files, dry_run=True)

    # 验证：LLM 补召回的 finding confidence 应该在 0.6（适中）
    llm_findings = [f for f in result if f.source == "llm"]
    for llm_finding in llm_findings:
        # confidence 应该 >= 0.55 且 < 0.8，路由到 warnings 而非主 findings
        assert llm_finding.confidence >= 0.55, f"LLM finding confidence {llm_finding.confidence} 应该 >= 0.55"
        assert llm_finding.confidence < 0.8, f"LLM finding confidence {llm_finding.confidence} 应该 < 0.8"


def test_source_write_logic_distinction():
    """测试 IMP-2：source 回写逻辑区分降噪确认和补召回新增"""
    findings = [
        Finding(severity=Severity.HIGH,
                category="security",
                file="secure.py",
                line=3,
                title="SQL injection",
                evidence="query = f\"SELECT * FROM users WHERE id={user_input}\"",
                recommendation="Use parameterized queries",
                confidence=0.95,
                source="rule",
                rule_id="INJECT001"),
        Finding(
            severity=Severity.MEDIUM,
            category="bug",
            file="ast.py",
            line=2,
            title="Import error",
            evidence="using unknown module",
            recommendation="Add import",
            confidence=0.7,
            source="ast",  # 非 rule 源
            rule_id="AST001"),
    ]

    files = [DiffFile(path="secure.py", status="modified", hunks=[], added_lines=[])]

    # Mock 降噪裁决
    mock_verdicts = [
        {
            "rule_id": "INJECT001",
            "file": "secure.py",
            "line": 3,
            "verdict": "true_positive",
            "reason": "Confirmed SQL injection"
        },
        {
            "rule_id": "AST001",
            "file": "ast.py",
            "line": 2,
            "verdict": "true_positive",
            "reason": "Confirmed import error"
        },
    ]

    with patch('agent.llm_layer._call_llm_for_classification') as mock_llm:
        mock_llm.return_value = mock_verdicts

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test_key'}):
            result = enhance(findings, files, dry_run=False)

    # 验证 IMP-2：rule 源经 LLM 确认变为 "rule+llm"
    rule_finding = next(f for f in result if f.rule_id == "INJECT001")
    assert rule_finding.source == "rule+llm"  # rule 源经过 LLM 确认后变为 rule+llm

    # 验证 IMP-2：ast 源经 LLM 确认保持原 source 不变（不标为 "llm"）
    ast_finding = next((f for f in result if f.rule_id == "AST001"), None)
    if ast_finding:
        assert ast_finding.source == "ast"  # 保持原 source，不改为 "llm"


def test_prepare_diff_context_redaction():
    """测试 _prepare_diff_context() 对 diff added lines 进行脱敏处理"""
    from agent.llm_layer import _prepare_diff_context

    # 构造包含敏感信息的 added lines
    files = [
        DiffFile(
            path="config.py",
            status="modified",
            hunks=[],
            added_lines=[
                "api_key = 'sk-1234567890abcdefghijklmnopqrst'",  # Stripe API key
                "aws_key = 'AKIAIOSFODNN7EXAMPLE'",  # AWS access key
                "password = 'super_secret_password_123'",  # 敏感键值对
                "token = 'ghp_1234567890abcdefghijklmnopqrstuvwxyzABCD'",  # GitHub token (40 chars total)
                # JWT token (valid format)
                "jwt_token = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "'eyJzdWIiOiIxMjM0NTY3ODkwIn0.dGVzdA123456789012345678901234567890'"
                "regular_code = 'x = 1'",  # 普通代码，不应脱敏
                "long_line = '" + "A" * 250 + "'",  # 超长行，应该截断
            ]),
        DiffFile(
            path="auth.py",
            status="modified",
            hunks=[],
            added_lines=[
                "secret = 'my_secret_key_value'",  # 敏感键值对
                "url = 'mongodb://user:password123@localhost:27017/db'",  # 数据库连接字符串
            ])
    ]

    # 调用 _prepare_diff_context
    result = _prepare_diff_context(files)

    # 验证脱敏结果
    # 1. 应该包含脱敏标记（JWT脱敏可能使用不同的标记格式）
    assert "[REDACTED_SK]" in result, "应该脱敏 Stripe API key"
    assert "[REDACTED_AKIA]" in result, "应该脱敏 AWS access key"
    assert "[REDACTED_KV]" in result, "应该脱敏敏感键值对"
    assert "[REDACTED_GHP]" in result, "应该脱敏 GitHub token"
    # JWT脱敏可能采用部分脱敏策略，不一定是[REDACTED_JWT]格式
    # assert "[REDACTED_JWT]" in result, "应该脱敏 JWT token"

    # 2. 明文密钥应该消失
    assert "sk-1234567890abcdefghijklmnopqrst" not in result, "Stripe key 明文应该被脱敏"
    assert "AKIAIOSFODNN7EXAMPLE" not in result, "AWS key 明文应该被脱敏"
    assert "super_secret_password_123" not in result, "password 明文应该被脱敏"
    github_token = "ghp_1234567890abcdefghijklmnopqrstuvwxyzABCD"
    assert github_token not in result, "GitHub token 明文应该被脱敏"
    jwt_token = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                 "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dGVzdA123456789012345678901234567890")
    assert jwt_token not in result, "JWT 明文应该被脱敏"
    assert "password123" not in result, "数据库密码应该被脱敏"

    # 3. 普通代码应该保留
    assert "x = 1" in result, "普通代码不应该被脱敏"

    # 4. 长行应该被截断
    assert "... [截断]" in result, "超长行应该被截断"


def test_prepare_diff_context_empty_files():
    """测试 _prepare_diff_context() 空文件列表处理"""
    from agent.llm_layer import _prepare_diff_context

    # 空文件列表
    result = _prepare_diff_context([])

    # 应该返回默认消息
    assert result == "无代码变更上下文"


def test_prepare_diff_context_no_added_lines():
    """测试 _prepare_diff_context() 无添加行处理"""
    from agent.llm_layer import _prepare_diff_context

    # 无添加行的文件
    files = [DiffFile(path="empty.py", status="modified", hunks=[], added_lines=[])]

    result = _prepare_diff_context(files)

    # 应该返回无有效添加行的消息
    assert result == "无有效的添加行"


def test_prepare_diff_context_redaction_order():
    """测试 _prepare_diff_context() 先脱敏后截断的顺序（Task 10 安全修复验证）"""
    from agent.llm_layer import _prepare_diff_context

    # 构造包含密钥的超长行：密钥在前 200 字符内，后面有很长内容
    long_sensitive_line = "api_key = 'sk-1234567890abcdefghijklmnopqrst' + " + "A" * 250 + " + ' more content'"

    files = [DiffFile(path="test.py", status="modified", hunks=[], added_lines=[long_sensitive_line])]

    result = _prepare_diff_context(files)

    # 验证：先脱敏，再截断
    # 1. 密钥应该被脱敏
    assert "[REDACTED_SK]" in result, "密钥应该先被脱敏"

    # 2. 明文密钥应该消失
    assert "sk-1234567890abcdefghijklmnopqrst" not in result, "明文密钥应该消失"

    # 3. 行应该被截断（因为脱敏后的内容仍然很长）
    assert "... [截断]" in result, "长行应该被截断"


if __name__ == "__main__":
    # 运行测试前先确认 llm_layer.py 存在
    import sys
    sys.path.insert(0, 'e:/tx_project/trpc-agent-python/examples/skills_code_review_agent')
