# test_dedup.py —— 四元组去重 + 三桶路由测试
from agent.models import Finding, Severity, Bucket
from agent.dedup import dedup_and_route, _key, _fid


class TestDedup:
    """测试去重与路由功能"""

    def test_same_quadruple_keeps_highest_confidence(self):
        """同 file/line/category/rule_id 两条取高置信度"""
        findings = [
            Finding(severity=Severity.MEDIUM,
                    category="security",
                    file="main.py",
                    line=42,
                    title="SQL injection",
                    evidence="cursor.execute(sql)",
                    recommendation="Use parameterized queries",
                    confidence=0.6,
                    source="rule",
                    rule_id="SQL001"),
            Finding(severity=Severity.HIGH,
                    category="security",
                    file="main.py",
                    line=42,
                    title="SQL injection",
                    evidence="cursor.execute(sql)",
                    recommendation="Use parameterized queries",
                    confidence=0.9,
                    source="rule",
                    rule_id="SQL001"),
        ]

        findings_result, warnings, needs_review = dedup_and_route(findings)

        assert len(findings_result) == 1
        assert findings_result[0].confidence == 0.9
        assert findings_result[0].finding_id != ""

    def test_same_line_different_rule_id_both_kept(self):
        """同行不同 rule_id 两条都保留（验证四元组不丢证据）"""
        findings = [
            Finding(severity=Severity.HIGH,
                    category="security",
                    file="auth.py",
                    line=10,
                    title="Hardcoded password",
                    evidence="password = 'admin123'",
                    recommendation="Use environment variables",
                    confidence=0.85,
                    source="rule",
                    rule_id="AUTH001"),
            Finding(severity=Severity.MEDIUM,
                    category="security",
                    file="auth.py",
                    line=10,
                    title="Weak password policy",
                    evidence="password = 'admin123'",
                    recommendation="Implement strong password requirements",
                    confidence=0.7,
                    source="rule",
                    rule_id="AUTH002"),
        ]

        findings_result, warnings, needs_review = dedup_and_route(findings)

        # 两条都应该保留，因为 rule_id 不同
        assert len(findings_result) == 1
        assert len(warnings) == 1
        assert findings_result[0].rule_id == "AUTH001"
        assert warnings[0].rule_id == "AUTH002"

    def test_confidence_routing(self):
        """confidence 0.65→warnings、0.4→needs_review、0.9→findings"""
        findings = [
            Finding(severity=Severity.HIGH,
                    category="security",
                    file="api.py",
                    line=100,
                    title="High confidence issue",
                    evidence="eval(user_input)",
                    recommendation="Avoid eval",
                    confidence=0.9,
                    source="rule",
                    rule_id="SEC001"),
            Finding(severity=Severity.MEDIUM,
                    category="performance",
                    file="utils.py",
                    line=50,
                    title="Medium confidence issue",
                    evidence="slow_operation()",
                    recommendation="Optimize algorithm",
                    confidence=0.65,
                    source="rule",
                    rule_id="PERF001"),
            Finding(severity=Severity.LOW,
                    category="style",
                    file="helpers.py",
                    line=20,
                    title="Low confidence issue",
                    evidence="long_line()",
                    recommendation="Break line",
                    confidence=0.4,
                    source="rule",
                    rule_id="STYLE001"),
        ]

        findings_result, warnings, needs_review = dedup_and_route(findings)

        assert len(findings_result) == 1
        assert len(warnings) == 1
        assert len(needs_review) == 1

        assert findings_result[0].confidence == 0.9
        assert findings_result[0].bucket == Bucket.FINDINGS

        assert warnings[0].confidence == 0.65
        assert warnings[0].bucket == Bucket.WARNINGS

        assert needs_review[0].confidence == 0.4
        assert needs_review[0].bucket == Bucket.NEEDS_REVIEW

    def test_finding_id_filled(self):
        """finding_id 填充"""
        findings = [
            Finding(severity=Severity.HIGH,
                    category="security",
                    file="test.py",
                    line=1,
                    title="Test issue",
                    evidence="test code",
                    recommendation="fix it",
                    confidence=0.8,
                    source="rule",
                    rule_id="TEST001"),
        ]

        findings_result, warnings, needs_review = dedup_and_route(findings)

        assert findings_result[0].finding_id != ""
        assert len(findings_result[0].finding_id) == 16  # sha256[:16]


class TestKey:
    """测试 _key 函数"""

    def test_key_generates_correct_quadruple(self):
        """_key 生成正确的四元组"""
        finding = Finding(severity=Severity.MEDIUM,
                          category="test",
                          file="test.py",
                          line=42,
                          title="Test",
                          evidence="test",
                          recommendation="test",
                          confidence=0.5,
                          source="rule",
                          rule_id="RULE001")

        key = _key(finding)
        assert key == ("test.py", 42, "test", "RULE001")

    def test_key_distinguishes_different_rule_ids(self):
        """_key 能区分不同 rule_id"""
        finding1 = Finding(severity=Severity.MEDIUM,
                           category="test",
                           file="test.py",
                           line=42,
                           title="Test",
                           evidence="test",
                           recommendation="test",
                           confidence=0.5,
                           source="rule",
                           rule_id="RULE001")

        finding2 = Finding(severity=Severity.MEDIUM,
                           category="test",
                           file="test.py",
                           line=42,
                           title="Test",
                           evidence="test",
                           recommendation="test",
                           confidence=0.5,
                           source="rule",
                           rule_id="RULE002")

        key1 = _key(finding1)
        key2 = _key(finding2)

        assert key1 != key2


class TestFid:
    """测试 _fid 函数"""

    def test_fid_generates_unique_id(self):
        """_fid 生成唯一的 finding_id"""
        finding = Finding(severity=Severity.MEDIUM,
                          category="test",
                          file="test.py",
                          line=42,
                          title="Test issue",
                          evidence="test code",
                          recommendation="fix it",
                          confidence=0.5,
                          source="rule",
                          rule_id="RULE001")

        fid = _fid(finding)
        assert fid != ""
        assert len(fid) == 16  # sha256[:16]

    def test_fid_different_for_different_findings(self):
        """_fid 对不同的 finding 生成不同的 ID"""
        finding1 = Finding(severity=Severity.MEDIUM,
                           category="test",
                           file="test.py",
                           line=42,
                           title="Test 1",
                           evidence="test1",
                           recommendation="fix1",
                           confidence=0.5,
                           source="rule",
                           rule_id="RULE001")

        finding2 = Finding(severity=Severity.MEDIUM,
                           category="test",
                           file="test.py",
                           line=42,
                           title="Test 2",
                           evidence="test2",
                           recommendation="fix2",
                           confidence=0.5,
                           source="rule",
                           rule_id="RULE001")

        fid1 = _fid(finding1)
        fid2 = _fid(finding2)

        assert fid1 != fid2
