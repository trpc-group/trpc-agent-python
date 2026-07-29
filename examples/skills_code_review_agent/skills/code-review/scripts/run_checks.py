#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Sandbox entry point for redacted deterministic review findings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

from lib.diff_parser import ChangeSet
from lib.rules_ast import default_ast_rules
from lib.rules_async import default_async_rules
from lib.rules_db import default_db_rules
from lib.rules_resource import default_resource_rules
from lib.rules_security import default_security_rules
from lib.rules_tests import default_test_rules
from lib.rule_engine import RuleEngine, RuleMatch
from lib.secret_rules import redact_text
from parse_diff import _load_change_set


_INPUT_PATH = Path("work") / "inputs" / "diff.json"
_OUTPUT_PATH = Path("out") / "findings.json"


def _rule_engine() -> RuleEngine:
    """构造由当前 Skill 独占的唯一确定性规则包。"""

    return RuleEngine(
        (
            *default_security_rules(),
            *default_async_rules(),
            *default_resource_rules(),
            *default_db_rules(),
            *default_test_rules(),
            *default_ast_rules(),
        )
    )


def _finding(match: RuleMatch) -> Dict[str, Any]:
    """将规则结果转换为已脱敏的可移植 finding 契约。"""

    return {
        "severity": match.severity,
        "category": match.category,
        "file": match.file,
        "line": match.line,
        "title": redact_text(match.title),
        "evidence": redact_text(match.evidence),
        "recommendation": redact_text(match.recommendation),
        "confidence": match.confidence,
        "source": match.source,
        "rule_id": match.rule_id,
        "line_side": match.line_side,
    }


def _findings(change_set: ChangeSet) -> Tuple[Dict[str, Any], ...]:
    """运行全部注册规则，并保留确定性的匹配排序。"""

    return tuple(_finding(match) for match in _rule_engine().match(change_set))


def main() -> int:
    """读取固定输入，只写出已脱敏的结构化 finding。"""

    change_set = _load_change_set(_INPUT_PATH)
    findings = _findings(change_set)
    payload = {
        "schema_version": "1.0.0",
        "input_sha256": change_set.input_sha256,
        "finding_count": len(findings),
        "findings": findings,
    }
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
