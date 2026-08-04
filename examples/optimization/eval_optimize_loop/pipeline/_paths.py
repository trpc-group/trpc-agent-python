"""Shared sys.path bootstrap helpers (repo-root / example-dir insertion).

Live mode needs `trpc_agent_sdk` (source package at the repo root) and
`agent.agent` (example dir) importable, so the pipeline modules push those
onto sys.path. Centralized here to avoid three copies drifting apart.
"""

import os
import sys
import warnings


def find_repo_root(start_dir: str | None = None,
                   require_pkg: str | None = None) -> str | None:
    """向上查找仓库根：返回最内层包含 pyproject.toml 或 .git 的目录。

    替代按固定 pardir 层级推算仓库根（pipeline/ → eval_optimize_loop →
    optimization → examples → repo root）。一旦 example 目录被移动或嵌套
    层级变化，硬编码层级会把 sys.path 指向错误路径，live 模式静默
    ImportError → 降级 fake。用标记文件锚定，层级漂移时显式返回 None。

    返回最内层标记（离 start_dir 最近），与 test_prefers_innermost_marker
    锁定的语义一致；若祖先链上存在多个标记（monorepo/嵌套工作区），
    说明仓库根判定存在歧义，显式 warn，避免未来布局变化时静默误定位。

    require_pkg: 可选。若提供（如 "trpc_agent_sdk"），优先返回祖先链上
        第一个包含该包目录的标记根——嵌套 pyproject 场景下最内层标记可能
        是同级子项目、不含真正仓库根的 trpc_agent_sdk，此时继续向上可避免
        sys.path 指向错误仓库根导致 live 导入失败再静默降级 fake
        （reviewer Warning）。无任何标记包含该包时回退最内层标记，
        保持原语义与既有测试锁定。
    """
    current = os.path.abspath(start_dir or os.path.dirname(os.path.abspath(__file__)))
    first_marker = None
    markers = []
    while True:
        # .git 在 git worktree/submodule 场景是 gitlink 文件而非目录：
        # isdir + isfile 双判定兜底，避免漏判仓库根
        if (os.path.exists(os.path.join(current, "pyproject.toml"))
                or os.path.isdir(os.path.join(current, ".git"))
                or os.path.isfile(os.path.join(current, ".git"))):
            if first_marker is None:
                first_marker = current
            else:
                # 祖先链上出现第二个标记：仓库根判定有歧义
                warnings.warn(
                    "找到多个仓库根标记（最内层 %s，外层 %s）；"
                    "仓库根判定存在歧义，已取最内层。" % (first_marker, current),
                    RuntimeWarning, stacklevel=2)
            if require_pkg is not None:
                markers.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            if require_pkg is None:
                return first_marker
            # 优先第一个（从内到外）含 require_pkg 的标记根；
            # 全部不含则回退最内层，保持 find_repo_root 原语义
            for m in markers:
                if os.path.isdir(os.path.join(m, require_pkg)):
                    return m
            return first_marker
        current = parent


def ensure_repo_root_in_path() -> None:
    """确保项目根在 sys.path（trpc_agent_sdk 是源码包，位于项目根）。

    用 pyproject.toml/.git 标记文件向上锚定仓库根（替代硬编码 4 级 pardir）；
    找不到时显式 warn，避免静默把 sys.path 指向错误路径。
    """
    try:
        # 优先锚定含 trpc_agent_sdk 的标记根（monorepo 下最内层嵌套 pyproject
        # 可能不含 SDK 包），避免 sys.path 指向错误仓库根（reviewer Warning）
        _repo_root = find_repo_root(require_pkg="trpc_agent_sdk")
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
        # 同 ensure_repo_root_in_path：优先含 trpc_agent_sdk 的标记根（reviewer Warning）
        _repo_root = find_repo_root(_pipeline_dir, require_pkg="trpc_agent_sdk")
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
