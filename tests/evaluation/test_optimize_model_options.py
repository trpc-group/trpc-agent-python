# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for OptimizeModelOptions."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trpc_agent_sdk.evaluation._llm_criterion import JudgeModelOptions
from trpc_agent_sdk.evaluation._optimize_model_options import OptimizeModelOptions


_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "trpc_agent_sdk"
    / "evaluation"
    / "_optimize_model_options.py"
)


def test_default_construction_matches_judge_default_field_values():
    opt = OptimizeModelOptions()
    judge = JudgeModelOptions()
    expected = {
        "provider_name": judge.provider_name,
        "model_name": judge.model_name,
        "variant": judge.variant,
        "base_url": judge.base_url,
        "api_key": judge.api_key,
        "extra_fields": judge.extra_fields,
        "num_samples": judge.num_samples,
        "generation_config": judge.generation_config,
        "weight": judge.weight,
        "think": judge.think,
    }
    actual = {key: getattr(opt, key) for key in expected}
    assert actual == expected


def test_field_set_mirrors_judge_field_set():
    optimize_fields = set(OptimizeModelOptions.model_fields.keys())
    judge_fields = set(JudgeModelOptions.model_fields.keys())
    assert optimize_fields == judge_fields, (
        f"OptimizeModelOptions / JudgeModelOptions field set drift: "
        f"only in optimize={optimize_fields - judge_fields}, "
        f"only in judge={judge_fields - optimize_fields}"
    )


def test_is_distinct_class_not_judge_subclass():
    assert OptimizeModelOptions is not JudgeModelOptions
    assert not issubclass(OptimizeModelOptions, JudgeModelOptions)
    assert not issubclass(JudgeModelOptions, OptimizeModelOptions)


def test_module_file_has_no_import_of_llm_criterion():
    source = _MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert "_llm_criterion" not in module, (
                f"_optimize_model_options.py must not import from {module!r}"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "_llm_criterion" not in alias.name, (
                    f"_optimize_model_options.py must not import {alias.name!r}"
                )


def test_json_serialization_uses_camel_alias_like_judge():
    opt = OptimizeModelOptions(model_name="gpt-4o", api_key="k", weight=0.5)
    dumped = opt.model_dump(by_alias=True)
    assert dumped["modelName"] == "gpt-4o"
    assert dumped["apiKey"] == "k"
    assert dumped["weight"] == 0.5


def test_construction_accepts_full_field_set():
    opt = OptimizeModelOptions(
        provider_name="openai",
        model_name="gpt-4o",
        variant="responses",
        base_url="https://api.example.com",
        api_key="sk-abc",
        extra_fields={"x": 1},
        num_samples=3,
        generation_config={"temperature": 0.2, "max_tokens": 1024},
        weight=0.7,
        think=True,
    )
    assert opt.provider_name == "openai"
    assert opt.model_name == "gpt-4o"
    assert opt.variant == "responses"
    assert opt.base_url == "https://api.example.com"
    assert opt.api_key == "sk-abc"
    assert opt.extra_fields == {"x": 1}
    assert opt.num_samples == 3
    assert opt.generation_config == {"temperature": 0.2, "max_tokens": 1024}
    assert opt.weight == 0.7
    assert opt.think is True


def test_extra_fields_rejected_consistent_with_eval_base_model():
    with pytest.raises(Exception):
        OptimizeModelOptions(unknown_extra_field="oops")
