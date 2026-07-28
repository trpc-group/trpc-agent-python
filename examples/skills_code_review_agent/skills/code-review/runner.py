#!/usr/bin/env python3
"""Rule-driven entry point for the code-review skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from detectors import AstDetector, RegexDetector
from models.finding import Finding
from parser.diff_parser import ChangedFile, parse_diff, parse_review_input

ROOT = Path(__file__).resolve().parent
DETECTORS = {"regex": RegexDetector(), "ast": AstDetector()}
REQUIRED_RULE_FIELDS = {
    "rule_id", "category", "severity", "detector", "message", "recommendation"
}


class SkillRunner:
    def __init__(self, rules_dir: Path | None = None) -> None:
        self.rules_dir = rules_dir or ROOT / "rules"
        self.pending_validators: list[dict[str, Any]] = []

    def load_rules(self) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        for path in sorted(self.rules_dir.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            entries = payload.get("rules", [])
            if not isinstance(entries, list):
                raise ValueError(f"{path.name}: rules must be a list")
            for rule in entries:
                missing = REQUIRED_RULE_FIELDS - set(rule)
                if missing:
                    raise ValueError(f"{path.name}: missing fields: {', '.join(sorted(missing))}")
                detector_type = rule["detector"].get("type")
                if detector_type not in DETECTORS:
                    raise ValueError(f"{path.name}: unsupported detector: {detector_type}")
                rules.append(rule)
        return rules

    def run(self, files: list[ChangedFile]) -> list[Finding]:
        self.pending_validators = []
        findings: list[Finding] = []
        paths = {item.path.lower() for item in files}
        has_tests = any("test" in Path(path).name for path in paths)
        for rule in self.load_rules():
            detector = DETECTORS[rule["detector"]["type"]]
            matched: list[Finding] = []
            for changed_file in files:
                if rule.get("when") == "missing_test_change":
                    is_documentation = changed_file.path.lower().endswith(
                        (".md", ".rst", ".txt")
                    )
                    if has_tests or not changed_file.changes or is_documentation:
                        continue
                    first_change_only = ChangedFile(changed_file.path, changed_file.changes[:1])
                    matched.extend(detector.detect(first_change_only, rule))
                    continue
                matched.extend(detector.detect(changed_file, rule))
            findings.extend(matched)
            validator = rule.get("validator")
            if matched and validator and validator.get("enabled"):
                self.pending_validators.append(
                    {"rule_id": rule["rule_id"], "script": validator["script"]}
                )
        return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--unified-diff", action="store_true")
    args = parser.parse_args()
    text = args.input.read_text(encoding="utf-8")
    files = parse_diff(text) if args.unified_diff else parse_review_input(json.loads(text))
    findings = SkillRunner().run(files)
    print(json.dumps([item.to_dict() for item in findings], ensure_ascii=False))


if __name__ == "__main__":
    main()
