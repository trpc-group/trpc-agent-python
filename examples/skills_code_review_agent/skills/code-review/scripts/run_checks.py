#!/usr/bin/env python3
import argparse
import json
import re
import os
import ast
import sys

# Categories
CAT_SECURITY = "Security Risk"
CAT_ASYNC = "Async Error"
CAT_RESOURCE = "Resource Leak"
CAT_DB = "Database Connection Lifecycle"
CAT_TEST = "Missing Test"
CAT_SENSITIVE = "Sensitive Information Leak"

class ASTCodeReviewVisitor(ast.NodeVisitor):
    """
    AST Visitor to analyze Python source code with high precision.
    """
    def __init__(self, filename, modified_lines_set):
        self.filename = filename
        self.modified_lines = modified_lines_set
        self.findings = []
        self.in_async_def = False
        self.in_with_stmt = False

    def visit_AsyncFunctionDef(self, node):
        old_state = self.in_async_def
        self.in_async_def = True
        self.generic_visit(node)
        self.in_async_def = old_state

    def visit_With(self, node):
        old_state = self.in_with_stmt
        self.in_with_stmt = True
        self.generic_visit(node)
        self.in_with_stmt = old_state

    def visit_Call(self, node):
        # Only report findings on lines that were actually modified in the diff
        if node.lineno in self.modified_lines:
            # 1. Check for shell=True in subprocess calls
            is_subprocess = False
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "subprocess" and node.func.attr in ("run", "Popen", "call", "check_output"):
                    is_subprocess = True
            elif isinstance(node.func, ast.Name) and node.func.id in ("run", "Popen", "call", "check_output"):
                # if imported directly
                is_subprocess = True

            if is_subprocess:
                for keyword in node.keywords:
                    if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                        self.findings.append({
                            "severity": "high",
                            "category": CAT_SECURITY,
                            "file": self.filename,
                            "line": node.lineno,
                            "title": "Subprocess execution with shell=True",
                            "evidence": f"Subprocess call with shell=True on line {node.lineno}",
                            "recommendation": "Avoid using shell=True to prevent command/shell injection risks. Pass arguments as a list instead.",
                            "confidence": "high",
                            "source": "ast_analyzer"
                        })

            # 2. Check for pickle.loads
            is_pickle_loads = False
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "pickle" and node.func.attr == "loads":
                    is_pickle_loads = True
            elif isinstance(node.func, ast.Name) and node.func.id == "loads":
                is_pickle_loads = True

            if is_pickle_loads:
                self.findings.append({
                    "severity": "high",
                    "category": CAT_SECURITY,
                    "file": self.filename,
                    "line": node.lineno,
                    "title": "Unsafe deserialization using pickle",
                    "evidence": f"pickle.loads call on line {node.lineno}",
                    "recommendation": "Use json or safer deserialization methods instead of pickle to prevent arbitrary code execution.",
                    "confidence": "high",
                    "source": "ast_analyzer"
                })

            # 3. Check for blocking time.sleep inside async functions
            if self.in_async_def:
                is_time_sleep = False
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "time" and node.func.attr == "sleep":
                        is_time_sleep = True
                elif isinstance(node.func, ast.Name) and node.func.id == "sleep":
                    is_time_sleep = True

                if is_time_sleep:
                    self.findings.append({
                        "severity": "medium",
                        "category": CAT_ASYNC,
                        "file": self.filename,
                        "line": node.lineno,
                        "title": "Blocking call time.sleep() in async function",
                        "evidence": f"time.sleep() called inside async def on line {node.lineno}",
                        "recommendation": "Use 'await asyncio.sleep()' instead of 'time.sleep()' to avoid blocking the event loop.",
                        "confidence": "high",
                        "source": "ast_analyzer"
                    })

            # 4. Check for open() outside 'with' statement
            if isinstance(node.func, ast.Name) and node.func.id == "open" and not self.in_with_stmt:
                self.findings.append({
                    "severity": "medium",
                    "category": CAT_RESOURCE,
                    "file": self.filename,
                    "line": node.lineno,
                    "title": "File opened without context manager",
                    "evidence": f"open() called outside with-statement on line {node.lineno}",
                    "recommendation": "Use 'with open(...) as f:' context manager to ensure the file is closed automatically.",
                    "confidence": "high",
                    "source": "ast_analyzer"
                })

            # 5. Check for db.connect() outside 'with' statement
            is_db_connect = False
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "db" and node.func.attr == "connect":
                    is_db_connect = True
            elif isinstance(node.func, ast.Name) and node.func.id == "connect":
                is_db_connect = True

            if is_db_connect and not self.in_with_stmt:
                self.findings.append({
                    "severity": "high",
                    "category": CAT_DB,
                    "file": self.filename,
                    "line": node.lineno,
                    "title": "Database connection or session created outside context manager",
                    "evidence": f"Database connection created outside with-statement on line {node.lineno}",
                    "recommendation": "Manage database connections or sessions with a context manager (with ...) or ensure proper session.close() is called.",
                    "confidence": "high",
                    "source": "ast_analyzer"
                })

        self.generic_visit(node)

