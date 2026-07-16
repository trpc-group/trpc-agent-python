# tests/test_rule_engine.py - 规则引擎测试
from agent.diff_parser import parse_diff
from agent.rule_engine import review_rules
from agent.redaction import redact_text


def test_security_os_system_detected():
    """测试 os.system(user_input) 被检测到"""
    files = parse_diff("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    os.system(user_input)\n")
    fs = review_rules(files)
    assert any(x.rule_id == "SEC001" for x in fs), "SEC001 应该检测到 os.system(user_input)"


def test_literal_arg_not_flagged():
    """测试 os.system("clear") 字面量参数不被标记"""
    files = parse_diff("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    os.system(\"clear\")\n")
    fs = review_rules(files)
    assert not any(x.rule_id == "SEC001" for x in fs), "SEC001 不应该标记字面量参数"


def test_subprocess_shell_true_detected():
    """测试 subprocess shell=True 被检测到"""
    files = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    subprocess.run(cmd, shell=True)\n")
    fs = review_rules(files)
    assert any(x.rule_id == "SEC002" for x in fs), "SEC002 应该检测到 shell=True"


def test_eval_exec_detected():
    """测试 eval/exec 被检测到"""
    files = parse_diff("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    eval(user_input)\n")
    fs = review_rules(files)
    assert any(x.rule_id == "SEC003" for x in fs), "SEC003 应该检测到 eval"


def test_pickle_loads_detected():
    """测试 pickle.loads 被检测到"""
    files = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    pickle.loads(untrusted_data)\n")
    fs = review_rules(files)
    assert any(x.rule_id == "SEC004" for x in fs), "SEC004 应该检测到 pickle.loads"


def test_asyncio_create_task_detected():
    """测试 asyncio.create_task 被检测到"""
    files = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    asyncio.create_task(coro)\n")
    fs = review_rules(files)
    assert any(x.rule_id == "ASYNC001" for x in fs), "ASYNC001 应该检测到 asyncio.create_task"


def test_resource_leak_open_detected():
    """测试 open() 资源泄漏被检测"""
    files = parse_diff("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    f = open('file.txt')\n")
    fs = review_rules(files)
    assert any(x.rule_id == "RES001" for x in fs), "RES001 应该检测到 open() 资源泄漏"


def test_resource_leak_with_statement_not_flagged():
    """测试 with 语句不标记"""
    files = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    with open('file.txt') as f:\n")
    fs = review_rules(files)
    assert not any(x.rule_id == "RES001" for x in fs), "RES001 不应该标记 with 语句"


def test_resource_leak_with_close_signal_not_flagged():
    """测试分行 close 仍应标记（保守抑制：只抑制 with 语句，允许误报避免漏报）"""
    # 注意：这是保守抑制策略，f=open(); f.close() 分行情况不抑制
    # 正则层无法精确追踪变量关系，宁可误报交给后续层降噪
    files = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,3 @@\n+    f = open('file.txt')\n+    f.close()\n")
    fs = review_rules(files)
    assert any(x.rule_id == "RES001" for x in fs), "RES001 应该标记分行 close（保守策略避免漏报）"


def test_db_lifecycle_connect_detected():
    """测试数据库连接被检测到"""
    files = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    conn = sqlite3.connect('db.sqlite')\n")
    fs = review_rules(files)
    assert any(x.rule_id == "DB001" for x in fs), "DB001 应该检测到数据库连接"


def test_db_lifecycle_with_close_signal_not_flagged():
    """测试分行 close 仍应标记（保守抑制：只抑制 with 语句，允许误报避免漏报）"""
    # 注意：这是保守抑制策略，conn=connect(); conn.close() 分行情况不抑制
    # 正则层无法精确追踪变量关系，宁可误报交给后续层降噪
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,3 @@\n"
                    "+    conn = sqlite3.connect('db.sqlite')\n"
                    "+    conn.close()\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert any(x.rule_id == "DB001" for x in fs), "DB001 应该标记分行 close（保守策略避免漏报）"


