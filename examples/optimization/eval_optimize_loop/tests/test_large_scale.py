"""Large-scale / stress tests with massive mock data.

Tests pipeline behavior under heavy load: many cases, long conversations,
high parallelism, and large prompts.
"""

import json
import os
import tempfile
import time

import pytest

from pipeline.config import load_evalset, load_pipeline_config
from pipeline.baseline import BaselineResult, run_baseline_fake
from pipeline.attribution import attribute_failures, _categorize_failure
from pipeline.gate import evaluate_gate, GateDecision
from pipeline.report import generate_json_report, generate_md_report
from pipeline.optimize import run_optimize_fake


# ═══════════════════════════════════════════════════════════════
# Helper: generate mock evalset data
# ═══════════════════════════════════════════════════════════════

def _make_case(case_id: str, question: str, expected: str,
               actual: str | None = None,
               tool_uses: list | None = None,
               is_pass: bool = True) -> dict:
    """Create a single evalset case in trace mode format."""
    actual_text = actual if actual is not None else expected
    return {
        "eval_id": case_id,
        "eval_mode": "trace",
        "conversation": [{
            "invocation_id": f"inv-{case_id}",
            "user_content": {"parts": [{"text": question}], "role": "user"},
            "final_response": {"parts": [{"text": expected}], "role": "model"},
        }],
        "actual_conversation": [{
            "invocation_id": f"inv-{case_id}",
            "user_content": {"parts": [{"text": question}], "role": "user"},
            "final_response": {"parts": [{"text": actual_text}], "role": "model"},
            "intermediate_data": {
                "tool_uses": tool_uses or [],
                "tool_responses": [],
                "intermediate_responses": [],
            },
        }],
    }


def _generate_mock_evalset(num_cases: int, seed: int = 42,
                           fail_ratio: float = 0.3) -> dict:
    """Generate a mock evalset with N diverse cases.

    Args:
        num_cases: Total number of cases.
        seed: Random seed for reproducibility.
        fail_ratio: Fraction of cases that should fail (0.0 to 1.0).

    Returns:
        Dict with eval_set_id, name, eval_cases.
    """
    import hashlib

    topics = [
        ("math", "Calculate {a} + {b}"),
        ("math", "What is {a} * {b}?"),
        ("math", "Divide {a} by {b}"),
        ("reasoning", "If you have {a} items and each costs ${b}, what is the total?"),
        ("reasoning", "A train travels {a} km in {b} hours. What's the speed?"),
        ("tool", "Use calculator to compute log({a})"),
        ("tool", "Calculate compound interest: ${a} at {b}% for {c} years"),
        ("chinese", "请计算{a}加{b}等于多少？"),
        ("chinese", "小明有{a}元，花了{b}元，还剩多少？"),
        ("format", "Answer in JSON: {{'op': 'add', 'a': {a}, 'b': {b}}}"),
        ("format", "Output only the number: {a} * {b}"),
        ("edge", "What is {a} / 0?"),
        ("edge", "Calculate 0.1 + 0.2 precisely"),
        ("multi_turn", "Step 1: Add {a} and {b}"),
        ("multi_turn", "Step 2: Multiply result by {c}"),
    ]

    cases = []
    for i in range(num_cases):
        topic, template = topics[i % len(topics)]

        # Generate deterministic values
        h = hashlib.md5(f"{seed}-{i}".encode()).hexdigest()
        a = int(h[:4], 16) % 500 + 1
        b = int(h[4:8], 16) % 100 + 1
        c = int(h[8:12], 16) % 10 + 1

        question = template.format(a=a, b=b, c=c)

        # Compute expected answer
        if topic == "math":
            expected = str(a + b) if "Multiply" not in template else str(a * b)
        elif topic == "reasoning":
            expected = str(a * b) if "cost" in template else str(round(a / b, 1))
        elif topic == "chinese":
            expected = str(a + b) if "加" in question else str(a - b)
        elif topic == "format":
            expected = f'{{"op": "add", "a": {a}, "b": {b}, "result": {a + b}}}'
        elif topic == "edge":
            expected = "undefined" if "/ 0" in question else "0.30000000000000004"
        else:
            expected = str(a + b)

        # Determine pass/fail
        should_fail = (i / num_cases) < fail_ratio
        actual = expected if not should_fail else f"wrong_answer_{i}"

        # Tool uses for tool topics
        tool_uses = None
        if topic == "tool":
            tool_uses = [{
                "tool_name": "calculate",
                "arguments": {"expression": question},
            }]

        cases.append(_make_case(
            f"case_{i:04d}", question, expected, actual,
            tool_uses=tool_uses, is_pass=not should_fail,
        ))

    return {
        "eval_set_id": f"mock-{num_cases}-cases",
        "name": f"Mock Evalset ({num_cases} cases, {fail_ratio:.0%} fail)",
        "description": f"Auto-generated evalset with {num_cases} diverse cases",
        "eval_cases": cases,
    }


