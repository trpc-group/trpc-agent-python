#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Command-line entry point for the automatic code-review Agent."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from code_review.config import ReviewConfig
from code_review.governance import (
    ExecutionBudget,
    GovernanceRequest,
    SandboxGovernanceFilter,
)
from code_review.inputs import FixturePayload, InputValidationError, load_input
from code_review.model_environment import load_model_environment
from code_review.model_runtime import build_real_model
from code_review.pipeline import PipelineFatalError, ReviewPipeline
from code_review.redaction import contains_plaintext_secret
from code_review.trace import TraceSink, emit_trace
from code_review.sandbox import (
    SandboxConfigurationError,
    SandboxRuntimeSelection,
    SdkSkillSandbox,
    build_sandbox_environment,
    create_sandbox_runtime,
)
from code_review.store import DEFAULT_DB_URL, SqlReviewStore, init_db
from trpc_agent_sdk.models import LLMModel


_PROJECT_ROOT = Path(__file__).resolve().parent
_SKILL_ROOT = _PROJECT_ROOT / "skills" / "code-review"
_MANIFEST_PATH = _SKILL_ROOT / "scripts" / "manifest.json"
_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_LOGGER = logging.getLogger("code_review_agent")
_USER_QUERY_MAX_CHARACTERS = 1000


class CliError(ValueError):
    """表示应以退出码 2 返回且不泄露原始异常文本的用户输入或运行配置错误。"""


class PipelineGovernance:
    """把 C1 的受控 Filter 请求适配为 ReviewPipeline 所需的治理端口。"""

    def __init__(self, *, selection: Any, config: ReviewConfig, workspace_root: Path) -> None:
        """绑定已选 runtime、固定配置和仅用于相对路径校验的任务 workspace 根目录。"""

        self._selection = selection
        self._config = config
        self._workspace_root = workspace_root
        self._filter = SandboxGovernanceFilter(_MANIFEST_PATH, config=config)

    def decide(self, **_arguments: Any) -> Mapping[str, Any]:
        """为固定 run_checks 脚本创建无 shell、无敏感环境变量的唯一治理请求。"""

        request = GovernanceRequest(
            script_id="run_checks",
            structured_args={},
            skill_root=_SKILL_ROOT,
            workspace_root=self._workspace_root,
            input_paths=(Path("work/inputs/diff.json"),),
            output_paths=(Path("out/findings.json"),),
            environment=build_sandbox_environment(),
            runtime_type=self._selection.runtime_type,
            effective_network_mode=self._selection.effective_network_mode,
            network_policy_verified=self._selection.network_policy_verified,
            explicit_local=self._selection.explicit_local,
            budget=ExecutionBudget(),
        )
        return self._filter.decide(request).to_mapping()


def _json_output(payload: Mapping[str, Any]) -> None:
    """输出稳定 JSON，避免 CLI 结果混入路径、异常原文或非结构化日志。"""

    print(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True))


