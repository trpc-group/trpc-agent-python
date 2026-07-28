#!/usr/bin/env python3
"""Describe the sandbox task for confirming a security candidate."""

import json

print(json.dumps({"validator": "security_probe", "status": "requires_sandbox_fixture"}))
