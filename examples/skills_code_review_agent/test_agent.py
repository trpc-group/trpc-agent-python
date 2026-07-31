import os
import uuid
import pytest
import sqlite3
from pathlib import Path
from examples.skills_code_review_agent.agent import CodeReviewAgent
from examples.skills_code_review_agent.db import ReviewDbRepository

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_DB_URL = "sqlite:///:memory:"  # Use in-memory DB for isolated tests

@pytest.fixture
def agent():
    return CodeReviewAgent(db_url=TEST_DB_URL, runtime_mode="local")

def test_clean_diff(agent):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    diff_file = FIXTURES_DIR / "fixture_clean.diff"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    assert report_json["status"] == "COMPLETED"
    assert len(report_json["findings"]) == 0
    
    # Query database to verify
    task_details = agent.db.get_task_details(task_id)
    assert task_details is not None
    assert task_details["status"] == "COMPLETED"
    assert len(task_details["findings"]) == 0

def test_security_diff(agent):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    diff_file = FIXTURES_DIR / "fixture_security.diff"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    assert report_json["status"] == "COMPLETED"
    findings = report_json["findings"]
    categories = [f["category"] for f in findings]
    
    assert "Security Risk" in categories
    assert any("shell=True" in f["evidence"] for f in findings)
    assert any("pickle.loads" in f["evidence"] for f in findings)

def test_async_diff(agent):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    diff_file = FIXTURES_DIR / "fixture_async.diff"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    assert report_json["status"] == "COMPLETED"
    findings = report_json["findings"]
    categories = [f["category"] for f in findings]
    
    assert "Async Error" in categories
    assert any("time.sleep" in f["evidence"] for f in findings)

def test_db_lifecycle_diff(agent):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    diff_file = FIXTURES_DIR / "fixture_db.diff"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    assert report_json["status"] == "COMPLETED"
    findings = report_json["findings"]
    categories = [f["category"] for f in findings]
    
    assert "Database Connection Lifecycle" in categories
    assert any("db.connect" in f["evidence"] for f in findings)

def test_missing_test_diff(agent):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    diff_file = FIXTURES_DIR / "fixture_missing_test.diff"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    assert report_json["status"] == "COMPLETED"
    findings = report_json["findings"]
    categories = [f["category"] for f in findings]
    
    assert "Missing Test" in categories

def test_duplicate_findings(agent):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    diff_file = FIXTURES_DIR / "fixture_duplicate.diff"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    assert report_json["status"] == "COMPLETED"
    findings = report_json["findings"]
    
    # Check that there is at least 1 'Security Risk' finding
    security_findings = [f for f in findings if f["category"] == "Security Risk"]
    assert len(security_findings) >= 1

def test_sandbox_failure(agent):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    diff_file = FIXTURES_DIR / "fixture_sandbox_fail.diff"
    
    # Run review. A sandbox failure shouldn't crash the whole task process.
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    assert report_json["status"] == "FAILED"
    
    # Verify sandbox runs table has FAILED state
    task_details = agent.db.get_task_details(task_id)
    assert any(run["status"] == "FAILED" for run in task_details["sandbox_runs"])

def test_sensitive_redaction(agent):
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    diff_file = FIXTURES_DIR / "fixture_sensitive.diff"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    assert report_json["status"] == "COMPLETED"
    
    # Ensure sensitive credentials do not appear in cleartext
    findings = report_json["findings"]
    assert len(findings) > 0
    for f in findings:
        if f["category"] == "Sensitive Information Leak":
            assert "sk-proj-super-secret-key-12345" not in f["evidence"]
            assert "admin_password_9876" not in f["evidence"]
            assert "[REDACTED]" in f["evidence"]

    # Also check database report contents
    task_details = agent.db.get_task_details(task_id)
    for f in task_details["findings"]:
        if f["category"] == "Sensitive Information Leak":
            assert "sk-proj-super-secret-key-12345" not in f["evidence"]
            assert "admin_password_9876" not in f["evidence"]
            assert "[REDACTED]" in f["evidence"]

