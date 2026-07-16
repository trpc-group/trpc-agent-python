# evaluate.py —— 量化评测脚本
"""
Task 14: 量化评测 evaluate + 隐藏集 + README
功能：跑全部 fixture 算 P/R/F1，卡阈值（检出≥0.80/误报≤0.15/脱敏≥0.95）

评测流程：
1. 加载所有 fixture diff 文件（8公开 + ~12隐藏）
2. 对每个 fixture 运行 pipeline.run_review 获取审查报告
3. 与 expected_findings.json 中的 ground-truth 比较
4. 计算 TP/FP/FN，得出精确率/召回率/F1分数
5. 检查脱敏率（敏感信息是否被正确脱敏）
6. 验证是否达到阈值要求
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Any, Optional

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(Path(__file__).parent))

from agent.models import Finding
from agent.pipeline import run_review


def find_env_file() -> Optional[str]:
    """自动探测 .env 文件

    优先级顺序：
    1. examples/skills_code_review_agent/.env
    2. examples/quickstart/.env
    3. 其他 examples/*/.env（含 OPENAI_API_KEY 或 TRPC_AGENT_API_KEY）

    Returns:
        找到的 .env 文件路径，若未找到返回 None
    """
    # 候选 .env 文件路径
    candidate_paths = [
        Path(__file__).parent / ".env",  # 当前目录 .env
        Path(__file__).parent.parent / "quickstart" / ".env",  # quickstart/.env
    ]

    # 扫描其他 examples 目录下的 .env 文件
    examples_dir = Path(__file__).parent.parent
    if examples_dir.exists():
        for env_file in examples_dir.glob("*/.env"):
            if env_file not in candidate_paths:
                candidate_paths.append(env_file)

    # 按优先级检查文件存在性和内容
    for env_path in candidate_paths:
        if not env_path.exists():
            continue

        # 检查是否包含目标 API Key
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if "OPENAI_API_KEY" in content or "TRPC_AGENT_API_KEY" in content:
                    return str(env_path)
        except (IOError, OSError):
            continue

    return None


def load_env_file(env_file: str) -> bool:
    """从 .env 文件加载环境变量

    Args:
        env_file: .env 文件路径

    Returns:
        加载成功返回 True，失败返回 False
    """
    try:
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # 跳过空行和注释
                if not line or line.startswith('#'):
                    continue

                # 解析 KEY=VALUE 格式
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()

                    # 只设置 LLM 相关的环境变量
                    if key in [
                            "OPENAI_API_KEY", "TRPC_AGENT_API_KEY", "OPENAI_BASE_URL", "TRPC_AGENT_BASE_URL",
                            "MODEL_NAME", "TRPC_AGENT_MODEL_NAME"
                    ]:
                        os.environ[key] = value

        # 验证是否成功加载 API Key
        has_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("TRPC_AGENT_API_KEY"))
        return has_key

    except (IOError, OSError) as e:
        print(f"[WARN] 无法读取 .env 文件: {str(e)}")
        return False


class EvaluationResult:
    """评测结果类"""

    def __init__(self):
        self.tp = 0  # True Positive
        self.fp = 0  # False Positive
        self.fn = 0  # False Negative
        self.findings_by_rule = defaultdict(list)
        self.expected_by_rule = defaultdict(list)
        self.redaction_check = {"total_sensitive": 0, "redacted_count": 0, "redaction_rate": 0.0}
        self.fixture_results = {}
        self.errors = []

    def add_fixture_result(self, fixture_name: str, result: Dict[str, Any]):
        """添加单个fixture的评测结果"""
        self.fixture_results[fixture_name] = result

    def calculate_metrics(self) -> Dict[str, float]:
        """计算评测指标"""
        precision = self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0
        recall = self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
        false_positive_rate = self.fp / (self.fp + self.tp) if (self.fp + self.tp) > 0 else 0.0

        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "false_positive_rate": false_positive_rate,
            "true_positives": self.tp,
            "false_positives": self.fp,
            "false_negatives": self.fn,
            "redaction_rate": self.redaction_check["redaction_rate"]
        }