def run_checks(parsed_diff, src_dir):
    findings = []
    
    # 1. Check for Missing Tests across all modified files
    modified_source_files = []
    has_test_changes = False
    
    for filename in parsed_diff.keys():
        fn_lower = filename.lower()
        if "test" in fn_lower:
            has_test_changes = True
        elif fn_lower.endswith(".py"):
            modified_source_files.append(filename)
            
    if modified_source_files and not has_test_changes:
        for src_file in modified_source_files:
            findings.append({
                "severity": "medium",
                "category": CAT_TEST,
                "file": src_file,
                "line": 1,
                "title": "Missing unit test file or test updates",
                "evidence": f"Modified source file: {src_file} without any matching test file changes in diff",
                "recommendation": f"Create or update a test file (e.g., tests/test_{os.path.basename(src_file)}) to verify these changes.",
                "confidence": "high",
                "source": "static_analyzer"
            })

    # 2. Scan each file using AST (if possible) or regex line-by-line fallback
    for filename, lines in parsed_diff.items():
        # Try to parse the full file from workspace if present, or reconstruct code
        full_code = None
        local_path = os.path.join(src_dir, filename) if src_dir else filename
        
        # Build set of modified lines
        modified_lines_set = {item["line"] for item in lines if item["type"] == "added"}
        
        # Attempt AST check if file is readable
        ast_success = False
        if os.path.exists(local_path):
            try:
                with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
                    full_code = f.read()
                tree = ast.parse(full_code)
                visitor = ASTCodeReviewVisitor(filename, modified_lines_set)
                visitor.visit(tree)
                findings.extend(visitor.findings)
                ast_success = True
            except Exception:
                pass # Fallback to regex pattern matching on diff lines
                
        # Regex / Line-by-line fallback pattern matching
        if not ast_success:
            for item in lines:
                line_num = item["line"]
                content = item["content"]
                
                # TRIGGER_SANDBOX_CRASH
                if "TRIGGER_SANDBOX_CRASH" in content:
                    raise ValueError("Simulated sandbox crash triggered by diff content")

                # Security checks
                if "shell=True" in content and ("subprocess" in content or "run(" in content or "Popen" in content):
                    findings.append({
                        "severity": "high",
                        "category": CAT_SECURITY,
                        "file": filename,
                        "line": line_num,
                        "title": "Subprocess execution with shell=True",
                        "evidence": content.strip(),
                        "recommendation": "Avoid using shell=True to prevent command/shell injection risks. Pass arguments as a list instead.",
                        "confidence": "high",
                        "source": "static_analyzer"
                    })
                
                if "pickle.loads(" in content:
                    findings.append({
                        "severity": "high",
                        "category": CAT_SECURITY,
                        "file": filename,
                        "line": line_num,
                        "title": "Unsafe deserialization using pickle",
                        "evidence": content.strip(),
                        "recommendation": "Use json or safer deserialization methods instead of pickle to prevent arbitrary code execution.",
                        "confidence": "high",
                        "source": "static_analyzer"
                    })

                # Sensitive information check with redaction
                cred_patterns = [
                    r'(?i)(api_key|password|token|secret|passwd)\s*=\s*["\']([^"\']+)["\']',
                ]
                for pat in cred_patterns:
                    m = re.search(pat, content)
                    if m:
                        key_name = m.group(1)
                        raw_val = m.group(2)
                        if len(raw_val) > 4 and not raw_val.startswith("os.environ") and not raw_val.startswith("YOUR_"):
                            redacted_evidence = content.replace(raw_val, "[REDACTED]").strip()
                            findings.append({
                                "severity": "critical",
                                "category": CAT_SENSITIVE,
                                "file": filename,
                                "line": line_num,
                                "title": "Potential hardcoded sensitive credential",
                                "evidence": redacted_evidence,
                                "recommendation": f"Remove the hardcoded credential for '{key_name}' and load it from environment variables or a configuration vault.",
                                "confidence": "high",
                                "source": "static_analyzer"
                            })

                # Async Checks
                if "time.sleep(" in content:
                    findings.append({
                        "severity": "medium",
                        "category": CAT_ASYNC,
                        "file": filename,
                        "line": line_num,
                        "title": "Blocking call time.sleep() in async environment",
                        "evidence": content.strip(),
                        "recommendation": "Use 'await asyncio.sleep()' instead of 'time.sleep()' to avoid blocking the event loop.",
                        "confidence": "high",
                        "source": "static_analyzer"
                    })
                    
                if "async def" not in content and re.search(r'\b[a-zA-Z0-9_]+_async\s*\(', content) and "await " not in content:
                    findings.append({
                        "severity": "high",
                        "category": CAT_ASYNC,
                        "file": filename,
                        "line": line_num,
                        "title": "Coroutine called without await",
                        "evidence": content.strip(),
                        "recommendation": "Prepend 'await' to the coroutine call or schedule it via asyncio.create_task().",
                        "confidence": "medium",
                        "source": "static_analyzer"
                    })

                # Resource leak checks
                if re.search(r'\w+\s*=\s*open\(', content) and "with " not in content:
                    findings.append({
                        "severity": "medium",
                        "category": CAT_RESOURCE,
                        "file": filename,
                        "line": line_num,
                        "title": "File opened without context manager",
                        "evidence": content.strip(),
                        "recommendation": "Use 'with open(...) as f:' context manager to ensure the file is closed automatically.",
                        "confidence": "high",
                        "source": "static_analyzer"
                    })

                # DB connection/transaction lifecycle checks
                if ("db.connect(" in content or "create_engine(" in content or "sessionmaker" in content) and "with " not in content:
                    findings.append({
                        "severity": "high",
                        "category": CAT_DB,
                        "file": filename,
                        "line": line_num,
                        "title": "Database connection or session created outside context manager",
                        "evidence": content.strip(),
                        "recommendation": "Manage database connections or sessions with a context manager (with ...) or ensure proper session.close() is called.",
                        "confidence": "high",
                        "source": "static_analyzer"
                    })
        else:
            # Still check for TRIGGER_SANDBOX_CRASH even if AST succeeded
            for item in lines:
                if "TRIGGER_SANDBOX_CRASH" in item["content"]:
                    raise ValueError("Simulated sandbox crash triggered by diff content")

    return findings

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--parsed-diff', required=True)
    parser.add_argument('--src-dir', required=False, default='.')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    if not os.path.exists(args.parsed_diff):
        print(f"Error: parsed diff {args.parsed_diff} does not exist.")
        with open(args.output, 'w') as f:
            json.dump([], f)
        return

    with open(args.parsed_diff, 'r', encoding='utf-8') as f:
        parsed_diff = json.load(f)

    findings = run_checks(parsed_diff, args.src_dir)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(findings, f, indent=2)

if __name__ == '__main__':
    main()
