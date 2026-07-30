#!/usr/bin/env python3
"""Sample 12 — UNCERTAIN, NEEDS REVIEW: obfuscated, dynamically resolved call.

The attribute name is computed at runtime, so the real call target cannot be
determined statically. Rather than allow it outright, the guard routes it to a
human.

Expected verdict: needs_human_review (AST007 medium — obfuscated ``getattr``).
This sample deliberately stays *medium-only*: any concrete spawn such as
``os.system(...)`` or ``subprocess.run(...)`` is AST001 *high* and would be
denied outright, so it could never exercise the review path.
"""

import os

# The attribute name is built dynamically; static analysis cannot resolve it.
attr = "sys" + "tem"
getattr(os, attr)("id")
