"""Unit tests for Python AST scanner utilities."""

import ast

import pytest

from trpc_agent_sdk.tools.safety.scanner.python_scanner import (
    extract_calls,
    extract_imports,
    find_function_calls,
    find_string_assignments,
    get_call_name,
    get_string_args,
    get_string_value,
    safe_parse,
)


class TestSafeParse:
    """Test safe_parse function."""

    def test_valid_python(self):
        tree = safe_parse("x = 1 + 2")
        assert tree is not None
        assert isinstance(tree, ast.Module)

    def test_multiline(self):
        code = "import os\nos.system('ls')"
        tree = safe_parse(code)
        assert tree is not None

    def test_syntax_error_returns_none(self):
        tree = safe_parse("def foo(")
        assert tree is None

    def test_empty_string(self):
        tree = safe_parse("")
        assert tree is not None  # Empty module is valid

    def test_binary_garbage_returns_none(self):
        tree = safe_parse("\x00\x01\x02\xff")
        assert tree is None


class TestExtractCalls:
    """Test extract_calls function."""

    def test_simple_call(self):
        tree = safe_parse("print('hello')")
        calls = extract_calls(tree)
        assert len(calls) == 1

    def test_multiple_calls(self):
        tree = safe_parse("a = foo()\nb = bar()\nc = baz()")
        calls = extract_calls(tree)
        assert len(calls) == 3

    def test_nested_calls(self):
        tree = safe_parse("print(len(str(42)))")
        calls = extract_calls(tree)
        assert len(calls) == 3  # print, len, str

    def test_no_calls(self):
        tree = safe_parse("x = 1\ny = 2")
        calls = extract_calls(tree)
        assert len(calls) == 0


class TestExtractImports:
    """Test extract_imports function."""

    def test_import(self):
        tree = safe_parse("import os")
        imports = extract_imports(tree)
        assert ("os", None) in imports

    def test_import_as(self):
        tree = safe_parse("import numpy as np")
        imports = extract_imports(tree)
        assert ("numpy", "np") in imports

    def test_from_import(self):
        tree = safe_parse("from os import path")
        imports = extract_imports(tree)
        assert ("os.path", None) in imports

    def test_from_import_as(self):
        tree = safe_parse("from os.path import join as j")
        imports = extract_imports(tree)
        assert ("os.path.join", "j") in imports

    def test_multiple_imports(self):
        code = "import os\nimport sys\nfrom pathlib import Path"
        tree = safe_parse(code)
        imports = extract_imports(tree)
        assert len(imports) == 3


class TestGetCallName:
    """Test get_call_name function."""

    def test_simple_function(self):
        tree = safe_parse("open('file.txt')")
        calls = extract_calls(tree)
        assert get_call_name(calls[0]) == "open"

    def test_module_function(self):
        tree = safe_parse("os.system('ls')")
        calls = extract_calls(tree)
        assert get_call_name(calls[0]) == "os.system"

    def test_deep_attribute(self):
        tree = safe_parse("subprocess.run(['ls'])")
        calls = extract_calls(tree)
        assert get_call_name(calls[0]) == "subprocess.run"

    def test_chained_attribute(self):
        tree = safe_parse("a.b.c.d()")
        calls = extract_calls(tree)
        assert get_call_name(calls[0]) == "a.b.c.d"

    def test_complex_call_returns_empty(self):
        # func[0]() — subscript call, not resolvable
        tree = safe_parse("funcs[0]()")
        calls = extract_calls(tree)
        assert get_call_name(calls[0]) == ""


class TestGetStringArgs:
    """Test get_string_args function."""

    def test_string_positional_arg(self):
        tree = safe_parse("open('/etc/passwd')")
        calls = extract_calls(tree)
        args = get_string_args(calls[0])
        assert "/etc/passwd" in args

    def test_multiple_string_args(self):
        tree = safe_parse("foo('a', 'b', 'c')")
        calls = extract_calls(tree)
        args = get_string_args(calls[0])
        assert args == ["a", "b", "c"]

    def test_keyword_string_arg(self):
        tree = safe_parse("requests.get(url='http://evil.com')")
        calls = extract_calls(tree)
        args = get_string_args(calls[0])
        assert "http://evil.com" in args

    def test_non_string_args_skipped(self):
        tree = safe_parse("foo(42, x, 'only_this')")
        calls = extract_calls(tree)
        args = get_string_args(calls[0])
        assert args == ["only_this"]

    def test_no_args(self):
        tree = safe_parse("foo()")
        calls = extract_calls(tree)
        args = get_string_args(calls[0])
        assert args == []


class TestGetStringValue:
    """Test get_string_value function."""

    def test_string_constant(self):
        tree = safe_parse("x = 'hello'")
        assign = tree.body[0]
        result = get_string_value(assign.value)
        assert result == "hello"

    def test_int_constant_returns_none(self):
        tree = safe_parse("x = 42")
        assign = tree.body[0]
        result = get_string_value(assign.value)
        assert result is None

    def test_name_returns_none(self):
        tree = safe_parse("x = y")
        assign = tree.body[0]
        result = get_string_value(assign.value)
        assert result is None


class TestFindFunctionCalls:
    """Test find_function_calls function."""

    def test_find_os_system(self):
        tree = safe_parse("import os\nos.system('rm -rf /')")
        matches = find_function_calls(tree, {"os.system"})
        assert len(matches) == 1

    def test_find_multiple(self):
        code = "subprocess.run(['ls'])\nos.system('pwd')\nprint('hi')"
        tree = safe_parse(code)
        matches = find_function_calls(tree, {"subprocess.run", "os.system"})
        assert len(matches) == 2

    def test_no_match(self):
        tree = safe_parse("print('safe')")
        matches = find_function_calls(tree, {"os.system", "subprocess.run"})
        assert len(matches) == 0


class TestFindStringAssignments:
    """Test find_string_assignments function."""

    def test_simple_assignment(self):
        tree = safe_parse("path = '/etc/passwd'")
        assignments = find_string_assignments(tree)
        assert assignments == {"path": "/etc/passwd"}

    def test_multiple_assignments(self):
        code = "url = 'http://evil.com'\nkey = 'sk-secret123'"
        tree = safe_parse(code)
        assignments = find_string_assignments(tree)
        assert assignments["url"] == "http://evil.com"
        assert assignments["key"] == "sk-secret123"

    def test_non_string_ignored(self):
        code = "x = 42\ny = 'hello'\nz = [1, 2]"
        tree = safe_parse(code)
        assignments = find_string_assignments(tree)
        assert "x" not in assignments
        assert "z" not in assignments
        assert assignments["y"] == "hello"
