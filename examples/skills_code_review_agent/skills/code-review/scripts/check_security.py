# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Security checker: eval/exec, shell=True, pickle, unsafe yaml.load, SQL injection."""
import re
import sys

from checklib import emit, finding, iter_added_py_lines, load_files

RULES = [
    (re.compile(r"\beval\s*\("), "high", "Use of eval() on dynamic data",
     "Avoid eval(); use ast.literal_eval or explicit parsing.", 0.9),
    (re.compile(r"\bexec\s*\("), "high", "Use of exec() on dynamic data",
     "Avoid exec(); restructure the code to avoid dynamic execution.", 0.9),
    (re.compile(r"shell\s*=\s*True"), "high", "subprocess with shell=True",
     "Pass an argument list and shell=False to avoid shell injection.", 0.85),
    (re.compile(r"pickle\.loads?\s*\("), "high", "Unpickling untrusted data",
     "Never unpickle untrusted input; use JSON or a safe serializer.", 0.8),
    (re.compile(r"yaml\.load\s*\((?!.*SafeLoader)"), "medium", "yaml.load without SafeLoader",
     "Use yaml.safe_load or pass Loader=yaml.SafeLoader.", 0.75),
    (re.compile(r"\.execute\s*\(\s*f[\"']"), "high", "SQL built with f-string (possible injection)",
     "Use parameterized queries (placeholders) instead of string interpolation.", 0.85),
    (re.compile(r"\.execute\s*\([^,)]*[\"']\s*\+"), "high", "SQL built with string concatenation",
     "Use parameterized queries (placeholders) instead of concatenation.", 0.8),
    (re.compile(r"os\.system\s*\("), "medium", "Use of os.system",
     "Use subprocess.run with an argument list.", 0.7),
]


def main():
    files = load_files(sys.argv)
    findings = []
    for path, line_no, text in iter_added_py_lines(files):
        for pattern, severity, title, rec, conf in RULES:
            if pattern.search(text):
                findings.append(finding(severity, "security", path, line_no, title,
                                        evidence=text.strip(), recommendation=rec, confidence=conf))
    emit(findings)


if __name__ == "__main__":
    main()
