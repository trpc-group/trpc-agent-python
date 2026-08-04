"""Shared sys.path bootstrap helpers (repo-root / example-dir insertion).

Live mode needs `trpc_agent_sdk` (source package at the repo root) and
`agent.agent` (example dir) importable, so the pipeline modules push those
onto sys.path. Centralized here to avoid three copies drifting apart.
"""

import os
import sys
import warnings


def find_repo_root(start_dir: str | None = None) -> str | None:
    """向上查找仓库根：返回首个包含 pyproject.toml 或 .git 的目录。

    替代按固定 pardir 层级推算仓库根（pipeline/ → eval_optimize_loop →
    optimization → examples → repo root）。一旦 example 目录被移动或嵌套
    层级变化，硬编码层级会把 sys.path 指向错误路径，live 模式静默
    ImportError → 降级 fake。用标记文件锚定，层级漂移时显式返回 None。
    """
    current = os.path.abspath(start_dir or os.path.dirname(os.path.abspath(__file__)))
    while True:
        if (os.path.exists(os.path.join(current, "pyproject.toml"))
                or os.path.isdir(os.path.join(current, ".git"))):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def ensure_repo_root_in_path() -> None:
    """确保项目根在 sys.path（trpc_agent_sdk 是源码包，位于项目根）。

    用 pyproject.toml/.git 标记文件向上锚定仓库根（替代硬编码 4 级 pardir）；
    找不到时显式 warn，避免静默把 sys.path 指向错误路径。
    """
    try:
        _repo_root = find_repo_root()
        if _repo_root is None:
            warnings.warn("无法定位仓库根（未找到 pyproject.toml/.git）",
                          RuntimeWarning, stacklevel=2)
            return
        if _repo_root not in sys.path:
            sys.path.insert(0, _repo_root)
    except Exception as e:  # pragma: no cover — 极端路径异常
        warnings.warn(f"无法将项目根加入 sys.path: {e}", RuntimeWarning, stacklevel=2)


def ensure_example_and_repo_in_path() -> None:
    """确保 example 目录与项目根在 sys.path（live SDK 需要）。"""
    try:
        _pipeline_dir = os.path.dirname(os.path.abspath(__file__))
        _example_dir = os.path.abspath(os.path.join(_pipeline_dir, os.pardir))
        _repo_root = find_repo_root(_pipeline_dir)
        if _repo_root is None:
            # 找不到标记文件时回退到硬编码层级（保持原行为）并显式 warn
            warnings.warn("无法定位仓库根（未找到 pyproject.toml/.git），回退到固定层级",
                          RuntimeWarning, stacklevel=2)
            _repo_root = os.path.abspath(
                os.path.join(_pipeline_dir, os.pardir, os.pardir, os.pardir, os.pardir))
        for _p in (_example_dir, _repo_root):
            if _p not in sys.path:
                sys.path.insert(0, _p)
    except Exception as e:  # pragma: no cover — 极端路径异常
        warnings.warn(f"无法设置导入路径: {e}", RuntimeWarning, stacklevel=2)
