# Hidden Test Samples for Detection Rate / False Positive Rate Evaluation
#
# These samples are NOT exposed to the public fixtures and are used for
# AC-02 (detection rate ≥ 80%) and AC-03 (false positive rate ≤ 15%) evaluation.
#
# Each sample has:
# - diff_content: The code diff to review
# - expected_findings: Ground truth list of expected findings
#   (file, line, severity, category, title keywords)

# Import runtime generators for samples that contain fake credentials
# (generated via string concatenation to avoid CodeCC false positives)
import sys
from pathlib import Path
_parent = Path(__file__).resolve().parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))
from fixtures.generate_fixtures import gen_02_secret, gen_06_jwt, gen_08_db_url


SAMPLE_01_VULN_SQL = {
    "id": "hidden_01",
    "description": "SQL injection via f-string in query",
    "diff_content": """--- a/src/user_dao.py
+++ b/src/user_dao.py
@@ -1,5 +1,12 @@
 import sqlite3


 def get_user(user_id):
     conn = sqlite3.connect("app.db")
     cursor = conn.cursor()
     cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
     return cursor.fetchone()
+
+
+def search_users(name):
+    conn = sqlite3.connect("app.db")
+    cursor = conn.cursor()
+    cursor.execute(f"SELECT * FROM users WHERE name LIKE '%{name}%'")
+    return cursor.fetchall()""",
    "expected_findings": [
        {"file": "src/user_dao.py", "line": 14, "severity": "critical", "category": "security", "title": "SQL注入"},
        {"file": "src/user_dao.py", "line": 12, "severity": "warning", "category": "db", "title": "数据库连接未关闭"},
    ],
}

SAMPLE_02_VULN_SECRET = {
    "id": "hidden_02",
    "description": "Multiple hardcoded secrets in config",
    "diff_content": gen_02_secret(),
    "expected_findings": [
        {"file": "src/aws_config.py", "line": 3, "severity": "critical", "category": "secret", "title": "AWS Access Key"},
    ],
}

SAMPLE_03_CLEAN = {
    "id": "hidden_03",
    "description": "Clean code with no issues",
    "diff_content": """--- a/src/utils.py
+++ b/src/utils.py
@@ -0,0 +1,15 @@
+import os
+from typing import List
+
+
+def format_name(first: str, last: str) -> str:
+    \"\"\"Format a person's name.\"\"\"
+    return f"{first} {last}"
+
+
+def add_prefix(values: List[str], prefix: str) -> List[str]:
+    \"\"\"Add a prefix to each string in the list.\"\"\"
+    return [prefix + v for v in values]
+
+
+CONFIG_PATH = os.getenv("CONFIG_PATH", "/etc/app/config.yaml")""",
    "expected_findings": [],
}

SAMPLE_04_VULN_CMD = {
    "id": "hidden_04",
    "description": "Command injection via os.system",
    "diff_content": """--- a/src/deploy.py
+++ b/src/deploy.py
@@ -1,5 +1,12 @@
 import os
 import subprocess


 def deploy(version):
    print(f"Deploying version {version}")
+
+
+def run_command(cmd):
+    \"\"\"Run a shell command.\"\"\"
+    result = os.system(f"bash -c {cmd}")
+    return result == 0""",
    "expected_findings": [
        {"file": "src/deploy.py", "line": 10, "severity": "critical", "category": "security", "title": "命令注入"},
    ],
}

SAMPLE_05_VULN_LEAK = {
    "id": "hidden_05",
    "description": "File handle leak and resource leak",
    "diff_content": """--- a/src/file_processor.py
+++ b/src/file_processor.py
@@ -1,5 +1,15 @@
+import json
+
+
+def read_config(path):
+    f = open(path, "r")
+    data = json.load(f)
+    return data
+
+
+def process_logs(paths):
+    for p in paths:
+        f = open(p, "r")
+        yield f.read()""",
    "expected_findings": [
        {"file": "src/file_processor.py", "line": 5, "severity": "warning", "category": "resource_leak", "title": "文件句柄未使用"},
        {"file": "src/file_processor.py", "line": 11, "severity": "warning", "category": "resource_leak", "title": "文件句柄未使用"},
    ],
}

SAMPLE_06_VULN_JWT = {
    "id": "hidden_06",
    "description": "JWT token and private key hardcoded",
    "diff_content": gen_06_jwt(),
    "expected_findings": [
        {"file": "src/auth_config.py", "line": 3, "severity": "critical", "category": "secret", "title": "JWT Token"},
        {"file": "src/auth_config.py", "line": 4, "severity": "critical", "category": "secret", "title": "私钥"},
    ],
}

SAMPLE_07_VULN_ASYNC = {
    "id": "hidden_07",
    "description": "Async resource leak with time.sleep",
    "diff_content": """--- a/src/async_worker.py
+++ b/src/async_worker.py
@@ -1,5 +1,15 @@
 import asyncio
 import time


 async def fetch_data(url):
    return {"data": "ok"}
+
+
+async def poll_server():
+    session = aiohttp.ClientSession()
+    while True:
+        resp = await session.get("https://api.example.com/health")
+        data = await resp.json()
+        time.sleep(5)
+        if data["status"] == "ok":
+            break""",
    "expected_findings": [
        {"file": "src/async_worker.py", "line": 10, "severity": "warning", "category": "resource_leak", "title": "aiohttp"},
        {"file": "src/async_worker.py", "line": 14, "severity": "warning", "category": "async", "title": "time.sleep"},
    ],
}

SAMPLE_08_VULN_DB_URL = {
    "id": "hidden_08",
    "description": "Database connection string with password",
    "diff_content": gen_08_db_url(),
    "expected_findings": [
        {"file": "src/db_config.py", "line": 3, "severity": "critical", "category": "secret", "title": "数据库连接字符串"},
    ],
}

# Collection of all hidden samples
HIDDEN_SAMPLES = [
    SAMPLE_01_VULN_SQL,
    SAMPLE_02_VULN_SECRET,
    SAMPLE_03_CLEAN,
    SAMPLE_04_VULN_CMD,
    SAMPLE_05_VULN_LEAK,
    SAMPLE_06_VULN_JWT,
    SAMPLE_07_VULN_ASYNC,
    SAMPLE_08_VULN_DB_URL,
]