def load_expected_findings() -> Dict[str, Any]:
    """加载期望的评测结果"""
    expected_file = Path(__file__).parent / "fixtures" / "expected_findings.json"
    with open(expected_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_fixture_diff(fixture_name: str) -> str:
    """加载fixture diff内容"""
    diff_file = Path(__file__).parent / "fixtures" / "diffs" / f"{fixture_name}.diff"
    if not diff_file.exists():
        raise FileNotFoundError(f"Fixture diff文件不存在: {diff_file}")

    with open(diff_file, 'r', encoding='utf-8') as f:
        return f.read()


def evaluate_fixture(fixture_name: str, expected_data: Dict[str, Any], use_llm: bool = False) -> Dict[str, Any]:
    """评估单个fixture

    Args:
        fixture_name: fixture名称
        expected_data: 该fixture的期望数据
        use_llm: 是否使用真实 LLM 模式

    Returns:
        该fixture的评测结果
    """
    print(f"\n{'='*60}")
    print(f"评测fixture: {fixture_name}")
    if use_llm:
        print("模式: 真实 LLM 模式 (LLM 补召回)")
    else:
        print("模式: Dry-run 模式 (预录制数据)")
    print(f"{'='*60}")

    try:
        # 加载diff内容
        diff_text = load_fixture_diff(fixture_name)
        print("[OK] 成功加载diff文件")

        # 运行审查管线
        report = run_review(
            diff_text=diff_text,
            repo="https://github.com/test/repo",
            sandbox="fake",
            dry_run=not use_llm,  # llm=True 时 dry_run=False
            llm=use_llm)  # 传递 llm 参数
        mode_str = "LLM 增强" if use_llm else "基础"
        print(f"[OK] 完成审查（{mode_str}模式），发现 {len(report.findings)} 个findings")

        # 获取期望的rule_id集合和实例数据
        expected_rule_ids = set(expected_data.get("expected_rule_ids", []))
        expected_count = expected_data.get("expected_findings_count", 0)
        expected_instances = expected_data.get("expected_instances", {})

        # Fix 3 (issue #92): 分桶统计，正确处理 needs_review 桶
        # 设计意图：needs_review 是"低置信度，不确定，交人工复核"，不是"误报"
        # - findings + warnings 桶：正常算 TP/FP
        # - needs_review 桶：命中 expected 算 TP，未命中不算 FP（设计为"待确认"）

        # 分别获取三个桶的 findings
        findings_findings = list(report.findings)  # confidence >= 0.8
        warnings_findings = list(report.warnings)  # 0.55 <= confidence < 0.8
        needs_review_findings = list(report.needs_human_review)  # confidence < 0.55

        # 高置信度桶（findings + warnings）：正常算 TP/FP
        high_confidence_findings = findings_findings + warnings_findings

        # 所有实际检测（用于召回率计算）：包含三个桶
        all_actual_findings = high_confidence_findings + needs_review_findings
        actual_rule_ids = set(finding.rule_id for finding in all_actual_findings)

        # 检查脱敏情况（检查所有 findings）
        redaction_check = check_redaction(fixture_name, all_actual_findings, expected_data)

        # 实例级匹配：分别统计每个桶的实例数
        high_conf_instances = {}
        for finding in high_confidence_findings:
            rule_id = finding.rule_id
            high_conf_instances[rule_id] = high_conf_instances.get(rule_id, 0) + 1

        needs_review_instances = {}
        for finding in needs_review_findings:
            rule_id = finding.rule_id
            needs_review_instances[rule_id] = needs_review_instances.get(rule_id, 0) + 1

        # 合并实例统计（用于整体分析）
        all_instances = {}
        for finding in all_actual_findings:
            rule_id = finding.rule_id
            all_instances[rule_id] = all_instances.get(rule_id, 0) + 1

        # 计算实例级TP/FP/FN
        tp = 0  # True Positive: 正确检测到的实例数
        fp = 0  # False Positive: 错误检测的实例数（仅高置信度桶）
        fn = 0  # False Negative: 遗漏的实例数

        # 如果没有期望实例，直接返回（避免除零错误）
        if not expected_instances:
            # 只有高置信度桶的实际检测算 FP（needs_review 不算 FP）
            fp = len(high_confidence_findings)
            # 构造结果（特殊处理无期望的情况）
            result = {
                "fixture_name": fixture_name,
                "description": expected_data.get("description", ""),
                "expected_rule_ids": list(expected_rule_ids),
                "actual_rule_ids": list(actual_rule_ids),
                "expected_instances": expected_instances,
                "actual_instances": all_instances,
                "high_confidence_instances": high_conf_instances,
                "needs_review_instances": needs_review_instances,
                "expected_count": 0,
                "actual_count": len(all_actual_findings),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": 0.0 if fp > 0 else 1.0,  # 无期望但有实际检测，算0精确率
                "recall": 0.0,  # 无期望，召回率为0
                "f1": 0.0,
                "redaction_rate": redaction_check["redaction_rate"],
                "note": expected_data.get("note", ""),
                "success": True
            }
            return result

        # 对每个期望的 rule_id 计算实例级匹配
        for rule_id, expected_count in expected_instances.items():
            # 高置信度桶的实例数
            high_conf_count = high_conf_instances.get(rule_id, 0)
            # needs_review 桶的实例数
            review_count = needs_review_instances.get(rule_id, 0)
            # 总实际检测数
            actual_count = high_conf_count + review_count

            if actual_count >= expected_count:
                # 检测到足够的实例，优先从高置信度桶算 TP，不足的从 needs_review 桶补
                tp += expected_count
                # 高置信度桶多检测的实例算 FP
                if high_conf_count > expected_count:
                    fp += (high_conf_count - expected_count)
            else:
                # 检测不足：已检测的都算 TP，未检测的算 FN
                tp += actual_count
                fn += (expected_count - actual_count)

        # 处理未在期望中但实际检测到的 rule_id
        # 高置信度桶的：全部算 FP（因为明确报告为问题）
        for rule_id, actual_count in high_conf_instances.items():
            if rule_id not in expected_instances:
                fp += actual_count

        # needs_review 桶的：不算 FP（设计为"待确认"，不是误报）
        # 注意：已在上面循环中通过 expected_instances 检查处理，命中 expected 的已算 TP

        # 计算 precision, recall, F1（加强除零保护）
        precision_val = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall_val = tp / (tp + fn) if (tp + fn) > 0 else 0.0

        # F1 计算：额外检查 precision+recall 避免除零（Fix 1: 修复 db_lifecycle 除零bug）
        if (precision_val + recall_val) > 0:
            f1_val = 2 * (precision_val * recall_val) / (precision_val + recall_val)
        else:
            f1_val = 0.0

        # 构造结果
        result = {
            "fixture_name": fixture_name,
            "description": expected_data.get("description", ""),
            "expected_rule_ids": list(expected_rule_ids),
            "actual_rule_ids": list(actual_rule_ids),
            "expected_instances": expected_instances,
            "actual_instances": all_instances,
            "high_confidence_instances": high_conf_instances,
            "needs_review_instances": needs_review_instances,
            "expected_count": expected_count,
            "actual_count": len(all_actual_findings),
            "high_confidence_count": len(high_confidence_findings),
            "needs_review_count": len(needs_review_findings),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision_val,
            "recall": recall_val,
            "f1": f1_val,
            "redaction_rate": redaction_check["redaction_rate"],
            "note": expected_data.get("note", ""),
            "success": True
        }

        # 打印结果
        print(f"期望规则: {expected_rule_ids}")
        print(f"期望实例: {expected_instances}")
        print(f"实际检测到: {actual_rule_ids}")
        print(f"实际实例: {all_instances}")
        print(f"高置信度实例: {high_conf_instances}")
        print(f"待复核实例: {needs_review_instances}")
        print(f"TP={tp}, FP={fp}, FN={fn}")
        print(f"精确率: {result['precision']:.3f}")
        print(f"召回率: {result['recall']:.3f}")
        print(f"F1分数: {result['f1']:.3f}")
        print(f"脱敏率: {result['redaction_rate']:.3f}")

        return result

    except Exception as e:
        print(f"[FAIL] 评测失败: {str(e)}")
        return {
            "fixture_name": fixture_name,
            "description": expected_data.get("description", ""),
            "error": str(e),
            "success": False
        }


def check_redaction(fixture_name: str, findings: List[Finding], expected_data: Dict[str, Any]) -> Dict[str, Any]:
    """检查脱敏情况

    Args:
        fixture_name: fixture名称
        findings: 实际检测到的findings
        expected_data: 期望数据

    Returns:
        脱敏检查结果
    """
    # 统计敏感信息检查
    total_sensitive = 0
    redacted_count = 0

    for finding in findings:
        if finding.category == "sensitive_information":
            total_sensitive += 1
            # 检查evidence是否被脱敏
            evidence = finding.evidence or ""
            if "***" in evidence or "REDACTED" in evidence:
                redacted_count += 1

    redaction_rate = redacted_count / total_sensitive if total_sensitive > 0 else 1.0

    return {"total_sensitive": total_sensitive, "redacted_count": redacted_count, "redaction_rate": redaction_rate}


def run_evaluation(use_llm: bool = False) -> EvaluationResult:
    """运行完整评测

    Args:
        use_llm: 是否使用真实 LLM 模式

    Returns:
        评测结果
    """
    print("=" * 60)
    if use_llm:
        print("开始代码审查Agent量化评测（真实 LLM 模式）")
    else:
        print("开始代码审查Agent量化评测（Dry-run 模式）")
    print("=" * 60)

    # 初始化评测结果
    eval_result = EvaluationResult()

    # 加载期望数据
    expected_findings = load_expected_findings()
    print("[OK] 加载期望数据")

    # 评测公开集
    print("\n## 评测公开集 ##")
    public_fixtures = expected_findings.get("public_fixtures", {})
    for fixture_name, expected_data in public_fixtures.items():
        result = evaluate_fixture(fixture_name, expected_data, use_llm=use_llm)
        eval_result.add_fixture_result(fixture_name, result)

        if result["success"]:
            eval_result.tp += result["tp"]
            eval_result.fp += result["fp"]
            eval_result.fn += result["fn"]
        else:
            eval_result.errors.append(f"{fixture_name}: {result.get('error', 'Unknown error')}")

    # 评测隐藏集
    print("\n## 评测隐藏集 ##")
    hidden_fixtures = expected_findings.get("hidden_fixtures", {})
    for fixture_name, expected_data in hidden_fixtures.items():
        result = evaluate_fixture(fixture_name, expected_data, use_llm=use_llm)
        eval_result.add_fixture_result(fixture_name, result)

        if result["success"]:
            eval_result.tp += result["tp"]
            eval_result.fp += result["fp"]
            eval_result.fn += result["fn"]
        else:
            eval_result.errors.append(f"{fixture_name}: {result.get('error', 'Unknown error')}")

    # 计算总体脱敏率
    total_sensitive = sum(
        result.get("redaction_rate", 0.0) > 0 for result in eval_result.fixture_results.values()
        if result.get("success"))
    if total_sensitive > 0:
        avg_redaction_rate = sum(
            result.get("redaction_rate", 0.0)
            for result in eval_result.fixture_results.values() if result.get("success")) / total_sensitive
        eval_result.redaction_check["redaction_rate"] = avg_redaction_rate

    return eval_result


def check_thresholds(eval_result: EvaluationResult, expected_findings: Dict[str, Any]) -> Dict[str, bool]:
    """检查是否达到阈值要求

    Args:
        eval_result: 评测结果
        expected_findings: 期望数据

    Returns:
        各项阈值的检查结果
    """
    metrics = eval_result.calculate_metrics()
    thresholds = expected_findings.get("evaluation_thresholds", {}).get("overall", {})

    recall_threshold = thresholds.get("recall_threshold", 0.80)
    precision_threshold = thresholds.get("precision_threshold", 0.80)
    fpr_threshold = thresholds.get("false_positive_rate_threshold", 0.15)
    redaction_threshold = thresholds.get("redaction_rate_threshold", 0.95)

    checks = {
        "recall": metrics["recall"] >= recall_threshold,
        "precision": metrics["precision"] >= precision_threshold,
        "false_positive_rate": metrics["false_positive_rate"] <= fpr_threshold,
        "redaction_rate": metrics["redaction_rate"] >= redaction_threshold
    }

    return checks


def print_evaluation_report(eval_result: EvaluationResult, expected_findings: Dict[str, Any]):
    """打印评测报告

    Args:
        eval_result: 评测结果
        expected_findings: 期望数据
    """
    print(f"\n{'='*60}")
    print("## 评测报告 ##")
    print(f"{'='*60}")

    # 打印总体指标
    metrics = eval_result.calculate_metrics()
    print("\n### 总体指标 ###")
    print(f"精确率 (Precision): {metrics['precision']:.3f}")
    print(f"召回率 (Recall): {metrics['recall']:.3f}")
    print(f"F1分数: {metrics['f1']:.3f}")
    print(f"误报率 (FPR): {metrics['false_positive_rate']:.3f}")
    print(f"脱敏率: {metrics['redaction_rate']:.3f}")
    print(f"TP/FN/FP: {metrics['true_positives']}/{metrics['false_negatives']}/{metrics['false_positives']}")

    # 检查阈值
    checks = check_thresholds(eval_result, expected_findings)
    print("\n### 阈值检查 ###")
    for check_name, passed in checks.items():
        status = "[OK] PASS" if passed else "[FAIL] FAIL"
        print(f"{check_name}: {status}")

    # 打印各fixture详细结果
    print("\n### Fixture详细结果 ###")
    for fixture_name, result in eval_result.fixture_results.items():
        if result["success"]:
            print(f"\n{fixture_name}:")
            print(f"  精确率: {result['precision']:.3f}")
            print(f"  召回率: {result['recall']:.3f}")
            print(f"  F1分数: {result['f1']:.3f}")
            if result.get("note"):
                print(f"  备注: {result['note']}")
        else:
            print(f"\n{fixture_name}: 失败 - {result.get('error', 'Unknown error')}")

    # 打印错误信息
    if eval_result.errors:
        print("\n### 错误信息 ###")
        for error in eval_result.errors:
            print(f"  - {error}")

    # 最终结论
    all_passed = all(checks.values())
    print(f"\n{'='*60}")
    if all_passed:
        print("[SUCCESS] 评测通过！所有指标均达到阈值要求。")
    else:
        print("[WARN]  评测未通过，部分指标未达到阈值要求。")
    print(f"{'='*60}")


def save_evaluation_report(eval_result: EvaluationResult, expected_findings: Dict[str, Any]):
    """保存评测报告到文件

    Args:
        eval_result: 评测结果
        expected_findings: 期望数据
    """
    metrics = eval_result.calculate_metrics()
    checks = check_thresholds(eval_result, expected_findings)

    report = {
        "summary": {
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1": metrics["f1"],
            "false_positive_rate": metrics["false_positive_rate"],
            "redaction_rate": metrics["redaction_rate"],
            "true_positives": metrics["true_positives"],
            "false_positives": metrics["false_positives"],
            "false_negatives": metrics["false_negatives"],
            "threshold_checks": checks,
            "all_passed": all(checks.values())
        },
        "fixture_results": eval_result.fixture_results,
        "errors": eval_result.errors
    }

    # 保存JSON报告
    output_dir = Path(__file__).parent / "outputs"
    output_dir.mkdir(exist_ok=True)
    report_file = output_dir / "evaluation_report.json"

    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"[OK] 评测报告已保存到: {report_file}")


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="代码审查Agent量化评测")
    parser.add_argument("--llm", action="store_true", help="启用真实 LLM 模式（默认为 dry_run 模式）")
    parser.add_argument("--env-file", type=str, default=None, help="指定 .env 文件路径（默认自动探测）")

    args = parser.parse_args()

    try:
        # 处理环境变量加载
        if args.llm:
            if args.env_file:
                # 用户指定了 .env 文件
                env_path = args.env_file
                if not Path(env_path).exists():
                    print(f"[ERROR] 指定的 .env 文件不存在: {env_path}")
                    sys.exit(3)

                print(f"[INFO] 使用指定 .env 文件: {env_path}")
                if not load_env_file(env_path):
                    print("[ERROR] .env 文件加载失败或无有效 API Key")
                    sys.exit(3)
                else:
                    print("[OK] API Key 已加载（Key 已加载）")

            else:
                # 自动探测 .env 文件
                env_path = find_env_file()
                if env_path:
                    print(f"[INFO] 自动探测到 .env 文件: {env_path}")
                    if not load_env_file(env_path):
                        print("[WARN] .env 文件加载失败或无有效 API Key，降级到 dry_run 模式")
                        args.llm = False
                    else:
                        print("[OK] API Key 已加载（Key 已加载）")
                else:
                    print("[WARN] 未找到 .env 文件，降级到 dry_run 模式")
                    args.llm = False

        # 加载期望数据
        expected_findings = load_expected_findings()

        # 运行评测
        eval_result = run_evaluation(use_llm=args.llm)

        # 打印报告
        print_evaluation_report(eval_result, expected_findings)

        # 保存报告
        save_evaluation_report(eval_result, expected_findings)

        # 返回是否通过
        checks = check_thresholds(eval_result, expected_findings)

        if not all(checks.values()):
            print("\n[WARN]  评测未通过，退出码1")
            sys.exit(1)
        else:
            print("\n[OK] 评测通过，退出码0")
            sys.exit(0)

    except Exception as e:
        print(f"\n[FAIL] 评测过程出错: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
