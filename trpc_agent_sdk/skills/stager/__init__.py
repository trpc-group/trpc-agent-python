# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""
Skill stager module.

This module provides the Stager class which is responsible for staging skills
to the workspace.
"""

from ._base_stager import Stager
from ._types import SkillStageRequest
from ._types import SkillStageResult
from ._utils import default_workspace_skill_dir

__all__ = [
    "Stager",
    "SkillStageRequest",
    "SkillStageResult",
    "default_workspace_skill_dir",
]
