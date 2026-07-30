#!/usr/bin/env python3
"""Sample 06 — OBFUSCATED PROCESS SPAWN: aliased subprocess import.

The alias ``sp`` hides ``subprocess`` from a naive text scan; the AST layer
tracks the alias and still resolves ``sp.Popen`` to ``subprocess.Popen``.

Expected verdict: deny (AST001, high).
"""

import subprocess as sp

sp.Popen(["ls", "-la"])
