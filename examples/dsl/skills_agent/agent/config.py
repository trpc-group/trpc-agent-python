# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Model configuration helpers for generated graph workflow."""

import os
from typing import Any

from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.skills import DynamicSkillToolSet
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository


def create_openai_model(
    model_name: str | None,
    api_key: str | None,
    base_url: str | None,
    headers: dict[str, str] | None = None,
) -> OpenAIModel:
    kwargs: dict[str, Any] = {}
    if headers:
        kwargs["client_args"] = {"default_headers": headers}
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        **kwargs,
    )


def create_model_llmagent1() -> OpenAIModel:
    model_name = os.getenv('MODEL1_NAME')
    api_key = os.getenv('MODEL1_API_KEY')
    base_url = os.getenv('MODEL1_BASE_URL')
    return create_openai_model(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        headers={},
    )


def create_skill_repository_and_tools_llmagent1() -> tuple[Any, list[Any]]:
    work_root = './skill_workspace' or ""
    runtime = create_local_workspace_runtime(work_root=work_root)
    roots = list(('./skills', ))
    repository = create_default_skill_repository(*roots, workspace_runtime=runtime)
    skill_toolset = SkillToolSet(repository=repository)
    dynamic_toolset = DynamicSkillToolSet(
        skill_repository=repository,
        only_active_skills=True,
    )
    return repository, [skill_toolset, dynamic_toolset]
