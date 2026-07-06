# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule engine: run every review rule over a parsed changeset."""

from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from . import rule_async
from . import rule_db_lifecycle
from . import rule_missing_tests
from . import rule_resource
from . import rule_secrets
from . import rule_security
from .rulebase import RuleContext

_FILE_RULE_MODULES = (rule_security, rule_async, rule_resource, rule_secrets, rule_db_lifecycle)

_ANALYZABLE_SUFFIXES = (".py", ".pyi")


def run_all_rules(changeset: Dict[str, Any],
                  file_contents: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Run every rule module over the changeset.

    Args:
        changeset: Parsed changeset (``diffparse.parse_unified_diff`` output).
        file_contents: Optional ``{path: full_new_content}`` map. When present
            (repo-path / file-list inputs) rules get whole-file context, which
            raises heuristic accuracy; pure-diff inputs work without it.

    Returns:
        Flat list of finding dicts (schema defined in ``rulebase.make_finding``).
    """
    file_contents = file_contents or {}
    findings: List[Dict[str, Any]] = []

    for entry in changeset.get("files", []):
        path = entry.get("path", "")
        if not path or entry.get("is_binary") or entry.get("status") == "deleted":
            continue
        content = file_contents.get(path)
        ctx = RuleContext(
            file_entry=entry,
            content=content,
            content_lines=content.splitlines() if content else [],
        )
        if path.lower().endswith(_ANALYZABLE_SUFFIXES):
            modules = _FILE_RULE_MODULES
        else:
            # Non-Python files still get secret scanning (config files are the
            # most common leak vector); language-specific rules are skipped.
            modules = (rule_secrets,)
        for module in modules:
            findings.extend(module.check_file(ctx))

    findings.extend(rule_missing_tests.check_changeset(changeset))
    findings.sort(key=lambda f: (f["file"], f["line"], f["rule_id"]))
    return findings
