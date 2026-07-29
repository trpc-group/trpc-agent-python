#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""仅供维护者手动执行的真实模型公开响应诊断测试。"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.agent import create_review_agent  # noqa: E402
from code_review.config import ReviewConfig  # noqa: E402
from code_review.inputs import FixturePayload  # noqa: E402
from code_review.model_environment import load_model_environment  # noqa: E402
from code_review.pipeline import ReviewPipeline  # noqa: E402
from code_review.redaction import contains_plaintext_secret  # noqa: E402
from code_review.sandbox import SdkSkillSandbox, create_sandbox_runtime  # noqa: E402
from code_review.store import SqlReviewStore  # noqa: E402
from run_agent import PipelineGovernance  # noqa: E402
from trpc_agent_sdk.models import OpenAIModel  # noqa: E402


_MODEL_KEYS = ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME")


def _docker_daemon_available() -> bool:
    """探测 Docker daemon 是否可用于本次可选容器诊断，不创建容器或网络。"""

    executable = shutil.which("docker")
    if executable is None:
        return False
    try:
        import subprocess

        result = subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _diagnostic_payload(
    *,
    result: object,
    public_messages: list[str],
    tool_trace: tuple[str, ...],
) -> dict[str, object]:
    """构造只含脱敏公开回复、工具序列和计数的终端诊断摘要，避免输出输入载荷或凭据。"""

    report = getattr(result, "report")
    metrics = report["metrics"]
    return {
        "event": "real_model_public_diagnostic",
        "sandbox": "container",
        "status": getattr(result, "status"),
        "tool_sequence": list(tool_trace),
        "tool_call_count": metrics["tool_call_count"],
        "llm_duration_ms": metrics["llm_duration_ms"],
        "finding_count": metrics["finding_count"],
        "model_public_messages": public_messages,
    }


@pytest.mark.container
@pytest.mark.real_llm
def test_print_real_model_public_response_and_skill_tool_diagnostics(tmp_path: Path) -> None:
    """调用真实模型和无网络容器，并仅打印模型公开回复及受控工具链的安全摘要。"""

    environment = load_model_environment(PROJECT_ROOT / ".env", environ={})
    if not all(environment.get(key) for key in _MODEL_KEYS):
        pytest.skip("real_model_configuration_missing")
    if not _docker_daemon_available():
        pytest.skip("container_runtime_unavailable")

    config = ReviewConfig()
    selection = create_sandbox_runtime("container")
    sandbox = SdkSkillSandbox(selection, PROJECT_ROOT / "skills" / "code-review", config=config)
    store = SqlReviewStore(f"sqlite+pysqlite:///{(tmp_path / 'diagnostic.db').as_posix()}")
    pipeline = ReviewPipeline(
        store=store,
        governance=PipelineGovernance(
            selection=selection,
            config=config,
            workspace_root=tmp_path / "governance-workspace",
        ),
        sandbox=sandbox,
        output_dir=tmp_path / "reports",
        config=config,
        model_mode="real",
        model_environment=environment,
    )
    model = OpenAIModel(
        environment["TRPC_AGENT_MODEL_NAME"],
        api_key=environment["TRPC_AGENT_API_KEY"],
        base_url=environment["TRPC_AGENT_BASE_URL"],
    )
    agent = create_review_agent(
        pipeline=pipeline,
        skill_root=PROJECT_ROOT / "skills",
        model=model,
        workspace_runtime=selection.runtime,
        workspace_binder=sandbox,
    )
    public_messages: list[str] = []
    container_client = selection.runtime.manager(None).container
    try:
        result = agent.review(
            fixture=FixturePayload(
                payload_type="diff",
                diff_text=(PROJECT_ROOT / "tests" / "fixtures" / "diffs" / "02_security_simple.diff").read_text(
                    encoding="utf-8"
                ),
            ),
            user_instruction="Use the code-review Skill for the approved review request.",
            public_response_observer=public_messages.append,
        )
        diagnostic = _diagnostic_payload(
            result=result,
            public_messages=public_messages,
            tool_trace=agent.last_tool_trace,
        )

        assert result.status == "completed"
        assert agent.last_tool_trace == ("skill_load", "skill_run")
        assert public_messages
        assert contains_plaintext_secret(diagnostic) is False
        print("[real-model-diagnostic] " + json.dumps(diagnostic, ensure_ascii=False, sort_keys=True))
    finally:
        store.close()
        container_client._cleanup_container()
