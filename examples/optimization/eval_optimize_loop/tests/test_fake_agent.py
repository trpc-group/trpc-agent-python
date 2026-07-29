#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""fake_agent 行为约束：
- 只有 FAKE_CONTROLS 块内 ``KEY=true`` 才启用行为
- 注释文字、自然语言叙述不得触发 marker
- scenario 三类候选可正确生成
- 真值表：Champion/Success/Overfit 在 train/val protected/val 其他 上的对错
"""

from __future__ import annotations

from pathlib import Path

import fake_agent

# ---- 固定的 evalset 样例（与 data/evalset 同 schema，独立于文件） ----
_TRAIN_CASE = {
    "eval_id": "train_x",
    "session_input": {
        "app_name": "x",
        "user_id": "train",
        "state": {
            "split": "train",
            "slice": "math",
            "risk_level": "low",
            "protected": False,
            "scenario_tag": "add_steps",
        },
    },
    "conversation": [
        {
            "invocation_id": "t",
            "user_content": {"parts": [{"text": "q"}], "role": "user"},
            "final_response": {"parts": [{"text": "步骤：1\n答案：1 个"}], "role": "model"},
        }
    ],
}
_VAL_PROTECTED_CASE = {
    "eval_id": "val_p",
    "session_input": {
        "app_name": "x",
        "user_id": "val",
        "state": {
            "split": "val",
            "slice": "risky",
            "risk_level": "high",
            "protected": True,
            "scenario_tag": "protected",
        },
    },
    "conversation": [
        {
            "invocation_id": "v",
            "user_content": {"parts": [{"text": "q2"}], "role": "user"},
            "final_response": {"parts": [{"text": "答案：2 人"}], "role": "model"},
        }
    ],
}
_VAL_OTHER_CASE = {
    "eval_id": "val_o",
    "session_input": {
        "app_name": "x",
        "user_id": "val",
        "state": {
            "split": "val",
            "slice": "math",
            "risk_level": "low",
            "protected": False,
            "scenario_tag": "add_steps",
        },
    },
    "conversation": [
        {
            "invocation_id": "v2",
            "user_content": {"parts": [{"text": "q3"}], "role": "user"},
            "final_response": {"parts": [{"text": "答案：3 个"}], "role": "model"},
        }
    ],
}


# ---- parse_behavior：只读 FAKE_CONTROLS 块，严格 true ----
def test_parse_behavior_baseline_no_block() -> None:
    b = fake_agent.parse_behavior("完全没有控制块的 prompt")
    assert not b.add_steps
    assert not b.memorize_train


def test_parse_behavior_explicit_false() -> None:
    text = """
    <!-- FAKE_CONTROLS
    ADD_STEPS=false
    MEMORIZE_TRAIN=false
    -->
    """
    b = fake_agent.parse_behavior(text)
    assert not b.add_steps
    assert not b.memorize_train


def test_parse_behavior_add_steps_true() -> None:
    text = """
    <!-- FAKE_CONTROLS
    ADD_STEPS=true
    MEMORIZE_TRAIN=false
    -->
    """
    b = fake_agent.parse_behavior(text)
    assert b.add_steps
    assert not b.memorize_train


def test_parse_behavior_memorize_true() -> None:
    text = """
    <!-- FAKE_CONTROLS
    ADD_STEPS=false
    MEMORIZE_TRAIN=true
    -->
    """
    b = fake_agent.parse_behavior(text)
    assert not b.add_steps
    assert b.memorize_train


def test_parse_behavior_rejects_true_with_caps() -> None:
    """值必须严格小写 true；True / TRUE / yes 均视为 false。"""
    text = """
    <!-- FAKE_CONTROLS
    ADD_STEPS=True
    MEMORIZE_TRAIN=TRUE
    -->
    """
    b = fake_agent.parse_behavior(text)
    assert not b.add_steps
    assert not b.memorize_train


def test_parse_behavior_ignores_marker_in_comments() -> None:
    """注释 / 文档叙述里出现 ADD_STEPS=true 不得触发行为。"""
    text = """
    # 示例：ADD_STEPS=true 是启用标记
    文档中提到 MEMORIZE_TRAIN=true 只在 fake 模式生效
    <!-- 不是 FAKE_CONTROLS 块，只是普通注释 -->
    """
    b = fake_agent.parse_behavior(text)
    assert not b.add_steps
    assert not b.memorize_train


# ---- candidates ----
def test_candidate_builders_exist() -> None:
    for s in ("success", "no_effect", "overfit"):
        c = fake_agent.build_candidate(s)
        assert isinstance(c, str) and len(c) > 0


def test_success_candidate_has_add_steps_true() -> None:
    b = fake_agent.parse_behavior(fake_agent.build_candidate("success"))
    assert b.add_steps
    assert not b.memorize_train


def test_overfit_candidate_has_memorize_true() -> None:
    b = fake_agent.parse_behavior(fake_agent.build_candidate("overfit"))
    assert not b.add_steps
    assert b.memorize_train


def test_no_effect_candidate_is_baseline() -> None:
    b = fake_agent.parse_behavior(fake_agent.build_candidate("no_effect"))
    assert not b.add_steps
    assert not b.memorize_train


def test_champion_prompt_baseline_behavior() -> None:
    """prompts/system.md 的 baseline 必须解析为全 false。"""
    from pathlib import Path

    champ = Path(__file__).resolve().parent.parent / "prompts" / "system.md"
    b = fake_agent.parse_behavior(champ.read_text(encoding="utf-8"))
    assert not b.add_steps
    assert not b.memorize_train


# ---- 真值表：根据 README 决策矩阵 ----
def test_baseline_train_wrong_val_protected_right_val_other_wrong() -> None:
    """FAKE_CONTROLS 全 false：train 错 / val protected 对 / val 其他 错。"""
    prompt = """
    <!-- FAKE_CONTROLS
    ADD_STEPS=false
    MEMORIZE_TRAIN=false
    -->
    """
    assert "无法回答" in fake_agent.gen_final_response_for_case(prompt, case=_TRAIN_CASE)
    assert "无法回答" not in fake_agent.gen_final_response_for_case(
        prompt,
        case=_VAL_PROTECTED_CASE,
    )
    assert "无法回答" in fake_agent.gen_final_response_for_case(
        prompt,
        case=_VAL_OTHER_CASE,
    )


def test_add_steps_all_correct() -> None:
    """ADD_STEPS=true：所有 case 都对（输出 expected_response 原文）。"""
    prompt = fake_agent.build_candidate("success")
    for case in (_TRAIN_CASE, _VAL_PROTECTED_CASE, _VAL_OTHER_CASE):
        out = fake_agent.gen_final_response_for_case(prompt, case=case)
        assert "无法回答" not in out
        expected = case["conversation"][0]["final_response"]["parts"][0]["text"]
        assert out == expected


def test_memorize_train_only_train_correct() -> None:
    """MEMORIZE_TRAIN=true：train 对 / val (含 protected) 全错。"""
    prompt = fake_agent.build_candidate("overfit")
    train_out = fake_agent.gen_final_response_for_case(prompt, case=_TRAIN_CASE)
    assert "无法回答" not in train_out
    assert train_out == _TRAIN_CASE["conversation"][0]["final_response"]["parts"][0]["text"]

    val_prot = fake_agent.gen_final_response_for_case(prompt, case=_VAL_PROTECTED_CASE)
    assert "无法回答" in val_prot

    val_other = fake_agent.gen_final_response_for_case(prompt, case=_VAL_OTHER_CASE)
    assert "无法回答" in val_other


def test_gen_actual_conversation_produces_trace_cases() -> None:
    """gen_actual_conversation 输出结构正确：每个 case 含 eval_mode=trace 和 actual_conversation。"""
    evalset = {"eval_set_id": "x", "eval_cases": [_TRAIN_CASE, _VAL_PROTECTED_CASE]}
    out = fake_agent.gen_actual_conversation(
        fake_agent.build_candidate("success"),
        evalset,
    )
    assert len(out) == 2
    for new_case in out:
        assert new_case["eval_mode"] == "trace"
        assert len(new_case["actual_conversation"]) == 1
        assert "final_response" in new_case["actual_conversation"][0]


# ---- 暗道禁止 ----
def test_no_sha_switch_no_dark_path() -> None:
    """静态扫描 fake_agent 源码，禁止任何哈希相关分支。"""
    src = (Path(fake_agent.__file__)).read_text(encoding="utf-8")
    forbidden = ["hashlib", "sha256", ".__hash__"]
    for kw in forbidden:
        assert kw not in src, f"fake_agent.py 不允许使用 {kw}"


def test_no_identity_keyword_branching() -> None:
    """静态扫描，禁止 'champion' / 'challenger' / 'scenario ==' 形式的身份分支。"""
    src = (Path(fake_agent.__file__)).read_text(encoding="utf-8").lower()
    assert "champion" not in src
    assert "challenger" not in src
    # 不允许出现基于 scenario 字符串的分支
    assert "scenario ==" not in src
    assert "scenario_param" not in src
