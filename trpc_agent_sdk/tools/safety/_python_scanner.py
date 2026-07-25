# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Python AST rules for the tool script safety guard."""

from __future__ import annotations

import ast
import re
from typing import Iterable

from ._models import Decision
from ._models import RiskLevel
from ._models import SafetyFinding
from ._policy import ToolSafetyPolicy
from ._rule_utils import SECRET_LITERAL_RE
from ._rule_utils import SECRET_NAME_RE
from ._rule_utils import URL_RE
from ._rule_utils import add_finding
from ._rule_utils import add_line_finding
from ._rule_utils import call_name
from ._rule_utils import domain_allowed
from ._rule_utils import path_is_denied
from ._rule_utils import string_value

_INSTALL_RE = re.compile(r"(?i)(?:pip3?|python\d*(?:\.\d+)?\s+-m\s+pip|npm|yarn|pnpm|apt(?:-get)?|apk|yum|dnf|brew)"
                         r"[\s\"',]+(?:install|add)\b")
_SHELL_META_RE = re.compile(r"(?:;|&&|\|\||`|\$\()")
_NETWORK_CALLS = {
    "aiohttp.request",
    "httpx.delete",
    "httpx.get",
    "httpx.patch",
    "httpx.post",
    "httpx.put",
    "requests.delete",
    "requests.get",
    "requests.patch",
    "requests.post",
    "requests.put",
    "requests.request",
    "socket.create_connection",
    "urllib.request.urlopen",
    "urlopen",
}
_PROCESS_CALLS = {
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.popen",
    "os.spawnl",
    "os.spawnlp",
    "os.system",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
}
_FILE_CALL_NAMES = {
    "open",
    "Path",
    "pathlib.Path",
}
_FILE_METHODS = {
    "open",
    "read_bytes",
    "read_text",
    "rmdir",
    "touch",
    "unlink",
    "write_bytes",
    "write_text",
}


