# tests/test_ast_analyzer.py - AST/taint 分析器测试
import pytest
from agent.models import DiffFile, Hunk, ChangedLine, Severity, Bucket
from agent.ast_analyzer import analyze


def test_taint_to_os_system():
    """测试污点传播到 os.system - 应检测到漏洞"""
    # 构造包含污点传播的代码
    code_lines = ["from flask import request", "cmd = request.args.get('command')", "os.system(cmd)"]

    # 构建 DiffFile 和 Hunk
    changed_lines = [ChangedLine(file="test.py", new_line=1, old_line=None, content=line) for line in code_lines]

    hunk = Hunk(file="test.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="test.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证检测结果
    assert len(findings) > 0, "应该检测到污点传播漏洞"

    finding = findings[0]
    assert finding.source == "ast"
    assert finding.category == "security"
    assert "AST001" in finding.rule_id
    assert finding.severity == Severity.HIGH
    assert finding.bucket == Bucket.FINDINGS
    assert finding.confidence > 0.7


def test_literal_no_finding():
    """测试字面量不产生误报 - os.system('ls') 不应报漏洞"""
    code_lines = ["# safe code", "os.system('ls')"]

    changed_lines = [ChangedLine(file="safe.py", new_line=1, old_line=None, content=line) for line in code_lines]

    hunk = Hunk(file="safe.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="safe.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证没有误报
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) == 0, "字面量调用不应产生污点传播漏洞报告"


def test_incomplete_syntax_no_crash():
    """测试 ast.parse 失败时不崩溃 - 语法不完整的代码应跳过"""
    code_lines = ["def incomplete_function(", "    # 缺少函数体", "    os.system(request.args['x'])"]

    changed_lines = [ChangedLine(file="incomplete.py", new_line=1, old_line=None, content=line) for line in code_lines]

    hunk = Hunk(file="incomplete.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="incomplete.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析 - 不应抛出异常
    try:
        findings = analyze([diff_file])
        # 验证返回了列表（可能为空）
        assert isinstance(findings, list)
    except Exception as e:
        pytest.fail(f"语法不完整代码不应抛出异常: {e}")


def test_non_python_files_skipped():
    """测试非 .py 文件被跳过"""
    code_lines = ["some javascript code", "os.system(request.args['x'])"]

    changed_lines = [ChangedLine(file="script.js", new_line=1, old_line=None, content=line) for line in code_lines]

    hunk = Hunk(file="script.js", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="script.js", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证跳过了非 Python 文件
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) == 0, "非 .py 文件应被跳过"


def test_multiple_taint_sources():
    """测试多个污点源检测"""
    code_lines = [
        "user_input = request.args.get('data')", "env_var = os.environ.get('CMD')", "os.system(user_input)",
        "os.popen(env_var)"
    ]

    changed_lines = [
        ChangedLine(file="multi.py", new_line=i + 1, old_line=None, content=line) for i, line in enumerate(code_lines)
    ]

    hunk = Hunk(file="multi.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="multi.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证检测到多个漏洞
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) >= 2, "应该检测到多个污点传播漏洞"


def test_empty_input():
    """测试空输入不崩溃"""
    findings = analyze([])
    assert isinstance(findings, list)
    assert len(findings) == 0


def test_indirect_taint_propagation():
    """测试间接污点传播"""
    code_lines = [
        "user_input = request.args.get('cmd')", "command = user_input", "executable = command", "os.system(executable)"
    ]

    changed_lines = [
        ChangedLine(file="indirect.py", new_line=i + 1, old_line=None, content=line)
        for i, line in enumerate(code_lines)
    ]

    hunk = Hunk(file="indirect.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="indirect.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证检测到间接污点传播
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) > 0, "应该检测到间接污点传播漏洞"


def test_different_sink_types():
    """测试不同类型的 sink"""
    code_lines = [
        "user_input = request.args.get('x')", "os.system(user_input)", "os.popen(user_input)", "eval(user_input)",
        "exec(user_input)"
    ]

    changed_lines = [
        ChangedLine(file="sinks.py", new_line=i + 1, old_line=None, content=line) for i, line in enumerate(code_lines)
    ]

    hunk = Hunk(file="sinks.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="sinks.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证检测到多个不同类型的 sink
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) >= 3, "应该检测到多个不同类型的 sink 漏洞"


def test_taint_from_env():
    """测试环境变量作为污点源"""
    code_lines = ["cmd = os.environ.get('USER_CMD')", "os.system(cmd)"]

    changed_lines = [
        ChangedLine(file="env_test.py", new_line=i + 1, old_line=None, content=line)
        for i, line in enumerate(code_lines)
    ]

    hunk = Hunk(file="env_test.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="env_test.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证检测到环境变量污点
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) > 0, "应该检测到环境变量污点传播漏洞"


def test_taint_from_data_payload():
    """测试 data 和 payload 作为污点源"""
    code_lines = ["data = request.data", "payload = request.payload", "os.system(data)", "os.popen(payload)"]

    changed_lines = [
        ChangedLine(file="data_test.py", new_line=i + 1, old_line=None, content=line)
        for i, line in enumerate(code_lines)
    ]

    hunk = Hunk(file="data_test.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="data_test.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证检测到 data/payload 污点
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) >= 2, "应该检测到 data 和 payload 污点传播漏洞"


def test_kwargs_tainted_value():
    """测试关键字参数传递污点值"""
    code_lines = ["cmd = request.args.get('command')", "os.system(command=cmd)"]

    changed_lines = [
        ChangedLine(file="kwargs_test.py", new_line=i + 1, old_line=None, content=line)
        for i, line in enumerate(code_lines)
    ]

    hunk = Hunk(file="kwargs_test.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="kwargs_test.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证检测到 kwargs 传递污点
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) > 0, "应该检测到关键字参数传递污点值"


def test_multiline_sql_injection():
    """测试多行 SQL 注入构造 - query=f"...{user}"; cursor.execute(query)"""
    code_lines = [
        "user = request.args.get('user')", "query = f\"SELECT * FROM users WHERE name = '{user}'\"",
        "cursor.execute(query)"
    ]

    changed_lines = [
        ChangedLine(file="sql_test.py", new_line=i + 1, old_line=None, content=line)
        for i, line in enumerate(code_lines)
    ]

    hunk = Hunk(file="sql_test.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="sql_test.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证检测到多行 SQL 注入
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) > 0, "应该检测到多行 SQL 注入构造（f-string + execute）"
    finding = ast_findings[0]
    assert "execute" in finding.title.lower() or "危险函数" in finding.title, "应该识别 execute 为危险函数"


def test_multiline_path_traversal():
    """测试多行路径遍历构造 - path=f"/{user_input}"; open(path)"""
    code_lines = ["user_input = request.args.get('file')", "path = f\"/var/www/{user_input}\"", "f = open(path, 'r')"]

    changed_lines = [
        ChangedLine(file="path_test.py", new_line=i + 1, old_line=None, content=line)
        for i, line in enumerate(code_lines)
    ]

    hunk = Hunk(file="path_test.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="path_test.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证检测到多行路径遍历
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) > 0, "应该检测到多行路径遍历构造（f-string + open）"
    finding = ast_findings[0]
    assert "open" in finding.title.lower() or "危险函数" in finding.title, "应该识别 open 为危险函数"


def test_safe_literal_execute():
    """测试字面量 execute 不应报漏洞 - cursor.execute('SELECT * FROM users')"""
    code_lines = ["query = 'SELECT * FROM users'", "cursor.execute(query)"]

    changed_lines = [
        ChangedLine(file="safe_execute.py", new_line=i + 1, old_line=None, content=line)
        for i, line in enumerate(code_lines)
    ]

    hunk = Hunk(file="safe_execute.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="safe_execute.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证没有误报
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) == 0, "字面量 execute 不应产生污点传播漏洞报告"


def test_safe_literal_open():
    """测试字面量 open 不应报漏洞 - open('/etc/passwd', 'r')"""
    code_lines = ["f = open('/etc/passwd', 'r')"]

    changed_lines = [ChangedLine(file="safe_open.py", new_line=1, old_line=None, content=line) for line in code_lines]

    hunk = Hunk(file="safe_open.py", old_start=1, new_start=1, added=changed_lines, context_after=code_lines)

    diff_file = DiffFile(path="safe_open.py", status="modified", hunks=[hunk], added_lines=changed_lines)

    # 执行分析
    findings = analyze([diff_file])

    # 验证没有误报
    ast_findings = [f for f in findings if f.source == "ast"]
    assert len(ast_findings) == 0, "字面量 open 不应产生污点传播漏洞报告"
