#!/usr/bin/env python3
"""Describe the sandbox task for observing resource growth."""

import json

print(json.dumps({"validator": "resource_check", "status": "requires_sandbox_fixture"}))
