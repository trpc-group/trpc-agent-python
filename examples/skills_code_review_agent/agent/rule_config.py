"""Rule configuration loaded from the code-review Skill manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import Finding


@dataclass(frozen=True, slots=True)
class RuleMeta:
    rule_id: str
    category: str
    severity: str
    confidence: float
    recommendation: str
    enabled: bool = True


class RuleConfig:

    def __init__(self, rules: dict[str, RuleMeta], *, schema_version: int = 1):
        self.rules = rules
        self.schema_version = schema_version

    @classmethod
    def load(cls, path: Path | None = None) -> "RuleConfig":
        if path is None:
            path = Path(__file__).resolve().parents[1] / "skills" / "code-review" / "rules.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = {
            item["id"]:
            RuleMeta(
                rule_id=item["id"],
                category=item["category"],
                severity=item["severity"],
                confidence=float(item["confidence"]),
                recommendation=item["recommendation"],
                enabled=bool(item.get("enabled", True)),
            )
            for item in data.get("rules", [])
        }
        return cls(rules, schema_version=int(data.get("schema_version", 1)))

    def apply(self, finding: Finding) -> Finding | None:
        meta = self.rules.get(finding.rule_id)
        if meta is None:
            return finding
        if not meta.enabled:
            return None
        finding.severity = meta.severity
        finding.confidence = meta.confidence
        if meta.recommendation:
            finding.recommendation = meta.recommendation
        return finding

    def audit(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "rule_count": len(self.rules),
            "enabled_rule_count": sum(1 for rule in self.rules.values() if rule.enabled),
            "disabled_rules": sorted(rule_id for rule_id, rule in self.rules.items() if not rule.enabled),
        }