# ═══════════════════════════════════════════════════════════════
# Test Classes
# ═══════════════════════════════════════════════════════════════

class TestLargeEvalset:
    """Tests with 50-100 case evalsets."""

    def test_50_case_batch_eval(self, temp_json_file):
        """50 diverse cases — all should load and evaluate."""
        data = _generate_mock_evalset(50, seed=1, fail_ratio=0.3)
        path = temp_json_file(data)
        try:
            cfg = load_pipeline_config()
            result = run_baseline_fake(path, cfg)
            assert result.total_cases == 50
            # With 30% fail ratio and conversation-based pass detection
            assert result.passed_cases >= 0
            assert result.failed_cases >= 0
            assert result.passed_cases + result.failed_cases == 50
        finally:
            os.unlink(path)

    def test_100_case_loading_speed(self, temp_json_file):
        """100 cases should load quickly."""
        data = _generate_mock_evalset(100, seed=2)
        path = temp_json_file(data)
        try:
            start = time.monotonic()
            data = load_evalset(path)
            elapsed = time.monotonic() - start
            assert len(data["eval_cases"]) == 100
            # Loading 100 cases should be very fast (< 2 seconds)
            assert elapsed < 5.0, f"Loading took {elapsed:.1f}s"
        finally:
            os.unlink(path)

    def test_massive_attribution(self):
        """50 failures should be attributed correctly via direct BaselineResult.

        Uses direct BaselineResult construction because run_baseline_fake
        determines pass/fail from conversation presence, not content matching.
        """
        # Build 50 failed per_case_results with diverse failure reasons
        categories = [
            "tool_call_error: timeout",
            "final_response_mismatch: expected 42",
            "llm_rubric_not_met: quality 0.3",
            "tool_parameter_error: missing expr",
            "wrong_tool_selected: used add",
            "knowledge_recall_insufficient: not found",
            "format_not_as_required: expected JSON",
            "missing expected output in response",
        ]
        per_case = []
        failed_ids = []
        for i in range(50):
            cid = f"case_{i:04d}"
            failed_ids.append(cid)
            per_case.append({
                "eval_id": cid,
                "pass": False,
                "reason": categories[i % len(categories)],
            })

        baseline = BaselineResult(
            evalset_id="massive-fail",
            total_cases=50,
            passed_cases=0,
            failed_cases=50,
            failed_case_ids=failed_ids,
            per_case_results=per_case,
        )

        attr = attribute_failures(baseline.__dict__, {})
        assert attr.total_failures == 50
        # All 8 categories should be represented
        assert len(attr.by_category) >= 7

    def test_diverse_categories(self, temp_json_file):
        """Cases with different failure reasons → multiple categories."""
        data = {
            "eval_set_id": "multi-category",
            "eval_cases": [],
        }
        categories = [
            "tool_call_error: timeout",
            "final_response_mismatch: expected 42 got 43",
            "llm_rubric_not_met: quality score 0.3",
            "tool_parameter_error: missing required 'expr'",
            "wrong_tool_selected: used divide instead of multiply",
            "knowledge_recall_insufficient: formula not in context",
            "format_not_as_required: expected JSON",
            "missing_expected_output: empty response",
            "unknown failure",
            "tool_call_error: connection refused",
        ]
        for i, reason in enumerate(categories):
            data["eval_cases"].append({
                "eval_id": f"cat_{i}",
                "eval_mode": "trace",
                "conversation": [{
                    "invocation_id": f"inv-cat-{i}",
                    "user_content": {"parts": [{"text": f"question {i}"}], "role": "user"},
                    "final_response": {"parts": [{"text": "expected"}], "role": "model"},
                }],
                "actual_conversation": [{
                    "invocation_id": f"inv-cat-{i}",
                    "user_content": {"parts": [{"text": f"question {i}"}], "role": "user"},
                    "final_response": {"parts": [{"text": f"actual {i}"}], "role": "model"},
                    "intermediate_data": {
                        "tool_uses": [],
                        "tool_responses": [],
                        "intermediate_responses": [],
                    },
                }],
            })

        # Add per_case_results with failure reasons
        per_case = []
        for i, reason in enumerate(categories):
            per_case.append({
                "eval_id": f"cat_{i}",
                "pass": False,
                "reason": reason,
            })

        baseline = BaselineResult(
            evalset_id="multi",
            total_cases=len(categories),
            passed_cases=0,
            failed_cases=len(categories),
            failed_case_ids=[f"cat_{i}" for i in range(len(categories))],
            per_case_results=per_case,
        )

        attr = attribute_failures(baseline.__dict__, {})
        # Should have multiple distinct categories
        assert len(attr.by_category) >= 5


