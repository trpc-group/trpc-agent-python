"""报告生成器 — JSON + Markdown 双格式输出。"""
import json
from pathlib import Path

def generate_json_report(baseline_train, baseline_val, attribution, optimization, validation, gate_decision, output_path):
    report = {"pipeline":"eval_optimize_loop","baseline":{"train":baseline_train.to_dict(),"val":baseline_val.to_dict()},"attribution":attribution.to_dict(),"optimization":optimization.to_dict(),"validation":validation.to_dict(),"gate_decision":gate_decision}
    with open(output_path,"w",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2)

def generate_markdown_report(baseline_train, baseline_val, attribution, optimization, validation, gate_decision, output_path):
    L = []
    w = L.append
    w("# Eval-Optimize Loop Report\n\n## 1. Baseline\n")
    for name,r in [("Train",baseline_train),("Val",baseline_val)]:
        w(f"### {name} Set\nPass Rate: {r.summary.pass_rate:.1%} ({r.summary.passed}/{r.summary.total})\nAvg Score: {r.summary.avg_score:.3f}\n\n")
        for c in r.cases:
            st = "PASS" if c.passed else "FAIL"
            w(f"- [{st}] {c.case_id}: {c.ground_truth} -> {c.predicted} (score={c.score:.3f})\n")
        w("\n")
    w("## 2. Attribution\n")
    w(f"Failures: {attribution.total_failures} | Attributed: {attribution.attributed_count}\n\n")
    for cl in attribution.clusters:
        w(f"- **{cl.category}** ({cl.count} cases) -> {cl.prompt_target}\n")
    w("\n## 3. Optimization\n")
    for cand in optimization.candidates:
        w(f"### Candidate {cand.iteration}\n- Target: `{cand.target_prompt_type}`\n- Category: `{cand.failure_category}`\n")
        for cl in cand.change_log:
            w(f"  - {cl}\n")
        w("\n")
    w("## 4. Validation\n")
    if validation.delta_cases:
        for d in validation.delta_cases:
            w(f"- {d.case_id}: {d.baseline_score:.3f} -> {d.candidate_score:.3f} ({d.score_delta:+.3f}) [{d.status}]\n")
        w(f"\nSummary: improved={validation.summary.improved} regressed={validation.summary.regressed}\n")
    w("\n## 5. Gate\n")
    w(f"**Accepted**: {gate_decision.get('accepted',False)}\n**Reason**: {gate_decision.get('reason','')}\n")
    checks = gate_decision.get("checks",[])
    if checks:
        w("\n| Check | Result | Detail |\n|-------|--------|--------|\n")
        for ck in checks:
            st = "PASS" if ck.get("passed",False) else "FAIL"
            w(f"| {ck.get('name','')} | {st} | {ck.get('detail','')} |\n")
    Path(output_path).write_text("".join(L),"utf-8")
