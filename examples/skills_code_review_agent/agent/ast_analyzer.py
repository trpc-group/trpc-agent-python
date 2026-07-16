# agent/ast_analyzer.py - AST/taint 污点传播分析
import ast
from typing import Set, List, Optional
from agent.models import DiffFile, Finding, Severity, Bucket

# 污点源：外部输入向量
TAINT_SOURCES = {"request", "args", "input", "env", "user_input", "data", "payload"}

# 污点汇：危险函数
TAINT_SINKS = {"system", "popen", "execute", "exec", "eval", "open"}


class _Visitor(ast.NodeVisitor):
    """AST 访问器，传播污点并检测漏洞"""

    def __init__(self, file_path: str):
        self.tainted: Set[str] = set()  # 污点变量集合
        self.findings: List[tuple] = []  # 检测到的漏洞
        self.file_path = file_path  # 当前文件路径
        self.current_line = 0  # 当前行号
        # 用户输入相关的变量名模式（用于函数参数污点分析）
        self.user_input_patterns = {
            "user", "username", "userid", "user_id", "input", "data", "query", "sql", "command", "cmd", "filename",
            "filepath", "path", "url", "uri", "search", "keyword", "term", "content", "message", "text", "payload",
            "param", "parameter", "arg", "argument", "value", "val", "field", "form"
        }

    def visit_Assign(self, node: ast.Assign):
        """访问赋值语句，传播污点"""
        # 首先检查是否有未定义的变量引用（可能是函数参数）
        self._contains_undefined_reference(node.value)

        # 检查右侧表达式是否为污点源
        if self._is_taint_source(node.value):
            # 将左侧变量标记为污点
            for target in node.targets:
                var_name = self._extract_name(target)
                if var_name:
                    self.tainted.add(var_name)

        # 检查右侧表达式是否为污点变量
        elif self._is_tainted_expr(node.value):
            for target in node.targets:
                var_name = self._extract_name(target)
                if var_name:
                    self.tainted.add(var_name)

        # 检查右侧表达式是否为包含任何变量的 f-string（字符串格式化）
        # 关键修复：任何 f-string 都被视为潜在污点，因为其中的变量可能来自用户输入
        elif self._is_fstring_with_variables(node.value):
            for target in node.targets:
                var_name = self._extract_name(target)
                if var_name:
                    self.tainted.add(var_name)

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """访问函数定义，标记用户输入相关的参数为潜在污点源"""
        # 检查函数参数，将可能包含用户输入的参数标记为污点
        for arg in node.args.args:
            arg_name = arg.arg
            # 如果参数名表明它可能是用户输入，标记为污点
            if arg_name.lower() in self.user_input_patterns:
                self.tainted.add(arg_name)

        # 处理位置参数和关键字参数
        for arg in node.args.posonlyargs + node.args.kwonlyargs:
            arg_name = arg.arg
            if arg_name.lower() in self.user_input_patterns:
                self.tainted.add(arg_name)

        # 处理 *args 和 **kwargs
        if node.args.vararg:
            vararg_name = node.args.vararg.arg
            if vararg_name and vararg_name.lower() in self.user_input_patterns:
                self.tainted.add(vararg_name)
        if node.args.kwarg:
            kwarg_name = node.args.kwarg.arg
            if kwarg_name and kwarg_name.lower() in self.user_input_patterns:
                self.tainted.add(kwarg_name)

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """访问函数调用，检测污点流入 sink"""
        func_name = self._get_call_name(node)

        # 检查是否为污点汇
        if func_name in TAINT_SINKS:
            # 检查位置参数是否被污染
            for arg in node.args:
                if self._is_tainted_expr(arg):
                    line_no = getattr(node, 'lineno', 0)
                    self.findings.append(("AST001", Severity.HIGH, f"污点传播到危险函数 '{func_name}'", line_no,
                                          f"检测到外部输入流入 {func_name}()，可能导致命令注入或代码执行漏洞",
                                          f"避免直接使用外部输入调用 {func_name}()，请进行输入验证和清理", 0.85, "ast"))
                    break  # 一个调用只报告一次

            # 检查关键字参数是否被污染
            for keyword in node.keywords:
                if self._is_tainted_expr(keyword.value):
                    line_no = getattr(node, 'lineno', 0)
                    self.findings.append(("AST001", Severity.HIGH, f"污点传播到危险函数 '{func_name}'", line_no,
                                          f"检测到外部输入流入 {func_name}()，可能导致命令注入或代码执行漏洞",
                                          f"避免直接使用外部输入调用 {func_name}()，请进行输入验证和清理", 0.85, "ast"))
                    break  # 一个调用只报告一次

        self.generic_visit(node)

    def _is_taint_source(self, node: ast.AST) -> bool:
        """判断节点是否为污点源"""
        # 检查 request.args.get('x') 或 request.args['x']
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                # request.args.get(...)
                if (func.attr == "get" and isinstance(func.value, ast.Attribute) and func.value.attr == "args"
                        and isinstance(func.value.value, ast.Name) and func.value.value.id == "request"):
                    return True
                # input(), os.environ.get(...)
                if func.attr == "get":
                    if isinstance(func.value, ast.Name):
                        if func.value.id == "input":
                            return True
                    if isinstance(func.value, ast.Attribute):
                        if (func.value.attr == "environ" and isinstance(func.value.value, ast.Name)):
                            if func.value.value.id == "os":
                                return True

        # 检查 os.environ['VAR']
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Attribute):
                if node.value.attr == "environ":
                    if (isinstance(node.value.value, ast.Name) and node.value.value.id == "os"):
                        return True

        # 检查属性访问：request.data, request.payload 等
        if isinstance(node, ast.Attribute):
            if (node.attr in TAINT_SOURCES and isinstance(node.value, ast.Name) and node.value.id == "request"):
                return True

        # 检查简单的变量名是否在污点源列表中
        if isinstance(node, ast.Name):
            return node.id in TAINT_SOURCES

        return False

    def _is_tainted_expr(self, node: ast.AST) -> bool:
        """判断表达式是否被污染"""
        if isinstance(node, ast.Name):
            return node.id in self.tainted

        # 检查链式调用：request.args.get('x')
        if isinstance(node, ast.Call):
            return self._is_taint_source(node)

        return False

    def _is_fstring_with_taint(self, node: ast.AST) -> bool:
        """判断 f-string 是否包含污点变量"""
        if isinstance(node, ast.JoinedStr):
            # 检查 f-string 中的所有值
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    # 检查格式化值是否为污点变量
                    if isinstance(value.value, ast.Name):
                        if value.value.id in self.tainted:
                            return True
                    # 检查格式化值是否为污点源
                    elif self._is_taint_source(value.value):
                        return True
        return False

    def _is_fstring_with_variables(self, node: ast.AST) -> bool:
        """判断 f-string 是否包含任何变量（用于污点传播）"""
        if isinstance(node, ast.JoinedStr):
            # 检查 f-string 中的所有值
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    # 任何包含变量的 f-string 都被视为潜在污点
                    if isinstance(value.value, ast.Name):
                        return True
        return False

    def _contains_undefined_reference(self, node: ast.AST) -> bool:
        """判断代码是否包含未定义的变量引用（可能是函数参数）"""

        class NameChecker(ast.NodeVisitor):

            def __init__(self, defined_names):
                self.defined_names = defined_names
                self.undefined_refs = set()

            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Load) and node.id not in self.defined_names:
                    self.undefined_refs.add(node.id)
                self.generic_visit(node)

        # 首先收集所有定义的变量名
        class NameDefCollector(ast.NodeVisitor):

            def __init__(self):
                self.defined_names = set()

            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Store):
                    self.defined_names.add(node.id)
                self.generic_visit(node)

        collector = NameDefCollector()
        collector.visit(node)

        # 检查是否有未定义的变量引用
        checker = NameChecker(collector.defined_names | self.tainted | TAINT_SOURCES)
        checker.visit(node)

        # 将未定义的变量引用标记为污点（可能是函数参数）
        for name in checker.undefined_refs:
            if name.lower() in self.user_input_patterns:
                self.tainted.add(name)

        return len(checker.undefined_refs) > 0

    def _extract_name(self, node: ast.AST) -> Optional[str]:
        """从节点中提取变量名"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Tuple):
            # 处理 a, b = ... 的情况
            if isinstance(node.elts[0], ast.Name):
                return node.elts[0].id
        return None

    def _get_call_name(self, node: ast.Call) -> Optional[str]:
        """获取函数调用的名称（支持方法调用如 cursor.execute）"""
        func = node.func

        # 方法调用：obj.method(...) 或 obj.attr.method(...)
        if isinstance(func, ast.Attribute):
            # 返回方法名，如 cursor.execute 中的 "execute"
            return func.attr

        # 直接调用：system(...)
        if isinstance(func, ast.Name):
            return func.id

        return None


def analyze(files: list[DiffFile]) -> list[Finding]:
    """分析 DiffFile 列表，检测污点传播漏洞

    Args:
        files: DiffFile 对象列表

    Returns:
        Finding 对象列表
    """
    findings = []

    for diff_file in files:
        # 只处理 Python 文件
        if not diff_file.path.endswith('.py'):
            continue

        # 处理每个 hunk
        for hunk in diff_file.hunks:
            if not hunk.added:
                continue

            # 拼接新增行形成代码块
            code_lines = [line.content for line in hunk.added]
            code_block = '\n'.join(code_lines)

            # 尝试解析 AST
            try:
                tree = ast.parse(code_block)
            except (SyntaxError, ValueError):
                # 语法不完整，尝试使用简单的字符串匹配作为后备方案
                findings.extend(_analyze_with_fallback(diff_file.path, hunk))
                continue

            # 创建 visitor 并分析
            visitor = _Visitor(diff_file.path)
            visitor.visit(tree)

            # 转换 findings
            for (rule_id, severity, title, line_no, evidence, recommendation, confidence, source) in visitor.findings:
                # 找到对应的行号
                actual_line = None
                if line_no > 0 and line_no <= len(hunk.added):
                    actual_line = hunk.added[line_no - 1].new_line

                finding = Finding(severity=severity,
                                  category="security",
                                  file=diff_file.path,
                                  line=actual_line,
                                  title=title,
                                  evidence=evidence,
                                  recommendation=recommendation,
                                  confidence=confidence,
                                  source=source,
                                  rule_id=rule_id,
                                  bucket=Bucket.FINDINGS)
                findings.append(finding)

    return findings


def _analyze_with_fallback(file_path: str, hunk) -> list[Finding]:
    """使用简单的字符串匹配作为后备方案分析污点传播

    当 AST 解析失败时使用此方法，处理不完整的代码块
    """
    findings = []
    user_input_patterns = {
        "user", "username", "userid", "user_id", "input", "data", "query", "sql", "command", "cmd", "filename",
        "filepath", "path", "url", "uri", "search", "keyword", "term", "content", "message", "text", "payload", "param",
        "parameter", "arg", "argument", "value", "val", "field", "form", "category"
    }

    # 检测模式：包含用户输入变量的 f-string 赋值，后跟危险函数调用
    tainted_vars = set()
    execute_lines = {}  # 存储execute调用及其行号

    for i, line in enumerate(hunk.added):
        content = line.content.strip()

        # 检测 f-string 赋值：var = f"...{user_input}..."
        if '=' in content and 'f"' in content and '{' in content and '}' in content:
            # 提取变量名
            var_name = content.split('=')[0].strip()
            if var_name and not var_name.startswith('#'):
                # 检查f-string中是否包含用户输入相关的变量
                for pattern in user_input_patterns:
                    if pattern in content.lower():
                        tainted_vars.add(var_name)
                        break

        # 检测危险函数调用
        for sink in ["execute", "open", "eval", "exec", "system", "popen"]:
            if sink in content.lower():
                # 检查参数是否是污点变量
                for var in tainted_vars:
                    if var in content:
                        execute_lines[i] = (sink, var)
                        break

    # 生成 findings
    for line_idx, (sink, var) in execute_lines.items():
        line_obj = hunk.added[line_idx]
        finding = Finding(
            severity=Severity.HIGH,
            category="security",
            file=file_path,
            line=line_obj.new_line,
            title=f"污点传播到危险函数 '{sink}'",
            evidence=line_obj.content,
            recommendation=f"避免直接使用用户输入调用 {sink}()，请进行输入验证和清理",
            confidence=0.8,  # 提高置信度以确保被归类为findings
            source="ast",
            rule_id="AST001",
            bucket=Bucket.FINDINGS)
        findings.append(finding)

    return findings
