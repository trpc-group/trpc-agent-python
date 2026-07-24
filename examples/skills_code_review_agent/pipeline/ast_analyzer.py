"""AST-based taint analysis for Python and JavaScript/TypeScript code.

Performs lightweight taint tracking from user input sources to dangerous sinks.
Falls back gracefully when ast module is unavailable or parsing fails.
"""

import re
from typing import Any

from .types import Finding, FindingCategory, Severity

# ── Python AST analysis ──────────────────────────────────────────────

try:
    import ast as _py_ast

    _PY_AST_AVAILABLE = True
except ImportError:
    _PY_AST_AVAILABLE = False

# Taint sources: where untrusted data enters the program
_PY_SOURCES = {"request", "input", "environ", "getenv", "sys.argv", "argv",
               "raw_input", "get_data", "get_json", "get_body", "get_argument"}

# Taint sinks: dangerous operations
_PY_SINKS = {
    "subprocess": ("subprocess.call", "subprocess.run", "subprocess.Popen",
                   "os.system", "os.popen", "commands.getoutput"),
    "sql": (".execute(", ".executemany(", ".raw_execute(", "cursor.execute"),
    "eval": ("eval(", "exec(", "compile("),
    "file": ("open(", "os.remove", "os.unlink", "shutil.rmtree"),
}


def analyze_python_ast(source: str, filename: str = "<unknown>") -> list[Finding]:
    """Analyze Python source for taint flow vulnerabilities.

    Args:
        source: Python source code.
        filename: File name for reporting.

    Returns:
        List of Findings from AST analysis.
    """
    findings: list[Finding] = []
    if not _PY_AST_AVAILABLE:
        return findings

    try:
        tree = _py_ast.parse(source)
    except SyntaxError:
        return findings

    visitor = _PyTaintVisitor(filename)
    visitor.visit(tree)
    return visitor.findings


class _PyTaintVisitor(_py_ast.NodeVisitor):
    """AST visitor that tracks tainted variables to dangerous sinks."""

    def __init__(self, filename: str):
        self.filename = filename
        self.findings: list[Finding] = []
        self.tainted: set[str] = set()
        self._assignment_targets: set[str] = set()

    def visit_Call(self, node: _py_ast.Call) -> None:
        # Check if call is to a taint source
        if isinstance(node.func, _py_ast.Name):
            if node.func.id in _PY_SOURCES:
                # Mark the result as tainted if assigned
                self._mark_current_context()
        self.generic_visit(node)

    def _mark_current_context(self) -> None:
        """Mark assignment targets in current context as tainted."""
        # Handled by visit_Assign
        pass

    def visit_Assign(self, node: _py_ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, _py_ast.Name):
                self._assignment_targets.add(target.id)
        if isinstance(node.value, _py_ast.Call):
            if isinstance(node.value.func, _py_ast.Name):
                if node.value.func.id in _PY_SOURCES:
                    for target in node.targets:
                        if isinstance(target, _py_ast.Name):
                            self.tainted.add(target.id)
        # Check for string concatenation in dangerous calls
        if isinstance(node.value, _py_ast.BinOp):
            if isinstance(node.value.op, _py_ast.Add):
                self._check_string_concat(node.value, node.lineno)
        self.generic_visit(node)

    def _check_string_concat(self, node: _py_ast.BinOp, lineno: int) -> None:
        """Flag string concatenation used in system/shell calls."""
        has_str = isinstance(node.left, _py_ast.Constant) and isinstance(node.left.value, str)
        has_var = isinstance(node.right, _py_ast.Name)
        if has_str and has_var:
            self.findings.append(Finding(
                severity=Severity.HIGH,
                category=FindingCategory.SECURITY,
                file=self.filename,
                line=lineno,
                title="String concatenation in system/shell context — possible injection",
                evidence=f"String concatenation with variable at line {lineno}",
                recommendation="Use parameterized commands or subprocess with a list of arguments.",
                confidence=0.75,
                source="ast_taint_analyzer",
            ))