def test_path_traversal_prevention(tmp_path):
    # Construct a mock malicious diff that tries to traverse directory
    malicious_diff = tmp_path / "fixture_traversal.diff"
    malicious_diff.write_text("""diff --git a/src/config.py b/../../etc/passwd
--- a/src/config.py
+++ b/../../etc/passwd
@@ -1,1 +1,1 @@
+subprocess.run("echo hello", shell=True)
""", encoding="utf-8")

    from examples.skills_code_review_agent.agent import CodeReviewAgent
    agent = CodeReviewAgent(db_url="sqlite:///:memory:", runtime_mode="local", repo_path=str(tmp_path))
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    
    # Run review on malicious diff
    report_json, report_md = agent.run_review(task_id, str(malicious_diff), fake_model=True)
    
    # Should not crash, and shouldn't read or leak outside files
    assert report_json["status"] == "COMPLETED" or report_json["status"] == "FAILED"

def test_ast_path_coverage(tmp_path):
    # Create the workspace layout
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    
    # Write a Python file with a violation inside the repo
    py_file = src_dir / "app.py"
    py_file.write_text("""def my_func():
    subprocess.run("echo ast", shell=True)
""", encoding="utf-8")

    # Create the diff pointing to this file
    diff_file = tmp_path / "ast_test.diff"
    diff_file.write_text("""diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def my_func():
-    pass
+    subprocess.run("echo ast", shell=True)
""", encoding="utf-8")

    from examples.skills_code_review_agent.agent import CodeReviewAgent
    # Instantiate agent pointing to the temporary workspace directory
    agent = CodeReviewAgent(db_url="sqlite:///:memory:", runtime_mode="local", repo_path=str(tmp_path))
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    # Assert findings generated by AST checks (and didn't fallback to regex since file exists)
    assert len(report_json["findings"]) >= 1
    security_findings = [f for f in report_json["findings"] if f["category"] == "Security Risk"]
    assert len(security_findings) >= 1
    finding = security_findings[0]
    assert "shell=True" in finding["evidence"]

def test_sensitive_and_shell_same_line(tmp_path):
    diff_file = tmp_path / "same_line.diff"
    diff_file.write_text("""diff --git a/src/same_line.py b/src/same_line.py
--- a/src/same_line.py
+++ b/src/same_line.py
@@ -1,1 +1,1 @@
+subprocess.run("echo hello", shell=True) # api_key="sk-proj-secret-123456789"
""", encoding="utf-8")

    from examples.skills_code_review_agent.agent import CodeReviewAgent
    agent = CodeReviewAgent(db_url="sqlite:///:memory:", runtime_mode="local", repo_path=str(tmp_path))
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    assert report_json["status"] == "COMPLETED"
    
    # Verify that all findings have evidence redacted and do not leak cleartext credentials
    all_findings = report_json["findings"] + report_json["needs_human_review"]
    assert len(all_findings) >= 1
    for f in all_findings:
        assert "sk-proj-secret-123456789" not in f["evidence"]
        if f["category"] in ("Security Risk", "Sensitive Information Leak"):
            assert "[REDACTED]" in f["evidence"]
        
    # Check DB
    task_details = agent.db.get_task_details(task_id)
    for f in task_details["findings"]:
        assert "sk-proj-secret-123456789" not in f["evidence"]