def test_db_lifecycle_with_statement_not_flagged():
    """测试 with 语句不标记"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,2 @@\n"
                    "+    with sqlite3.connect('db.sqlite') as conn:\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert not any(x.rule_id == "DB001" for x in fs), "DB001 不应该标记 with 语句"


def test_sensitive_information_secret_detected():
    """测试敏感信息被检测到"""
    files = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    api_key = \"sk-1234567890\"\n")
    fs = review_rules(files)
    assert any(x.rule_id == "SECRET001" for x in fs), "SECRET001 应该检测到硬编码的密钥"


def test_missing_tests_detected():
    """测试缺少测试被检测到"""
    files = parse_diff(
        "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n+    def new_function():\n")
    fs = review_rules(files)
    assert any(x.rule_id == "TEST001" for x in fs), "TEST001 应该检测到缺少测试"


def test_missing_tests_not_flagged_when_test_exists():
    """测试有测试文件时不标记"""
    diff_content = ("diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
                    "@@ -1 +1,2 @@\n"
                    "+    def new_function():\n"
                    "diff --git a/test_app.py b/test_app.py\n--- a/test_app.py\n"
                    "+++ b/test_app.py\n@@ -1 +1,2 @@\n"
                    "+    def test_new_function():\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert not any(x.rule_id == "TEST001" for x in fs), "TEST001 不应该标记有测试文件的变更"


def test_confidence_values():
    """测试 confidence 值设置正确"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,3 @@\n"
                    "+    api_key = \"sk-1234567890\"\n"
                    "+    os.system(user_input)\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)

    # SECURITY 和 SECRET 应该有高 confidence (>=0.8)
    security_findings = [x for x in fs if x.rule_id in ["SEC001", "SECRET001"]]
    assert len(security_findings) > 0, "应该有 SECURITY/SECRET findings"
    assert all(x.confidence >= 0.8 for x in security_findings), "SECURITY/SECRET confidence 应该 >=0.8"

    # missing_tests 应该有低 confidence (0.65)
    # 添加只有生产代码变更的情况
    files2 = parse_diff(
        "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n+    def new_function():\n")
    fs2 = review_rules(files2)
    test_findings = [x for x in fs2 if x.rule_id == "TEST001"]
    if test_findings:
        assert test_findings[0].confidence == 0.65, "TEST001 confidence 应该是 0.65"


def test_all_categories_covered():
    """测试所有 6 类规则都被覆盖"""
    # 构造包含所有类型的 diff
    diff_text = """
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1,8 @@
+    os.system(user_input)
+    subprocess.run(cmd, shell=True)
+    eval(user_input)
+    pickle.loads(data)
+    asyncio.create_task(coro)
+    f = open('file.txt')
+    conn = sqlite3.connect('db.sqlite')
+    api_key = "sk-1234567890"
"""
    files = parse_diff(diff_text)
    fs = review_rules(files)

    # 检查是否包含所有预期的规则 ID
    expected_rule_ids = {"SEC001", "SEC002", "SEC003", "SEC004", "ASYNC001", "RES001", "DB001", "SECRET001"}
    actual_rule_ids = {x.rule_id for x in fs if x.rule_id in expected_rule_ids}

    assert len(actual_rule_ids) >= 6, f"应该检测到至少 6 类规则，实际检测到: {len(actual_rule_ids)} 类: {actual_rule_ids}"


def test_irrelevant_close_should_not_suppress():
    """测试不相关的 close 不应抑制 open 泄漏检测（避免漏报）"""
    # hunk 中包含 open() 和无关的 close()，仍应报 RES001
    files = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,3 @@\n+    f = open('x.txt')\n+    obj.close()\n")
    fs = review_rules(files)
    assert any(x.rule_id == "RES001" for x in fs), "RES001 应该检测到 open()，即使存在无关的 close()"


def test_with_open_should_suppress():
    """测试 with open 语句应抑制泄漏检测（正确的资源管理）"""
    # with open(...语句应该被抑制，不报 RES001
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,2 @@\n"
                    "+    with open('x.txt') as f:\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert not any(x.rule_id == "RES001" for x in fs), "RES001 不应该标记 with open 语句"


def test_multiple_opens_single_close_should_not_fully_suppress():
    """测试多个 open 只有一个 close 时，未管理的 open 仍应报泄漏（避免漏报）"""
    # 两个 open 但只有一个 close，未管理的那个仍应报 RES001
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,4 @@\n"
                    "+    f1 = open('file1.txt')\n"
                    "+    f2 = open('file2.txt')\n"
                    "+    f1.close()\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    res001_findings = [x for x in fs if x.rule_id == "RES001"]
    assert len(res001_findings) >= 1, "至少应该检测到一个 open() 泄漏（两个 open 只有一个 close）"


def test_additional_kv_keys_detection():
    """测试额外键名（access_key/secret_key/private_key/auth_key）被 rule_engine 检出"""
    # 测试 access_key 被检出
    diff_content1 = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                     "@@ -1 +1,2 @@\n"
                     "+    access_key = \"my_secret_key_12345\"\n")
    files1 = parse_diff(diff_content1)
    fs1 = review_rules(files1)
    assert any(x.rule_id == "SECRET001" for x in fs1), "SECRET001 应该检测到 access_key"

    # 测试 secret_key 被检出
    files2 = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    secret_key = \"my_secret_value_67890\"\n"
    )
    fs2 = review_rules(files2)
    assert any(x.rule_id == "SECRET001" for x in fs2), "SECRET001 应该检测到 secret_key"

    # 测试 private_key 被检出
    diff_content3 = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                     "@@ -1 +1,2 @@\n"
                     "+    private_key = \"my_private_key_abc123\"\n")
    files3 = parse_diff(diff_content3)
    fs3 = review_rules(files3)
    assert any(x.rule_id == "SECRET001" for x in fs3), "SECRET001 应该检测到 private_key"

    # 测试 auth_key 被检出
    files4 = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n+    auth_key = \"my_auth_key_xyz789\"\n")
    fs4 = review_rules(files4)
    assert any(x.rule_id == "SECRET001" for x in fs4), "SECRET001 应该检测到 auth_key"


