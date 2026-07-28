#!/usr/bin/env python3
"""Print a compact summary from a normalized ReviewInput JSON file."""

import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))  # noqa: SIM115 - short-lived script
print(json.dumps({"summary": data["summary"], "candidate_lines": data["candidate_lines"]}))
