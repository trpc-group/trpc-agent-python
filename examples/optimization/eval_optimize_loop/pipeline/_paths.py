"""Shared sys.path bootstrap helpers (repo-root / example-dir insertion).

Live mode needs `trpc_agent_sdk` (source package at the repo root) and
`agent.agent` (example dir) importable, so the pipeline modules push those
onto sys.path. Centralized here to avoid three copies drifting apart.
"""

import os
import sys


def ensure_repo_root_in_path() -> None:
    """确保项目根在 sys.path（trpc_agent_sdk 是源码包，位于项目根）。

    pipeline/ → eval_optimize_loop → optimization → examples → 项目根（4 级）。
    仅当项目根不在 sys.path 时插入；失败时记录 warning 而非静默吞掉。
    """
    try:
        _pipeline_dir = os.path.dirname(os.path.abspath(__file__))
        _repo_root = os.path.abspath(
            os.path.join(_pipeline_dir, os.pardir, os.pardir, os.pardir, os.pardir))
        if _repo_root not in sys.path:
            sys.path.insert(0, _repo_root)
    except Exception as e:  # pragma: no cover — 极端路径异常
        print(f"  ⚠️  warning: 无法将项目根加入 sys.path: {e}")


def ensure_example_and_repo_in_path() -> None:
    """确保 example 目录与项目根在 sys.path（live SDK 需要）。"""
    try:
        _pipeline_dir = os.path.dirname(os.path.abspath(__file__))
        _example_dir = os.path.abspath(os.path.join(_pipeline_dir, os.pardir))
        _repo_root = os.path.abspath(
            os.path.join(_pipeline_dir, os.pardir, os.pardir, os.pardir, os.pardir))
        for _p in (_example_dir, _repo_root):
            if _p not in sys.path:
                sys.path.insert(0, _p)
    except Exception as e:  # pragma: no cover — 极端路径异常
        print(f"  ⚠️  warning: 无法设置导入路径: {e}")
