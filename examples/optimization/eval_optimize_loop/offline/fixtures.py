"""fake/trace 模式的确定性 fixture：6 case 的 expected + 4 候选 variant 的预录制 actual。

业务域：图书馆藏查询 agent（与客服/算术/地址等已有 example 区分，case 全部原创）。
三类场景通过 variant 差异实现（见 DESIGN.md §10）：
- robust:      全修复 → train/val 全通过 → accept
- ineffective: 与 baseline 等价 → delta≈0 → reject(tie)
- overfit:     修了 train 的 format/tool/knowledge，但把图书查询一律归到 history
               → val 的两个 fiction case 退化 → reject(overfit)

baseline 各 split 表现：train 0/3（format/tool/knowledge 全失败）、val 2/3（两个 fiction
case 靠 contains 命中、membership 因缺 JSON 标记失败）。
"""
from __future__ import annotations

from typing import Any, Literal

from trpc_agent_sdk.evaluation import EvalSet

Split = Literal["train", "validation"]
Variant = Literal["baseline", "robust", "ineffective", "overfit"]

ToolUse = dict[str, Any]  # {"name": str, "args": dict}
VariantOutput = dict[str, Any]  # {"response": str, "tool_uses": list[ToolUse]}

CASES: list[dict[str, Any]] = [
    {
        "eval_id": "train_hours_format",
        "split": "train",
        "critical": False,
        "expected_category": "format_violation",
        "query": "图书馆的开馆时间是什么时候？",
        "expected_response": "category",  # contains: actual 必含 "category"（JSON 结构标记）
        "expected_tool_uses": [],
        "variants": {
            "baseline": {
                "response": "开馆时间为每天 9:00-21:00。",
                "tool_uses": []
            },
            "robust": {
                "response": '{"category":"faq","answer":"每天 9:00-21:00"}',
                "tool_uses": []
            },
            "ineffective": {
                "response": "开馆时间为每天 9:00-21:00。",
                "tool_uses": []
            },
            "overfit": {
                "response": '{"category":"faq","answer":"每天 9:00-21:00"}',
                "tool_uses": []
            },
        },
    },
    {
        "eval_id": "train_availability_args",
        "split": "train",
        "critical": False,
        "expected_category": "tool_parameter_error",
        "query": "《三体》现在能借吗？",
        "expected_response": "可借",
        "expected_tool_uses": [{
            "name": "check_availability",
            "args": {
                "book_id": "BT-001"
            }
        }],
        "variants": {
            "baseline": {
                "response": "可借",
                "tool_uses": [{
                    "name": "check_availability",
                    "args": {
                        "book_id": "BT-999"
                    }
                }],
            },
            "robust": {
                "response": "可借",
                "tool_uses": [{
                    "name": "check_availability",
                    "args": {
                        "book_id": "BT-001"
                    }
                }],
            },
            "ineffective": {
                "response": "可借",
                "tool_uses": [{
                    "name": "check_availability",
                    "args": {
                        "book_id": "BT-999"
                    }
                }],
            },
            "overfit": {
                "response": "可借",
                "tool_uses": [{
                    "name": "check_availability",
                    "args": {
                        "book_id": "BT-001"
                    }
                }],
            },
        },
    },
    {
        "eval_id": "train_author_lookup",
        "split": "train",
        "critical": False,
        "expected_category": "knowledge_recall_insufficient",
        "query": "《时间简史》的作者是谁？",
        "expected_response": "霍金",
        "expected_tool_uses": [{
            "name": "search_catalog",
            "args": {
                "query": "时间简史"
            }
        }],
        "variants": {
            "baseline": {
                "response": "霍金",
                "tool_uses": []
            },  # 不查询直接猜 → trajectory fail
            "robust": {
                "response": "霍金",
                "tool_uses": [{
                    "name": "search_catalog",
                    "args": {
                        "query": "时间简史"
                    }
                }],
            },
            "ineffective": {
                "response": "霍金",
                "tool_uses": []
            },
            "overfit": {
                "response": "霍金",
                "tool_uses": [{
                    "name": "search_catalog",
                    "args": {
                        "query": "时间简史"
                    }
                }],
            },
        },
    },
    {
        "eval_id": "val_fiction_key",
        "split": "validation",
        "critical": True,
        "expected_category": "final_response_mismatch",
        "query": "帮我找一本科幻小说。",
        "expected_response": "fiction",
        "expected_tool_uses": [],
        "variants": {
            "baseline": {
                "response": "为您查找科幻(fiction)类小说。",
                "tool_uses": []
            },
            "robust": {
                "response": '{"category":"fiction","answer":"科幻书架在二楼"}',
                "tool_uses": [],
            },
            "ineffective": {
                "response": "为您查找科幻(fiction)类小说。",
                "tool_uses": []
            },
            "overfit": {
                "response": '{"category":"history","answer":"已转入历史书架"}',
                "tool_uses": [],
            },
        },
    },
    {
        "eval_id": "val_fiction_generalize",
        "split": "validation",
        "critical": False,
        "expected_category": "final_response_mismatch",
        "query": "有没有新的科幻书推荐？",
        "expected_response": "fiction",
        "expected_tool_uses": [],
        "variants": {
            "baseline": {
                "response": "科幻(fiction)新书推荐中。",
                "tool_uses": []
            },
            "robust": {
                "response": '{"category":"fiction","answer":"新书推荐"}',
                "tool_uses": []
            },
            "ineffective": {
                "response": "科幻(fiction)新书推荐中。",
                "tool_uses": []
            },
            "overfit": {
                "response": '{"category":"history","answer":"历史新书"}',
                "tool_uses": []
            },
        },
    },
    {
        "eval_id": "val_stable_membership",
        "split": "validation",
        "critical": False,
        "expected_category": "format_violation",
        "query": "办借书证需要什么条件？",
        "expected_response": "category",
        "expected_tool_uses": [],
        "variants": {
            "baseline": {
                "response": "凭身份证免费办理。",
                "tool_uses": []
            },
            "robust": {
                "response": '{"category":"faq","answer":"凭身份证免费办理"}',
                "tool_uses": [],
            },
            "ineffective": {
                "response": "凭身份证免费办理。",
                "tool_uses": []
            },
            "overfit": {
                "response": '{"category":"faq","answer":"凭身份证免费办理"}',
                "tool_uses": [],
            },
        },
    },
]