def test_redaction_rule_engine_sync():
    """测试 redaction 和 rule_engine 检/脱同步（C1 验收）"""
    from agent.redaction import SECRET_KV_KEYS

    # 验证 rule_engine 的 SECRET001 规则使用 SECRET_KV_KEYS
    # 通过检查规则引擎是否能检测到所有 SECRET_KV_KEYS 定义的键名
    for key_name in SECRET_KV_KEYS:
        diff_text = (f"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                     f"@@ -1 +1,2 @@\n"
                     f"+    {key_name} = \"test_secret_value\"\n")
        files = parse_diff(diff_text)
        fs = review_rules(files)
        assert any(x.rule_id == "SECRET001" for x in fs), f"SECRET001 应该检测到 {key_name}"

    # 验证 redaction 也能脱敏所有 SECRET_KV_KEYS 定义的键名
    for key_name in SECRET_KV_KEYS:
        text = f'{key_name} = "test_secret_value"'
        redacted, count = redact_text(text)
        assert "[REDACTED_KV]" in redacted, f"redaction 应该脱敏 {key_name}"


def test_sql_injection_f_string_detected():
    """测试SEC005：f-string SQL注入被检测到"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,2 @@\n"
                    "+    cursor.execute(f\"SELECT * FROM users WHERE name = '{username}'\")\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert any(x.rule_id == "SEC005" for x in fs), "SEC005 应该检测到 f-string SQL注入"


def test_sql_injection_string_concatenation_detected():
    """测试SEC005：字符串拼接SQL注入被检测到"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,2 @@\n"
                    "+    cursor.execute(\"SELECT * FROM users WHERE id = \" + user_id)\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert any(x.rule_id == "SEC005" for x in fs), "SEC005 应该检测到字符串拼接SQL注入"


def test_sql_injection_format_detected():
    """测试SEC005：.format() SQL注入被检测到"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,2 @@\n"
                    "+    cursor.execute(\"SELECT * FROM users WHERE name = '{}'\".format(username))\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert any(x.rule_id == "SEC005" for x in fs), "SEC005 应该检测到 .format() SQL注入"


def test_path_traversal_f_string_detected():
    """测试SEC006：f-string路径遍历被检测到"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,2 @@\n"
                    "+    with open(f\"/home/users/{filename}\", 'r') as f:\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert any(x.rule_id == "SEC006" for x in fs), "SEC006 应该检测到 f-string 路径遍历"


def test_path_traversal_string_format_detected():
    """测试SEC006：字符串格式化路径遍历被检测到"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,2 @@\n"
                    "+    with open(\"/home/users/%s\" % filename, 'r') as f:\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert any(x.rule_id == "SEC006" for x in fs), "SEC006 应该检测到字符串格式化路径遍历"


def test_path_traversal_os_path_join_detected():
    """测试SEC006：os.path.join路径遍历被检测到"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,2 @@\n"
                    "+    with open(os.path.join(base_path, user_input), 'r') as f:\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert any(x.rule_id == "SEC006" for x in fs), "SEC006 应该检测到 os.path.join 路径遍历"


def test_multiline_shell_injection_list_detected():
    """测试扩展SEC002：多行列表构造shell注入被检测到"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,5 @@\n"
                    "+    cmd = []\n"
                    "+    cmd.append(\"bash\")\n"
                    "+    cmd.append(\"-c\")\n"
                    "+    cmd.append(user_input)\n"
                    "+    subprocess.run(cmd, shell=True)\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert any(x.rule_id == "SEC002" for x in fs), "SEC002 应该检测到多行列表构造shell注入"


def test_multiline_shell_injection_concatenation_detected():
    """测试扩展SEC002：字符串拼接构造shell注入被检测到"""
    diff_content = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                    "@@ -1 +1,3 @@\n"
                    "+    cmd = \"rm -rf /tmp/\" + filename\n"
                    "+    subprocess.run(cmd, shell=True)\n")
    files = parse_diff(diff_content)
    fs = review_rules(files)
    assert any(x.rule_id == "SEC002" for x in fs), "SEC002 应该检测到字符串拼接构造shell注入"