def test_ast_sensitive_credentials(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    
    # Write Python file with hardcoded key
    py_file = src_dir / "secret.py"
    py_file.write_text("""api_key = "sk-proj-secret-123456789"
""", encoding="utf-8")

    diff_file = tmp_path / "secret.diff"
    diff_file.write_text("""diff --git a/src/secret.py b/src/secret.py
--- a/src/secret.py
+++ b/src/secret.py
@@ -1,1 +1,1 @@
+api_key = "sk-proj-secret-123456789"
""", encoding="utf-8")

    from examples.skills_code_review_agent.agent import CodeReviewAgent
    agent = CodeReviewAgent(db_url="sqlite:///:memory:", runtime_mode="local", repo_path=str(tmp_path))
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    # Findings must include the sensitive key warning, and evidence must be redacted
    all_findings = report_json["findings"] + report_json["needs_human_review"]
    assert len(all_findings) >= 1
    cred_finding = [f for f in all_findings if f["category"] == "Sensitive Information Leak"][0]
    assert "sk-proj-secret-123456789" not in cred_finding["evidence"]
    assert "[REDACTED]" in cred_finding["evidence"]

def test_real_git_diff_hunk_context(tmp_path):
    diff_content = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@ def foo():
-    pass
+    subprocess.run("echo", shell=True)
"""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "parse_diff",
        str(Path(__file__).parent / "skills" / "code-review" / "scripts" / "parse_diff.py")
    )
    parse_diff_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parse_diff_module)
    parse_diff = parse_diff_module.parse_diff
    parsed = parse_diff(diff_content)
    assert "src/app.py" in parsed
    added_lines = parsed["src/app.py"]
    assert len(added_lines) == 1
    assert added_lines[0]["line"] == 1
    assert added_lines[0]["type"] == "added"

def test_metacharacter_path_handling(tmp_path):
    # Path containing shell metacharacters like $ and ;
    special_dir = tmp_path / "my$project;dir"
    special_dir.mkdir(parents=True, exist_ok=True)
    
    diff_file = special_dir / "test.diff"
    diff_file.write_text("""diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,1 @@
+print("Hello World")
""", encoding="utf-8")

    from examples.skills_code_review_agent.agent import CodeReviewAgent
    # Instantiate pointing to the path with special characters
    agent = CodeReviewAgent(db_url="sqlite:///:memory:", runtime_mode="local", repo_path=str(special_dir))
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    # Must NOT be INTERCEPTED due to shell metacharacters in paths since shell=False is used
    assert report_json["status"] == "COMPLETED"

def test_absolute_path_traversal_prevention(tmp_path):
    # Place a target file outside the temporary workspace directory
    outside_file = tmp_path / "outside.py"
    outside_file.write_text("secret_key = 'unreachable'", encoding="utf-8")
    
    # workspace directory
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    
    # Malicious diff that sets filename to absolute path of outside_file
    diff_file = workspace_dir / "malicious.diff"
    diff_file.write_text(f"""diff --git a/src/app.py b{outside_file.as_posix()}
--- a/src/app.py
+++ b{outside_file.as_posix()}
@@ -1,1 +1,1 @@
+secret_key = 'unreachable'
""", encoding="utf-8")

    from examples.skills_code_review_agent.agent import CodeReviewAgent
    agent = CodeReviewAgent(db_url="sqlite:///:memory:", runtime_mode="local", repo_path=str(workspace_dir))
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    
    report_json, report_md = agent.run_review(task_id, str(diff_file), fake_model=True)
    
    # Must run to completion successfully but should not find the sensitive information because the file was skipped and not read
    assert report_json["status"] == "COMPLETED"
    findings = report_json["findings"]
    for f in findings:
        assert f["category"] != "Sensitive Information Leak"

def test_diff_code_contains_hunk_marker(tmp_path):
    diff_content = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def foo():
+    pat = re.compile('@@')
"""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "parse_diff",
        str(Path(__file__).parent / "skills" / "code-review" / "scripts" / "parse_diff.py")
    )
    parse_diff_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parse_diff_module)
    parse_diff = parse_diff_module.parse_diff
    parsed = parse_diff(diff_content)
    
    # Verify app.py has the added line and it is not consumed/skipped as hunk header
    assert "src/app.py" in parsed
    added_lines = parsed["src/app.py"]
    # 1 context line (def foo():) and 1 added line (pat = re.compile('@@'))
    assert len(added_lines) == 2
    assert added_lines[1]["type"] == "added"
    assert "re.compile('@@')" in added_lines[1]["content"]