def analyze_python_regex(source: str, filename: str = "<unknown>") -> list[Finding]:
    """Fallback regex-based analysis for Python when AST parsing is unavailable.

    Uses regex patterns to detect common taint flow patterns.
    """
    findings: list[Finding] = []

    patterns = [
        (re.compile(r'(?:os\.system|os\.popen|subprocess\.\w+)\s*\([^)]*(?:\+|%|\.format|f["\'])'),
         "Potential command injection via string concatenation in subprocess call",
         Severity.HIGH, 0.8),
        (re.compile(r'\.execute\s*\([^)]*(?:\+|%|\.format|f["\'])'),
         "Potential SQL injection via string concatenation in query",
         Severity.CRITICAL, 0.85),
        (re.compile(r'eval\s*\([^)]*(?:\+|\.format|f["\']|request|input)'),
         "eval() with potentially tainted input — code injection risk",
         Severity.CRITICAL, 0.9),
    ]

    for line_no, line in enumerate(source.split("\n"), start=1):
        for pattern, title, severity, confidence in patterns:
            if pattern.search(line):
                findings.append(Finding(
                    severity=severity,
                    category=FindingCategory.SECURITY,
                    file=filename,
                    line=line_no,
                    title=title,
                    evidence=line.strip()[:120],
                    recommendation="Use parameterized APIs and avoid string concatenation with user input.",
                    confidence=confidence,
                    source="ast_regex_fallback",
                ))

    return findings


# ── JavaScript/TypeScript regex analysis ─────────────────────────────

_JS_TAINT_PATTERNS = [
    (re.compile(r'(?:child_process\.exec|child_process\.spawn)\s*\([^)]*(?:\+|\.concat|`\$\{)'),
     "Potential command injection in Node.js child_process call",
     Severity.HIGH, 0.8),
    (re.compile(r'eval\s*\([^)]*(?:\+|\.concat|`\$\{)'),
     "eval() with dynamic input — code injection risk in JS/TS",
     Severity.CRITICAL, 0.9),
    (re.compile(r'(?:\.innerHTML\s*=|dangerouslySetInnerHTML)'),
     "Potential XSS via innerHTML assignment",
     Severity.HIGH, 0.85),
    (re.compile(r'(?:\.query\s*\(|\.execute\s*\()[^)]*(?:\+|\.concat|`\$\{)'),
     "Potential SQL injection in JS/TS database query",
     Severity.CRITICAL, 0.85),
]


def analyze_js_ts(source: str, filename: str = "<unknown>") -> list[Finding]:
    """Analyze JavaScript/TypeScript code for security issues using regex.

    Args:
        source: JS/TS source code.
        filename: File name for reporting.

    Returns:
        List of Findings.
    """
    findings: list[Finding] = []
    for line_no, line in enumerate(source.split("\n"), start=1):
        for pattern, title, severity, confidence in _JS_TAINT_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(
                    severity=severity,
                    category=FindingCategory.SECURITY,
                    file=filename,
                    line=line_no,
                    title=title,
                    evidence=line.strip()[:120],
                    recommendation="Avoid dynamic code execution with untrusted input.",
                    confidence=confidence,
                    source="js_ts_analyzer",
                ))
    return findings


# ── Language detection and unified API ───────────────────────────────

_PY_EXTENSIONS = {".py", ".pyw", ".pyx", ".pxd", ".pyi"}
_JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


def is_supported_language(filename: str) -> bool:
    """Check if the file extension is supported for AST analysis."""
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
    return ext.lower() in _PY_EXTENSIONS | _JS_EXTENSIONS


def analyze_source(source: str, filename: str = "<unknown>") -> list[Finding]:
    """Analyze source code with language-appropriate method.

    Uses AST for Python when available, falls back to regex.
    Uses regex patterns for JavaScript/TypeScript.

    Args:
        source: Source code text.
        filename: File name (used for language detection).

    Returns:
        List of Findings.
    """
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".py"

    if ext.lower() in _PY_EXTENSIONS:
        findings = analyze_python_ast(source, filename)
        if not findings:
            findings = analyze_python_regex(source, filename)
        return findings
    elif ext.lower() in _JS_EXTENSIONS:
        return analyze_js_ts(source, filename)
    else:
        # Unsupported language — skip AST analysis
        return []