def configure_safe_logging(level_name: str | None) -> None:
    """配置仅输出安全项目事件的 stderr 日志，并屏蔽可能含路径或工作区标识的 SDK 原始诊断。"""

    level = getattr(logging, (level_name or "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    _LOGGER.handlers.clear()
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(level)
    _LOGGER.propagate = False
    sdk_logger = logging.getLogger("trpc_agent_sdk")
    sdk_logger.handlers.clear()
    sdk_logger.addHandler(logging.NullHandler())
    sdk_logger.setLevel(logging.CRITICAL + 1)
    sdk_logger.propagate = False


def _terminal_report_path(path: Path) -> str:
    """把报告路径限制为当前目录相对形式，避免 INFO 日志暴露宿主绝对路径。"""

    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def _trace_sink(args: argparse.Namespace) -> TraceSink | None:
    """在显式 --trace 时构造 stderr JSONL sink，保持 stdout 的最终结果契约。"""

    if not getattr(args, "trace", False):
        return None

    def write(event: str, details: Mapping[str, object]) -> None:
        """将已脱敏事件立即写到 stderr，供终端实时展示与脚本过滤。"""

        payload = {"event": event, **dict(details)}
        print(
            "[code-review-trace] " + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
            flush=True,
        )

    return write


def load_fixture_payload(name: str) -> FixturePayload:
    """从受控 fixture 目录解析 diff 或 JSON 载荷，供 CLI 与评测复用同一可信 fixture 边界。"""

    if not name or Path(name).name != name:
        raise CliError("fixture_name_invalid")
    fixture_dir = _PROJECT_ROOT / "tests" / "fixtures" / "diffs"
    diff_path = fixture_dir / f"{name}.diff"
    json_path = fixture_dir / f"{name}.json"
    if diff_path.is_file():
        return FixturePayload(payload_type="diff", diff_text=diff_path.read_text(encoding="utf-8"))
    if json_path.is_file():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CliError("fixture_payload_invalid") from exc
        if isinstance(payload, dict) and isinstance(payload.get("diff"), str):
            return FixturePayload(payload_type="diff", diff_text=payload["diff"])
    raise CliError("fixture_not_found")


def _normalized_files(values: Sequence[str], input_root: Path) -> tuple[Path, ...]:
    """把 CLI 文件实参规范为 input_root 内相对路径，拒绝宿主路径逃逸。"""

    normalized: list[Path] = []
    resolved_root = input_root.resolve()
    for value in values:
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(resolved_root)
            except (OSError, ValueError) as exc:
                raise CliError("input_path_outside_root") from exc
        normalized.append(candidate)
    return tuple(normalized)


def _container_available() -> bool:
    """在启动任务前检查严格默认 container 的最小本机前置，缺失时不伪造 local 回退。"""

    executable = shutil.which("docker")
    if executable is None:
        return False
    try:
        result = subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def build_review_pipeline(
    args: argparse.Namespace,
) -> tuple[ReviewPipeline, SqlReviewStore, SdkSkillSandbox, SandboxRuntimeSelection]:
    """按明确 sandbox 选择组装唯一 ReviewPipeline，供 CLI 与受控评测复用且不让 dry-run 改变隔离策略。"""

    config = ReviewConfig.from_env()
    output_dir = Path(args.output_dir)
    if args.sandbox == "container" and not _container_available():
        raise CliError("container_runtime_unavailable")
    try:
        selection = create_sandbox_runtime(
            args.sandbox,
            explicit_local=args.sandbox == "local",
            local_work_root=str(output_dir / ".workspaces"),
        )
    except SandboxConfigurationError as exc:
        raise CliError("sandbox_configuration_invalid") from exc

    model_environment = None
    if args.model_mode == "real" and not args.dry_run:
        model_environment = load_model_environment(_PROJECT_ROOT / ".env")
    store = SqlReviewStore(args.db_url)
    sandbox = SdkSkillSandbox(selection, _SKILL_ROOT, config=config)
    pipeline = ReviewPipeline(
        store=store,
        governance=PipelineGovernance(
            selection=selection,
            config=config,
            workspace_root=output_dir / ".workspace-root",
        ),
        sandbox=sandbox,
        output_dir=output_dir,
        config=config,
        model_mode="fake" if args.dry_run else args.model_mode,
        model_environment=model_environment,
    )
    return pipeline, store, sandbox, selection


def _agent_model(args: argparse.Namespace) -> LLMModel | None:
    """仅在显式 real 且非 dry-run 时构造真实 Agent 模型，其余模式使用离线工具调用模型。"""

    if args.dry_run or args.model_mode != "real":
        return None
    environment = load_model_environment(_PROJECT_ROOT / ".env")
    api_key = environment.get("TRPC_AGENT_API_KEY", "")
    base_url = environment.get("TRPC_AGENT_BASE_URL", "")
    model_name = environment.get("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not base_url or not model_name:
        raise CliError("real_model_configuration_missing")
    return build_real_model(environment)


def _create_sdk_review_agent(
    *,
    pipeline: ReviewPipeline,
    selection: SandboxRuntimeSelection,
    sandbox: SdkSkillSandbox,
    model: LLMModel | None,
) -> Any:
    """仅在 user-query 入口加载 SDK Agent，并屏蔽已知第三方弃用警告以保护终端路径边界。"""

    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

    warnings.filterwarnings(
        "ignore",
        message=r"The default value of `allowed_objects` will change.*",
        category=LangChainPendingDeprecationWarning,
        module=r"langgraph\.checkpoint\..*",
    )
    from agent.agent import create_review_agent

    return create_review_agent(
        pipeline=pipeline,
        skill_root=_PROJECT_ROOT / "skills",
        model=model,
        workspace_runtime=selection.runtime,
        workspace_binder=sandbox,
    )


def _review_input(args: argparse.Namespace) -> dict[str, Any]:
    """将 argparse 输入转换为 pipeline 的四选一输入契约，并保留 fixture 解析边界。"""

    if args.diff_file is not None:
        return {"diff_file": Path(args.diff_file)}
    if args.repo_path is not None:
        return {"repo_path": Path(args.repo_path)}
    if args.files:
        input_root = Path(args.input_root).resolve()
        return {
            "files": _normalized_files(args.files, input_root),
            "input_root": input_root,
        }
    if args.fixture is not None:
        return {"fixture": load_fixture_payload(args.fixture)}
    raise CliError("review_input_required")


def _validate_user_query(query: str) -> None:
    """验证自然语言意图不携带凭据、原始补丁或不可控二进制内容。"""

    if not isinstance(query, str) or not query.strip() or len(query) > _USER_QUERY_MAX_CHARACTERS:
        raise CliError("user_query_invalid")
    if "\x00" in query or "diff --git " in query or "\n@@ " in query:
        raise CliError("user_query_payload_forbidden")
    if contains_plaintext_secret(query):
        raise CliError("user_query_secret_forbidden")


def _preflight_user_query_input(args: argparse.Namespace) -> dict[str, Any]:
    """在创建 Agent 前验证四类结构化输入，确保无效载荷零模型和沙箱副作用。"""

    input_options = _review_input(args)
    try:
        parsed = load_input(config=ReviewConfig.from_env(), **input_options)
    except (InputValidationError, ValueError) as exc:
        raise CliError("user_query_input_invalid") from exc
    if input_options.get("diff_file") is not None and parsed.change_set.file_count == 0:
        raise CliError("user_query_diff_invalid")
    return input_options


def _source_kind(input_options: Mapping[str, Any]) -> str:
    """从已验证的输入选项提取安全来源枚举，禁止读取路径或原始载荷。"""

    for name in ("fixture", "diff_file", "repo_path", "files"):
        if input_options.get(name) is not None:
            return name.removesuffix("_file").removesuffix("_path")
    return "unknown"


def _severity_exit_code(report: Mapping[str, Any], threshold: str | None) -> int:
    """仅根据 canonical 正式 findings 和显式阈值决定评审命令的 0 或 1。"""

    if threshold is None:
        return 0
    minimum = _SEVERITY_RANK[threshold]
    for finding in report.get("findings", ()):  # needs_human_review 不得改变 CI 失败语义。
        if isinstance(finding, Mapping) and _SEVERITY_RANK.get(finding.get("severity"), 0) >= minimum:
            return 1
    return 0


def _report_output_paths(output_dir: Path) -> dict[str, str]:
    """返回本次终端可见的完整报告路径，不把路径持久化到审查数据。"""

    return {
        "json": str((output_dir / "review_report.json").resolve()),
        "markdown": str((output_dir / "review_report.md").resolve()),
    }


def _run_review(
    args: argparse.Namespace,
    *,
    use_agent: bool,
    user_instruction: str | None = None,
    input_options: Mapping[str, Any] | None = None,
) -> int:
    """按 direct 或 Agent 入口执行同一 pipeline，并只输出安全的运行摘要。"""

    pipeline, store, sandbox, selection = build_review_pipeline(args)
    trace = _trace_sink(args)
    try:
        resolved_input = dict(_review_input(args) if input_options is None else input_options)
        agent = None
        entrypoint = "agent" if use_agent else "pipeline"
        _LOGGER.info(
            "Review started: entrypoint=%s model_mode=%s runtime=%s",
            entrypoint,
            "fake" if args.dry_run else args.model_mode,
            args.sandbox,
        )
        container_id = sandbox.container_id
        if container_id:
            _LOGGER.info("Container started: container_id=%s", container_id)
        emit_trace(
            trace,
            "review.started",
            entrypoint=entrypoint,
            runtime_type=args.sandbox,
            model_mode="fake" if args.dry_run else args.model_mode,
        )
        if use_agent:
            agent = _create_sdk_review_agent(
                pipeline=pipeline,
                model=_agent_model(args),
                selection=selection,
                sandbox=sandbox,
            )
            result = agent.review(
                user_instruction=user_instruction,
                trace=trace,
                **resolved_input,
            )
        else:
            result = pipeline.run(trace=trace, **resolved_input)
        emit_trace(trace, "review.completed", status=result.status, entrypoint=entrypoint)
        report_files = _report_output_paths(Path(args.output_dir))
        _LOGGER.info(
            "Report persisted: status=%s findings=%s warnings=%s needs_human_review=%s",
            result.status,
            len(result.report.get("findings", ())),
            len(result.report.get("warnings", ())),
            len(result.report.get("needs_human_review", ())),
        )
        _LOGGER.info("JSON report saved to: %s", _terminal_report_path(Path(report_files["json"])))
        _LOGGER.info("Markdown report saved to: %s", _terminal_report_path(Path(report_files["markdown"])))
        _json_output(
            {
                "task_id": result.task_id,
                "status": result.status,
                "entrypoint": entrypoint,
                "skill_tools": list(agent.last_tool_trace) if agent is not None else [],
                "report_files": report_files,
                "dry_run": bool(args.dry_run),
                "sandbox": args.sandbox,
            }
        )
        return _severity_exit_code(result.report, args.fail_on_severity)
    except PipelineFatalError as exc:
        raise CliError("review_pipeline_failed") from exc
    finally:
        store.close()


def _review(args: argparse.Namespace) -> int:
    """执行公开 direct review 入口，不创建 Agent 或 Skill 工具调用。"""

    return _run_review(args, use_agent=False)


def _user_query(args: argparse.Namespace) -> int:
    """验证自然语言意图和结构化输入后，经 SDK Agent 调用受控 code-review Skill。"""

    trace = _trace_sink(args)
    emit_trace(trace, "user_query.request_received", input_type="query")
    _validate_user_query(args.query)
    input_options = _preflight_user_query_input(args)
    emit_trace(trace, "user_query.input_validated", input_type=_source_kind(input_options))
    return _run_review(
        args,
        use_agent=True,
        user_instruction=args.query,
        input_options=input_options,
    )


def _show(args: argparse.Namespace) -> int:
    """按 task id 查询完整脱敏数据库 bundle；不存在时以退出码 2 表达无效请求。"""

    store = SqlReviewStore(args.db_url)
    try:
        store.initialize()
        bundle = store.get_task_bundle(args.task_id)
    finally:
        store.close()
    if bundle is None:
        raise CliError("task_not_found")
    _json_output(bundle)
    return 0


def _list(args: argparse.Namespace) -> int:
    """列出任务的最小安全摘要，避免将报告、原始输入或宿主路径复制到标准输出。"""

    store = SqlReviewStore(args.db_url)
    try:
        store.initialize()
        payload = {"tasks": store.list_task_summaries()}
    finally:
        store.close()
    _json_output(payload)
    return 0


def _init_db(args: argparse.Namespace) -> int:
    """初始化指定数据库的五张业务表，保持该子命令幂等。"""

    init_db(args.db_url)
    _json_output({"status": "initialized"})
    return 0


def _add_db_argument(parser: argparse.ArgumentParser) -> None:
    """向子命令添加可替换 SQL 后端的 URL 参数，默认保持 SQLite 开箱即用。"""

    parser.add_argument("--db-url", default=DEFAULT_DB_URL)


def _add_review_execution_arguments(parser: argparse.ArgumentParser) -> None:
    """为 review 与 user-query 添加一致的沙箱、模型、输出和 CI 失败阈值参数。"""

    parser.add_argument("--output-dir", default="out")
    parser.add_argument("--sandbox", choices=("container", "cube", "local"), default="container")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model-mode", choices=("fake", "real", "off"), default="fake")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING"), default="INFO")
    parser.add_argument("--trace", action="store_true", help="stream sanitized review progress to stderr")
    parser.add_argument("--fail-on-severity", choices=tuple(_SEVERITY_RANK))
    _add_db_argument(parser)


def _add_review_input_arguments(parser: argparse.ArgumentParser) -> None:
    """为 direct 与 Agent 入口添加完全一致且互斥的四类结构化输入参数。"""

    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--diff-file")
    inputs.add_argument("--repo-path")
    inputs.add_argument("--files", nargs="+")
    inputs.add_argument("--fixture")
    parser.add_argument("--input-root", default=str(Path.cwd()))


def _build_parser() -> argparse.ArgumentParser:
    """构建仅暴露本期允许参数的子命令解析器，未知高风险参数由 argparse 拒绝。"""

    parser = argparse.ArgumentParser(description="Automatic code-review Agent")
    subcommands = parser.add_subparsers(dest="command", required=True)

    review = subcommands.add_parser("review", help="run one deterministic review")
    _add_review_input_arguments(review)
    _add_review_execution_arguments(review)
    review.set_defaults(handler=_review)

    user_query = subcommands.add_parser(
        "user-query",
        help="run an Agent review from natural-language intent and one explicit input",
    )
    user_query.add_argument("query")
    _add_review_input_arguments(user_query)
    _add_review_execution_arguments(user_query)
    user_query.set_defaults(handler=_user_query)

    show = subcommands.add_parser("show", help="show one persisted review bundle")
    show.add_argument("task_id")
    _add_db_argument(show)
    show.set_defaults(handler=_show)

    list_command = subcommands.add_parser("list", help="list persisted review tasks")
    _add_db_argument(list_command)
    list_command.set_defaults(handler=_list)

    initialize = subcommands.add_parser("init-db", help="initialize the review database")
    _add_db_argument(initialize)
    initialize.set_defaults(handler=_init_db)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """分发 CLI 子命令并将可预期错误收敛为 2，其他异常不暴露原始运行环境信息。"""

    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_safe_logging(getattr(args, "log_level", "WARNING"))
    try:
        return int(args.handler(args))
    except (CliError, ValueError):
        _json_output({"status": "invalid", "error": "invalid_request_or_configuration"})
        return 2
    except Exception:
        _json_output({"status": "failed", "error": "review_runtime_error"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
