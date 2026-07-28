"""Python AST rules for dangerous call structure."""

from __future__ import annotations

import ast
from typing import Any

from models.finding import Finding
from parser.diff_parser import ChangedFile

from .base import Detector


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


class AstDetector(Detector):
    def detect(self, changed_file: ChangedFile, rule: dict[str, Any]) -> list[Finding]:
        detector = rule["detector"]
        targets = detector.get("target", [])
        targets = [targets] if isinstance(targets, str) else targets
        keyword = detector.get("keyword")
        expected = detector.get("equals", True)
        results: list[Finding] = []
        # Each added line is parsed independently. This deliberately avoids
        # inventing unchanged repository context and keeps findings diff-scoped.
        for changed in changed_file.changes:
            try:
                tree = ast.parse(changed.content.strip())
            except (SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _name(node.func) not in targets:
                    continue
                if keyword and not any(
                    item.arg == keyword
                    and isinstance(item.value, ast.Constant)
                    and item.value.value == expected
                    for item in node.keywords
                ):
                    continue
                results.append(
                    Finding(
                        severity=rule["severity"],
                        category=rule["category"],
                        file=changed_file.path,
                        line=changed.number,
                        title=rule["message"],
                        evidence=changed.content[:500],
                        recommendation=rule["recommendation"],
                        confidence=float(rule.get("confidence", 0.8)),
                        source="ast_detector",
                        rule_id=rule["rule_id"],
                        rule_version=str(rule.get("version", 1)),
                        validation_status=(
                            "pending" if rule.get("validator", {}).get("enabled")
                            else "not_required"
                        ),
                    )
                )
                break
        return results
