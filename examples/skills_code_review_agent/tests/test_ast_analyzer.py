"""Tests for AST analyzer — taint analysis for Python and JS/TS."""

import pytest

from pipeline.ast_analyzer import (
    analyze_python_ast,
    analyze_python_regex,
    analyze_js_ts,
    analyze_source,
    is_supported_language,
)
from pipeline.types import Finding


class TestPythonAST:
    """Python AST-based analysis tests."""

    def test_clean_code_no_findings(self):
        src = "def hello():\n    return 'world'\n"
        findings = analyze_python_ast(src)
        assert len(findings) == 0

    def test_os_system_with_concatenation(self):
        src = 'os.system("rm -rf " + user_path)\n'
        findings = analyze_python_regex(src)
        assert any("command injection" in f.title.lower() for f in findings)

    def test_eval_with_input(self):
        src = 'eval("os." + user_cmd)\n'
        findings = analyze_python_regex(src)
        assert any("eval" in f.title.lower() for f in findings)

    def test_sql_injection_pattern(self):
        src = 'cursor.execute("SELECT * FROM users WHERE id=" + uid)\n'
        findings = analyze_python_regex(src)
        assert any("sql injection" in f.title.lower() for f in findings)

    def test_syntax_error_graceful(self):
        src = "def broken(\n    return {'missing':\n"
        findings = analyze_python_ast(src)
        assert len(findings) == 0  # Graceful fallback

    def test_empty_source(self):
        findings = analyze_python_ast("")
        assert findings == []

    def test_safe_code_no_taint(self):
        src = "def add(a, b):\n    return a + b\nresult = add(1, 2)\n"
        findings = analyze_python_regex(src)
        assert len(findings) == 0


class TestJSTSAnalysis:
    """JavaScript/TypeScript regex analysis tests."""

    def test_js_eval_detection(self):
        src = 'eval("console.log(" + user_input + ")")'
        findings = analyze_js_ts(src)
        assert any("eval" in f.title.lower() for f in findings)

    def test_js_inner_html_xss(self):
        src = "document.getElementById('app').innerHTML = userInput;\n"
        findings = analyze_js_ts(src)
        assert any("innerhtml" in f.title.lower() for f in findings)

    def test_clean_js_no_findings(self):
        src = "const sum = (a, b) => a + b;\nconsole.log(sum(1, 2));\n"
        findings = analyze_js_ts(src)
        assert len(findings) == 0

    def test_typescript_sql_injection(self):
        src = 'db.query("DELETE FROM users WHERE id=" + req.params.id)'
        findings = analyze_js_ts(src)
        assert any("sql injection" in f.title.lower() for f in findings)


class TestLanguageDetection:
    """Language detection tests."""

    def test_py_extension(self):
        assert is_supported_language("main.py") is True

    def test_js_extension(self):
        assert is_supported_language("app.js") is True

    def test_ts_extension(self):
        assert is_supported_language("lib.ts") is True

    def test_unsupported_extension(self):
        assert is_supported_language("main.cpp") is False

    def test_no_extension(self):
        assert is_supported_language("Makefile") is False


class TestUnifiedAPI:
    """Unified analyze_source() tests."""

    def test_python_source_routing(self):
        src = "import os\nos.system('rm -rf ' + path)\n"
        findings = analyze_source(src, "app.py")
        assert len(findings) > 0

    def test_js_source_routing(self):
        src = 'eval("dangerous_" + input_var);\n'
        findings = analyze_source(src, "app.js")
        assert len(findings) > 0

    def test_unsupported_language_no_error(self):
        src = "int main() { return 0; }\n"
        findings = analyze_source(src, "main.cpp")
        assert findings == []
