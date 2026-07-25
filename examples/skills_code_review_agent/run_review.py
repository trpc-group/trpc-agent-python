#!/usr/bin/env python3
"""Run the native Skill-driven code-review Agent."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
load_dotenv(Path(__file__).with_name(".env"))

from examples.skills_code_review_agent.agent.tools import parse_review_input, save_review_report


def create_task_id(*, diff_file: str = "", repo_path: str = "", files: list[str] | None = None,
                   now: datetime | None = None, suffix: str | None = None) -> str:
    """Create a readable, time-sortable review id with a collision-safe suffix."""
    if diff_file:
        label = Path(diff_file).resolve().parent.name
    elif repo_path:
        label = Path(repo_path).resolve().name
    elif files:
        label = Path(files[0]).stem
    else:
        label = "review"
    label = re.sub(r"^\d+[-_]+", "", label)
    label = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "review"
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    short_suffix = (suffix or uuid4().hex[:8]).lower()
    return f"cr-{timestamp}-{label}-{short_suffix}"


async def _run_sdk_agent(payload: dict, runtime: str, model: object) -> None:
    """Use the repository Runner in a supported (normally Linux) SDK environment."""
    from trpc_agent_sdk.runners import Runner
    from trpc_agent_sdk.sessions import InMemorySessionService
    from trpc_agent_sdk.types import Content, Part
    from examples.skills_code_review_agent.agent.agent import create_agent_async

    session_service = InMemorySessionService()
    agent = await create_agent_async(runtime=runtime, model=model)
    runner = Runner(app_name="skills_code_review_agent", agent=agent, session_service=session_service)
    await session_service.create_session(app_name="skills_code_review_agent", user_id="reviewer", session_id=payload["task_id"])
    async for _ in runner.run_async(user_id="reviewer", session_id=payload["task_id"],
                                    new_message=Content(parts=[Part.from_text(text=json.dumps(payload))])):
        pass


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the native Skill code-review Agent.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--diff-file", type=str)
    source.add_argument("--repo-path", type=str)
    source.add_argument("--files", type=str, nargs="+")
    parser.add_argument("--output-dir", type=str, default=str(Path(__file__).resolve().parent / "review-output"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Run deterministic no-key review without SDK sandbox startup.")
    mode.add_argument("--fake-model", action="store_true", help="Run the SDK Agent with its deterministic FakeModel; no API key is required.")
    parser.add_argument("--runtime", choices=("docker", "local", "cube", "e2b"), default="docker")
    parser.add_argument("--model-base-url")
    parser.add_argument("--model")
    parser.add_argument("--model-api-key-env", default="OPENAI_API_KEY")
    args = parser.parse_args()

    task_id = create_task_id(diff_file=args.diff_file or "", repo_path=args.repo_path or "", files=args.files)
    staging_dir = Path(args.output_dir) / ".staging" / task_id
    try:
        review_input = parse_review_input(
            diff_file=args.diff_file or "", repo_path=args.repo_path or "", files=args.files,
            staging_dir=str(staging_dir),
        )
        payload = {**review_input, "task_id": task_id, "output_dir": args.output_dir}
        if args.dry_run:
            # Windows-safe fallback: same deterministic parse/report FunctionTool boundary,
            # without importing the SDK path that currently loads python-magic.
            report = save_review_report(task_id=payload["task_id"], findings=[], evidence={"changed_lines": payload["changed_lines"]}, output_dir=args.output_dir)
            print(f"completed: {report['json_path']}")
            return
        if args.fake_model:
            from examples.skills_code_review_agent.agent.agent import create_fake_model
            model = create_fake_model()
        else:
            api_key = os.environ.get(args.model_api_key_env, "")
            if not api_key:
                raise SystemExit(f"missing model API key: set {args.model_api_key_env} in .env or the environment")
            from examples.skills_code_review_agent.agent.agent import OpenAIReviewModel
            model = OpenAIReviewModel(
                api_key, args.model_base_url or os.getenv("OPENAI_BASE_URL", ""),
                args.model or os.getenv("OPENAI_MODEL", ""),
            ).create()
        await _run_sdk_agent(payload, args.runtime, model)
        print(f"submitted: {Path(args.output_dir) / payload['task_id'] / 'review_report.json'}")
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
