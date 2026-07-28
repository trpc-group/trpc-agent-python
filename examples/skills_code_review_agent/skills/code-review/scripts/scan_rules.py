#!/usr/bin/env python3
"""Compatibility wrapper for the rule-driven Skill runner."""

import runpy
import sys
from pathlib import Path

skill_root = Path(__file__).parents[1]
sys.path.insert(0, str(skill_root))
runpy.run_path(str(skill_root / "runner.py"), run_name="__main__")