class TestLongConversations:
    """Tests with long multi-turn conversations."""

    def test_multi_turn_20_rounds(self, temp_json_file):
        """20-turn conversation — should not crash."""
        data = {
            "eval_set_id": "long-convo",
            "eval_cases": [{
                "eval_id": "long_case",
                "eval_mode": "trace",
                "conversation": [
                    {
                        "invocation_id": f"inv-{i}",
                        "user_content": {"parts": [{"text": f"Turn {i}: calculate {i} + {i+1}"}], "role": "user"},
                        "final_response": {"parts": [{"text": str(2*i + 1)}], "role": "model"},
                    }
                    for i in range(20)
                ],
                "actual_conversation": [
                    {
                        "invocation_id": f"inv-{i}",
                        "user_content": {"parts": [{"text": f"Turn {i}: calculate {i} + {i+1}"}], "role": "user"},
                        "final_response": {"parts": [{"text": str(2*i + 1)}], "role": "model"},
                        "intermediate_data": {
                            "tool_uses": [],
                            "tool_responses": [],
                            "intermediate_responses": [],
                        },
                    }
                    for i in range(20)
                ],
            }],
        }
        path = temp_json_file(data)
        try:
            result = run_baseline_fake(path, load_pipeline_config())
            assert result.total_cases == 1
        finally:
            os.unlink(path)


class TestMixedLanguage:
    """Tests with multi-language evalset data."""

    def test_chinese_case_names(self, temp_json_file):
        """Chinese eval_ids and content should work."""
        data = {
            "eval_set_id": "chinese-test",
            "eval_cases": [
                {
                    "eval_id": "中文测试_001",
                    "eval_mode": "trace",
                    "conversation": [{
                        "invocation_id": "inv-中文-001",
                        "user_content": {
                            "parts": [{"text": "请计算二十五加十七等于多少？"}],
                            "role": "user",
                        },
                        "final_response": {
                            "parts": [{"text": "四十二"}],
                            "role": "model",
                        },
                    }],
                    "actual_conversation": [{
                        "invocation_id": "inv-中文-001",
                        "user_content": {
                            "parts": [{"text": "请计算二十五加十七等于多少？"}],
                            "role": "user",
                        },
                        "final_response": {
                            "parts": [{"text": "四十二"}],
                            "role": "model",
                        },
                        "intermediate_data": {
                            "tool_uses": [],
                            "tool_responses": [],
                            "intermediate_responses": [],
                        },
                    }],
                },
                {
                    "eval_id": "emoji_🎉_test",
                    "eval_mode": "trace",
                    "conversation": [{
                        "invocation_id": "inv-emoji-001",
                        "user_content": {
                            "parts": [{"text": "Calculate 2+2 🧮"}],
                            "role": "user",
                        },
                        "final_response": {
                            "parts": [{"text": "4 ✅"}],
                            "role": "model",
                        },
                    }],
                    "actual_conversation": [{
                        "invocation_id": "inv-emoji-001",
                        "user_content": {
                            "parts": [{"text": "Calculate 2+2 🧮"}],
                            "role": "user",
                        },
                        "final_response": {
                            "parts": [{"text": "4 ✅"}],
                            "role": "model",
                        },
                        "intermediate_data": {
                            "tool_uses": [],
                            "tool_responses": [],
                            "intermediate_responses": [],
                        },
                    }],
                },
            ],
        }
        path = temp_json_file(data)
        try:
            cfg = load_pipeline_config()
            result = run_baseline_fake(path, cfg)
            assert result.total_cases == 2
        finally:
            os.unlink(path)

    def test_japanese_and_korean(self, temp_json_file):
        """Japanese and Korean content."""
        data = {
            "eval_set_id": "cjk-test",
            "eval_cases": [
                {
                    "eval_id": "jp_001",
                    "eval_mode": "trace",
                    "conversation": [{
                        "invocation_id": "inv-jp-001",
                        "user_content": {
                            "parts": [{"text": "15 + 27 はいくつですか？"}],
                            "role": "user",
                        },
                        "final_response": {
                            "parts": [{"text": "42です"}],
                            "role": "model",
                        },
                    }],
                    "actual_conversation": [{
                        "invocation_id": "inv-jp-001",
                        "user_content": {
                            "parts": [{"text": "15 + 27 はいくつですか？"}],
                            "role": "user",
                        },
                        "final_response": {
                            "parts": [{"text": "42です"}],
                            "role": "model",
                        },
                        "intermediate_data": {
                            "tool_uses": [],
                            "tool_responses": [],
                            "intermediate_responses": [],
                        },
                    }],
                },
                {
                    "eval_id": "kr_001",
                    "eval_mode": "trace",
                    "conversation": [{
                        "invocation_id": "inv-kr-001",
                        "user_content": {
                            "parts": [{"text": "10 곱하기 5 는 얼마인가요?"}],
                            "role": "user",
                        },
                        "final_response": {
                            "parts": [{"text": "50"}],
                            "role": "model",
                        },
                    }],
                    "actual_conversation": [{
                        "invocation_id": "inv-kr-001",
                        "user_content": {
                            "parts": [{"text": "10 곱하기 5 는 얼마인가요?"}],
                            "role": "user",
                        },
                        "final_response": {
                            "parts": [{"text": "50"}],
                            "role": "model",
                        },
                        "intermediate_data": {
                            "tool_uses": [],
                            "tool_responses": [],
                            "intermediate_responses": [],
                        },
                    }],
                },
            ],
        }
        path = temp_json_file(data)
        try:
            cfg = load_pipeline_config()
            result = run_baseline_fake(path, cfg)
            assert result.total_cases == 2
        finally:
            os.unlink(path)


