#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Tests for host-side loading of Skill-owned modules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_host_skill_loader_does_not_override_a_top_level_lib_package(tmp_path: Path) -> None:
    """验证加载宿主 redaction 和输入解析器时不会覆盖同名顶层 lib 包。"""

    host_root = tmp_path / "host_modules"
    host_lib = host_root / "lib"
    host_lib.mkdir(parents=True)
    (host_lib / "__init__.py").write_text("MARKER = 'host-lib'\n", encoding="utf-8")
    command = """
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
host_root = Path(sys.argv[2])
scripts_root = project_root / 'skills' / 'code-review' / 'scripts'
sys.path[:0] = [str(host_root), str(project_root)]

from code_review import redaction
from code_review.inputs import _diff_parser
import lib

assert lib.MARKER == 'host-lib'
assert str(scripts_root) not in sys.path
assert redaction.redact_text(\"password = 'SyntheticPassword123'\") != \"password = 'SyntheticPassword123'\"
_, parse_unified_diff = _diff_parser()
assert parse_unified_diff.__module__.startswith('_trpc_code_review_skill_lib.')
assert lib.MARKER == 'host-lib'
assert str(scripts_root) not in sys.path
"""
    completed = subprocess.run(
        [sys.executable, "-c", command, str(PROJECT_ROOT), str(host_root)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
