#!/usr/bin/env python
# run_review.py —— 代码审查 Agent CLI 入口
"""
CLI 工具：读取输入（diff 文件 / git 工作区 / 文件列表）→ 调
agent.pipeline.run_review(...) → 打印 conclusion 摘要。

使用示例：
    # 基本用法（使用 fake 沙箱，任意环境可跑通）
    python run_review.py --diff-file changes.diff --repo-path /path/to/repo

    # 启用 LLM 增强（dry_run 时走预录制）
    python run_review.py --diff-file changes.diff --repo-path /path/to/repo --llm

    # 指定沙箱后端（真实后端需对应依赖）
    python run_review.py --diff-file changes.diff --repo-path /path/to/repo --sandbox local

    # 直接审查文件列表（不走 git diff）
    python run_review.py --files a.py b.py --repo-path /path/to/repo

默认 sandbox=fake，可通过 CODE_REVIEW_SANDBOX_BACKEND 环境变量覆盖。
"""
import argparse
import os
import sys
from pathlib import Path

# 将本脚本所在目录加入 sys.path，确保 agent/storage/filters/sandbox 包可导入
_PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))

from agent.pipeline import run_review  # noqa: E402


def _parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="代码审查 Agent CLI 工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  %(prog)s --diff-file changes.diff --repo-path /path/to/repo
  %(prog)s --diff-file changes.diff --repo-path /path/to/repo --llm
  %(prog)s --files a.py b.py --repo-path /path/to/repo
        """,
    )

    # 输入源（三选一）
    parser.add_argument("--diff-file", type=str, default=None, help="包含 unified diff 的文件路径")
    parser.add_argument("--repo-path", type=str, default=None, help="仓库本地路径（用于 git diff 工作区扫描）")
    parser.add_argument("--files", nargs="+", default=None, help="直接指定文件列表（不走 git diff，逐文件读取）")

    # 沙箱 / 模式
    parser.add_argument(
        "--sandbox",
        type=str,
        default="fake",
        choices=["fake", "local", "container", "cube"],
        help="沙箱后端类型（默认：fake；可被环境变量 CODE_REVIEW_SANDBOX_BACKEND 覆盖）",
    )
    parser.add_argument("--dry-run", action="store_true", default=True, help="Dry-run 模式（默认启用；LLM 层使用预录制数据）")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run", help="禁用 dry-run（真实调用 LLM，需 API Key）")
    parser.add_argument("--llm",
                        action="store_true",
                        default=False,
                        help="启用 LLM 增强层（需 OPENAI_API_KEY/TRPC_AGENT_API_KEY）")

    return parser.parse_args()


def _load_diff_text(args: argparse.Namespace) -> str:
    """根据参数加载 diff 文本（优先级：diff-file > repo-path git diff > files）"""
    if args.diff_file:
        if not os.path.exists(args.diff_file):
            print(f"错误：diff 文件不存在: {args.diff_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.diff_file, "r", encoding="utf-8") as f:
            return f.read()

    if args.files:
        # 直接读取文件列表，构造 DiffFile（不走 unified diff 解析）
        # 这里返回空字符串，pipeline 会拿到 0 文件；改为逐文件拼接成伪 diff
        from agent.diff_parser import parse_file_list
        _ = parse_file_list(args.files)  # 触发解析，用于校验文件存在性
        # 构造伪 diff 文本给 pipeline（pipeline 内部会再 parse_diff）
        # 但 parse_file_list 产出的是 DiffFile，而非 diff 文本；
        # 为保持 pipeline 接口统一，这里把文件内容拼成伪 unified diff。
        lines = []
        for fp in args.files:
            if not os.path.exists(fp):
                continue
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            lines.append(f"diff --git a/{fp} b/{fp}")
            lines.append("--- /dev/null")
            lines.append(f"+++ b/{fp}")
            lines.append("@@ -0,0 +1,%d @@" % len(content.splitlines()))
            for cl in content.splitlines():
                lines.append("+" + cl)
        return "\n".join(lines)

    if args.repo_path:
        # 从 git 工作区扫描变更：用 git diff 获取原始文本交给 pipeline 的 parse_diff
        import subprocess
        try:
            result = subprocess.run(["git", "diff", "HEAD"],
                                    cwd=args.repo_path,
                                    capture_output=True,
                                    text=True,
                                    check=False)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
            print("警告：git 工作区无变更或 git 不可用，使用空 diff", file=sys.stderr)
            return ""
        except (OSError, subprocess.SubprocessError) as e:
            print(f"警告：git diff 失败({e})，使用空 diff", file=sys.stderr)
            return ""

    return ""


def main() -> None:
    """CLI 主入口"""
    args = _parse_args()

    # 至少需要一个输入源
    if not args.diff_file and not args.repo_path and not args.files:
        print("错误：必须提供 --diff-file / --repo-path / --files 之一", file=sys.stderr)
        sys.exit(2)

    # 加载 diff 文本
    diff_text = _load_diff_text(args)

    # 仓库标识（用于报告 repository 字段）
    repo = args.repo_path or os.getcwd()

    # 沙箱后端：环境变量优先于命令行参数
    sandbox_backend = os.getenv("CODE_REVIEW_SANDBOX_BACKEND", args.sandbox)

    # 打印启动信息
    print("开始代码审查...")
    print(f"  仓库: {repo}")
    print(f"  沙箱: {sandbox_backend}")
    print(f"  Dry-run: {args.dry_run}")
    print(f"  LLM: {args.llm}")
    print(f"  diff 行数: {len(diff_text.splitlines()) if diff_text else 0}")
    print()

    # 执行管线
    try:
        report = run_review(diff_text=diff_text, repo=repo, sandbox=sandbox_backend, dry_run=args.dry_run, llm=args.llm)
    except Exception as e:  # noqa: BLE001
        print(f"错误：代码审查失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(3)

    # 打印 conclusion 摘要
    print("代码审查完成")
    print(f"  结论 (conclusion): {report.conclusion}")
    print(f"  状态 (status): {report.status}")
    print(f"  Findings: {len(report.findings)}")
    print(f"  Warnings: {len(report.warnings)}")
    print(f"  Needs Human Review: {len(report.needs_human_review)}")
    print(f"  沙箱运行次数: {len(report.sandbox_runs)}")
    print(f"  被拦截次数: {report.monitoring.blocked_count}")
    print(f"  总耗时: {report.monitoring.total_duration_ms}ms")
    print()

    # 报告文件位置
    output_dir = _PROJECT_ROOT / "outputs"
    if output_dir.exists():
        print("报告已生成：")
        for name in ("review_report.json", "review_report.md", "review_report.sarif"):
            fp = output_dir / name
            if fp.exists():
                print(f"  - {fp}")

    # 退出码：按 conclusion 派生（approve=0, changes_requested=1,
    # needs_human_review=2, completed_with_warnings=0）
    _EXIT_CODES = {
        "approve": 0,
        "changes_requested": 1,
        "needs_human_review": 2,
        "completed_with_warnings": 0,
    }
    sys.exit(_EXIT_CODES.get(report.conclusion, 0))


if __name__ == "__main__":
    main()
