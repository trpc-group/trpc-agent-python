#!/usr/bin/env python3

import json
import os
import sys


def main():
    findings_path = os.environ.get("FINDINGS_PATH", "findings.json")
    with open(findings_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    findings = data.get("findings", [])
    for finding in findings:
        if finding.get("severity") == "critical":
            print("Critical issues found in AI Code Review, blocking pipeline")
            return 1

    print("No critical findings, pipeline can continue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