def scan_python(
    script: str,
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> list[SafetyFinding]:
    """Scan Python source with AST-aware rules."""
    findings: list[SafetyFinding] = []
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        add_finding(
            findings,
            policy,
            category="parser_uncertainty",
            rule_id="PYTHON-001",
            title="Python source could not be parsed",
            risk_level=RiskLevel.MEDIUM,
            decision=Decision.NEEDS_HUMAN_REVIEW,
            evidence=f"line {exc.lineno or 0}: syntax error",
            recommendation="Fix the syntax or manually review code that cannot be statically parsed.",
            line_number=exc.lineno,
        )
        return findings

    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    tainted_names = _collect_tainted_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if path_is_denied(node.value, policy) and _is_file_context(node, parents):
                add_line_finding(
                    findings,
                    policy,
                    script,
                    node.lineno,
                    secrets,
                    category="dangerous_file_operation",
                    rule_id="FILE-002",
                    title="Script accesses a path protected by policy",
                    risk_level=RiskLevel.HIGH,
                    decision=Decision.DENY,
                    recommendation="Use a scoped workspace and inject only files required by the tool.",
                )
            continue

        if isinstance(node, ast.While) and _is_constant_true(node.test):
            add_line_finding(
                findings,
                policy,
                script,
                node.lineno,
                secrets,
                category="resource_abuse",
                rule_id="RESOURCE-001",
                title="Unbounded loop detected",
                risk_level=RiskLevel.HIGH,
                decision=Decision.DENY,
                recommendation="Add a bounded condition, cancellation check, and runtime timeout.",
            )
            continue

        if not isinstance(node, ast.Call):
            continue
        name = call_name(node.func)
        _scan_file_call(script, node, name, findings, policy, secrets)
        _scan_network_call(script, node, name, findings, policy, secrets)
        _scan_process_call(script, node, name, findings, policy, secrets)
        _scan_resource_call(script, node, name, findings, policy, secrets)
        _scan_secret_output(node, name, tainted_names, findings, policy)

    return findings


def _scan_file_call(
    script: str,
    node: ast.Call,
    name: str,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    if name == "shutil.rmtree":
        add_line_finding(
            findings,
            policy,
            script,
            node.lineno,
            secrets,
            category="dangerous_file_operation",
            rule_id="FILE-001",
            title="Recursive file deletion detected",
            risk_level=RiskLevel.CRITICAL,
            decision=Decision.DENY,
            recommendation="Replace recursive deletion with reviewed, workspace-scoped cleanup.",
        )
    elif name in {"os.remove", "os.unlink"} or name.endswith(".unlink"):
        add_line_finding(
            findings,
            policy,
            script,
            node.lineno,
            secrets,
            category="dangerous_file_operation",
            rule_id="FILE-003",
            title="File deletion requires review",
            risk_level=RiskLevel.MEDIUM,
            decision=Decision.NEEDS_HUMAN_REVIEW,
            recommendation="Constrain deletion to a temporary workspace and validate the resolved path.",
        )


def _scan_network_call(
    script: str,
    node: ast.Call,
    name: str,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    urls = [
        value for child in list(node.args) + [keyword.value for keyword in node.keywords]
        for value in [string_value(child)] if value and URL_RE.match(value)
    ]
    method_name = name.rsplit(".", 1)[-1]
    is_network = (name in _NETWORK_CALLS or method_name in {"connect", "create_connection"}
                  or bool(urls) and method_name in {"delete", "get", "patch", "post", "put", "request", "urlopen"})
    if not is_network:
        return
    if urls:
        for url in urls:
            if domain_allowed(url, policy):
                continue
            add_line_finding(
                findings,
                policy,
                script,
                node.lineno,
                secrets,
                category="network_egress",
                rule_id="NETWORK-001",
                title="Network target is not allowlisted",
                risk_level=RiskLevel.HIGH,
                decision=Decision.DENY,
                recommendation="Add the reviewed hostname to allowed_domains or remove the outbound request.",
            )
        return

    static_strings = [
        child.value for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]
    if name.endswith(("connect", "create_connection")) and static_strings:
        host = static_strings[0]
        if domain_allowed(f"https://{host}", policy):
            return
        add_line_finding(
            findings,
            policy,
            script,
            node.lineno,
            secrets,
            category="network_egress",
            rule_id="NETWORK-001",
            title="Network target is not allowlisted",
            risk_level=RiskLevel.HIGH,
            decision=Decision.DENY,
            recommendation="Add the reviewed hostname to allowed_domains or remove the outbound request.",
        )
    else:
        add_line_finding(
            findings,
            policy,
            script,
            node.lineno,
            secrets,
            category="network_egress",
            rule_id="NETWORK-002",
            title="Dynamic network target cannot be verified",
            risk_level=RiskLevel.MEDIUM,
            decision=Decision.NEEDS_HUMAN_REVIEW,
            recommendation="Use a literal allowlisted URL or validate the resolved hostname before connecting.",
        )


def _scan_process_call(
    script: str,
    node: ast.Call,
    name: str,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    if name in {"eval", "exec"}:
        add_line_finding(
            findings,
            policy,
            script,
            node.lineno,
            secrets,
            category="process_execution",
            rule_id="PROCESS-002",
            title="Dynamic code execution detected",
            risk_level=RiskLevel.HIGH,
            decision=Decision.DENY,
            recommendation="Replace dynamic evaluation with a fixed, validated operation.",
        )
        return
    if name not in _PROCESS_CALLS:
        return

    add_line_finding(
        findings,
        policy,
        script,
        node.lineno,
        secrets,
        category="process_execution",
        rule_id="PROCESS-001",
        title="Child process invocation detected",
        risk_level=RiskLevel.MEDIUM,
        decision=Decision.NEEDS_HUMAN_REVIEW,
        recommendation="Use an argument-list API, an allowed executable, and sandbox resource limits.",
    )
    source = ast.get_source_segment(script, node) or name
    shell_enabled = name in {"os.system", "os.popen", "asyncio.create_subprocess_shell"}
    shell_enabled = shell_enabled or any(
        keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
        for keyword in node.keywords)
    first_argument = node.args[0] if node.args else None
    dynamic_command = first_argument is not None and string_value(first_argument) is None
    static_command = string_value(first_argument) if first_argument is not None else None
    if shell_enabled and (dynamic_command or static_command and _SHELL_META_RE.search(static_command)):
        add_line_finding(
            findings,
            policy,
            script,
            node.lineno,
            secrets,
            category="process_execution",
            rule_id="PROCESS-002",
            title="Shell injection path detected",
            risk_level=RiskLevel.HIGH,
            decision=Decision.DENY,
            recommendation="Disable shell execution and pass validated arguments as a list.",
        )
    if _INSTALL_RE.search(source):
        add_line_finding(
            findings,
            policy,
            script,
            node.lineno,
            secrets,
            category="dependency_install",
            rule_id="DEPENDENCY-001",
            title="Runtime dependency installation detected",
            risk_level=RiskLevel.HIGH,
            decision=Decision.DENY,
            recommendation="Build reviewed dependencies into an immutable execution image.",
        )


def _scan_resource_call(
    script: str,
    node: ast.Call,
    name: str,
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    secrets: Iterable[str],
) -> None:
    if name in {"time.sleep", "asyncio.sleep"} and node.args:
        duration = _number_value(node.args[0])
        if duration is not None and duration > policy.max_sleep_seconds:
            add_line_finding(
                findings,
                policy,
                script,
                node.lineno,
                secrets,
                category="resource_abuse",
                rule_id="RESOURCE-002",
                title="Long sleep exceeds policy",
                risk_level=RiskLevel.MEDIUM,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                recommendation="Use a shorter delay or an externally cancellable scheduler.",
            )
    if name in {"os.fork", "os.forkpty"}:
        add_line_finding(
            findings,
            policy,
            script,
            node.lineno,
            secrets,
            category="resource_abuse",
            rule_id="RESOURCE-003",
            title="Process forking detected",
            risk_level=RiskLevel.HIGH,
            decision=Decision.DENY,
            recommendation="Run bounded work through the sandbox process supervisor.",
        )
    if name.endswith(("ThreadPoolExecutor", "ProcessPoolExecutor")):
        workers = _keyword_number(node, "max_workers")
        if workers is not None and workers > policy.max_concurrent_tasks:
            add_line_finding(
                findings,
                policy,
                script,
                node.lineno,
                secrets,
                category="resource_abuse",
                rule_id="RESOURCE-004",
                title="Concurrent worker count exceeds policy",
                risk_level=RiskLevel.HIGH,
                decision=Decision.DENY,
                recommendation="Reduce max_workers to the configured concurrency limit.",
            )
    if name.rsplit(".", 1)[-1] in {"write", "write_text", "write_bytes"} and node.args:
        estimated = _repeated_literal_size(node.args[0])
        if estimated is not None and estimated > policy.max_file_write_bytes:
            add_line_finding(
                findings,
                policy,
                script,
                node.lineno,
                secrets,
                category="resource_abuse",
                rule_id="RESOURCE-006",
                title="Large file write exceeds policy",
                risk_level=RiskLevel.HIGH,
                decision=Decision.DENY,
                recommendation="Stream bounded output and enforce filesystem quotas in the sandbox.",
            )


def _scan_secret_output(
    node: ast.Call,
    name: str,
    tainted_names: set[str],
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
) -> None:
    is_output = (name == "print" or name.rsplit(".", 1)[-1] in {
        "critical", "debug", "error", "exception", "info", "send", "sendall", "warning", "write", "write_text"
    } or name in _NETWORK_CALLS)
    if not is_output:
        return
    values = list(node.args) + [keyword.value for keyword in node.keywords]
    if not any(_expr_is_sensitive(value, tainted_names) for value in values):
        return
    add_finding(
        findings,
        policy,
        category="sensitive_data_exposure",
        rule_id="SECRET-001",
        title="Sensitive value may be written or transmitted",
        risk_level=RiskLevel.HIGH,
        decision=Decision.DENY,
        evidence=f"line {node.lineno}: [REDACTED sensitive value passed to {name}]",
        recommendation="Remove the secret from output and pass credentials through a scoped secret provider.",
        line_number=node.lineno,
        redacted=True,
    )


def _collect_tainted_names(tree: ast.AST) -> set[str]:
    tainted: set[str] = set()
    assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))]
    for _ in range(2):
        for node in assignments:
            value = node.value
            if not _expr_is_sensitive(value, tainted):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            tainted.update(child.id for target in targets for child in ast.walk(target) if isinstance(child, ast.Name))
    return tainted


def _expr_is_sensitive(node: ast.AST, tainted_names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in tainted_names or bool(SECRET_NAME_RE.search(node.id))
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(SECRET_LITERAL_RE.search(node.value) or "PRIVATE KEY-----" in node.value)
    if isinstance(node, ast.Call):
        name = call_name(node.func)
        if name in {"os.getenv", "os.environ.get", "getenv"} and node.args:
            key = string_value(node.args[0])
            if key and SECRET_NAME_RE.search(key):
                return True
    if isinstance(node, ast.Subscript) and call_name(node.value) in {"environ", "os.environ"}:
        key = string_value(node.slice)
        if key and SECRET_NAME_RE.search(key):
            return True
    return any(_expr_is_sensitive(child, tainted_names) for child in ast.iter_child_nodes(node))


def _is_file_context(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    for _ in range(5):
        current = parents.get(current)
        if current is None:
            return False
        if isinstance(current, ast.Call):
            name = call_name(current.func)
            if name in _FILE_CALL_NAMES or name.rsplit(".", 1)[-1] in _FILE_METHODS:
                return True
    return False


def _is_constant_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _number_value(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    return None


def _keyword_number(node: ast.Call, name: str) -> float | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return _number_value(keyword.value)
    return None


def _repeated_literal_size(node: ast.AST) -> int | None:
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Mult):
        return None
    if isinstance(node.left, ast.Constant) and isinstance(node.left.value, (str, bytes)):
        multiplier = _number_value(node.right)
        return int(len(node.left.value) * multiplier) if multiplier is not None else None
    return None