# 候选 id → prompt 文件名（agent/prompts/ 下）。baseline 用 system.md。
CANDIDATE_PROMPTS: dict[str, str] = {
    "baseline": "system.md",
    "robust": "candidate_robust.md",
    "ineffective": "candidate_ineffective.md",
    "overfit": "candidate_overfit.md",
}


def _invocation(query: str, response: str, tool_uses: list[ToolUse]) -> dict[str, Any]:
    """构造一个 Invocation 的 dict（供 EvalSet.model_validate 递归解析）。"""
    inv: dict[str, Any] = {
        "invocation_id": "inv-1",
        "user_content": {
            "parts": [{
                "text": query
            }],
            "role": "user"
        },
        "final_response": {
            "parts": [{
                "text": response
            }],
            "role": "model"
        },
        "creation_timestamp": 0.0,
    }
    if tool_uses:
        inv["intermediate_data"] = {
            "tool_uses": [{
                "id": f"t{i}",
                "name": t["name"],
                "args": t["args"]
            } for i, t in enumerate(tool_uses)]
        }
    return inv


def build_trace_eval_set(cases: list[dict[str, Any]], variant: Variant, split: Split) -> EvalSet:
    """构造 trace 模式 EvalSet：actual_conversation = 该 variant 的预录制输出，
    conversation = expected（标准答案）。trace 模式跳过 agent，直接用 actual 评分。
    """
    selected = [c for c in cases if c["split"] == split]
    eval_cases = []
    for c in selected:
        v: VariantOutput = c["variants"][variant]
        eval_cases.append({
            "eval_id": c["eval_id"],
            "eval_mode": "trace",
            "actual_conversation": [_invocation(c["query"], v["response"], v["tool_uses"])],
            "conversation": [_invocation(c["query"], c["expected_response"], c["expected_tool_uses"])],
            "session_input": {
                "app_name": "eval_optimize_loop",
                "user_id": "user",
                "state": {},
            },
        })
    return EvalSet.model_validate({"eval_set_id": f"eol_{split}_{variant}", "eval_cases": eval_cases})


def cases_for_split(cases: list[dict[str, Any]], split: Split) -> list[dict[str, Any]]:
    return [c for c in cases if c["split"] == split]