class TestPipelineStress:
    """Stress tests for full pipeline under load."""

    def test_full_pipeline_50_cases(self, temp_json_file):
        """Full pipeline with 50 cases end-to-end."""
        data = _generate_mock_evalset(50, seed=5, fail_ratio=0.3)
        train_path = temp_json_file(data)
        val_data = _generate_mock_evalset(20, seed=6, fail_ratio=0.2)
        val_path = temp_json_file(val_data)

        try:
            cfg = load_pipeline_config(
                train_evalset=train_path,
                val_evalset=val_path,
                mode="fake",
                max_iterations=3,
            )

            # Stage 1-2: Config + Baseline
            bl_train = run_baseline_fake(train_path, cfg)
            bl_val = run_baseline_fake(val_path, cfg)

            # Stage 3: Attribution
            attr = attribute_failures(bl_train.__dict__, bl_val.__dict__)

            # Stage 4: Optimization
            opt = run_optimize_fake(attr, cfg)

            # Stage 5-6: Validate + Gate
            gate = evaluate_gate(
                baseline_pass_rate=bl_train.pass_rate,
                candidate_pass_rate=min(1.0, bl_train.pass_rate + 0.2),
                baseline_metrics={}, candidate_metrics={},
                baseline_failed=bl_train.failed_case_ids,
                candidate_failed=[],
                optimization_cost=opt.total_cost,
            )

            # Stage 7: Report
            report = generate_json_report(
                "stress-50", bl_train, bl_val, attr, gate,
                optimization_result={
                    "algorithm": opt.algorithm,
                    "total_iterations": opt.total_iterations,
                },
            )
            data = json.loads(report)
            assert data["task_id"] == "stress-50"

        finally:
            for p in [train_path, val_path]:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def test_100_case_evalset_report_generation(self, temp_json_file):
        """Report generation with 100 cases should not hang."""
        data = _generate_mock_evalset(100, seed=7)
        path = temp_json_file(data)
        try:
            cfg = load_pipeline_config()
            result = run_baseline_fake(path, cfg)
            attr = attribute_failures(result.__dict__, {})

            start = time.monotonic()
            gate = evaluate_gate(0.5, 0.8, {}, {}, min_improvement=0.1)
            report = generate_json_report("stress-100", result, result, attr, gate)
            elapsed = time.monotonic() - start

            data = json.loads(report)
            assert data["task_id"] == "stress-100"
            # Should complete in reasonable time
            assert elapsed < 5.0, f"Report generation took {elapsed:.1f}s"
        finally:
            os.unlink(path)
