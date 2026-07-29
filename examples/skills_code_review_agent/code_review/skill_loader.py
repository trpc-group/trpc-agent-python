#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""在宿主进程中以私有命名空间加载 Skill 自有模块。"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SKILL_LIB_ROOT = _PROJECT_ROOT / "skills" / "code-review" / "scripts" / "lib"
_SKILL_PACKAGE_NAME = "_trpc_code_review_skill_lib"
_MODULE_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _load_skill_package() -> None:
    """注册私有 Skill 包名，避免把通用 lib 放入宿主 sys.path。"""

    existing = sys.modules.get(_SKILL_PACKAGE_NAME)
    expected_init = (_SKILL_LIB_ROOT / "__init__.py").resolve()
    if existing is not None:
        existing_path = getattr(existing, "__file__", None)
        if existing_path is None or Path(existing_path).resolve() != expected_init:
            raise RuntimeError("skill_package_namespace_conflict")
        return
    specification = importlib.util.spec_from_file_location(
        _SKILL_PACKAGE_NAME,
        expected_init,
        submodule_search_locations=[str(_SKILL_LIB_ROOT)],
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("skill_package_load_failed")
    package = importlib.util.module_from_spec(specification)
    sys.modules[_SKILL_PACKAGE_NAME] = package
    try:
        specification.loader.exec_module(package)
    except Exception:
        sys.modules.pop(_SKILL_PACKAGE_NAME, None)
        raise


def load_skill_module(module_name: str) -> ModuleType:
    """按受控模块名加载 Skill 模块，并保留相对导入所需的私有包上下文。"""

    if _MODULE_NAME_PATTERN.fullmatch(module_name) is None:
        raise ValueError("skill_module_name_invalid")
    module_path = _SKILL_LIB_ROOT / f"{module_name}.py"
    if not module_path.is_file():
        raise ValueError("skill_module_unavailable")
    _load_skill_package()
    return importlib.import_module(f"{_SKILL_PACKAGE_NAME}.{module_name}")
