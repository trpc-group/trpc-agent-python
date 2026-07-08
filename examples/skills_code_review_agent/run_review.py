"""CLI for the skills based code review agent example."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_DIR.parents[1]
for import_path in (EXAMPLE_DIR, REPO_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from agent.pipeline import DEFAULT_DB_PATH
from agent.pipeline import DEFAULT_OUTPUT_DIR
from agent.pipeline import query_task
from agent.pipeline import run_review
from agent.runtime_factory import create_container_sandbox_runner
from agent.runtime_factory import create_cube_sandbox_runner_from_config
from agent.skill_smoke import run_code_review_skill_smoke
from agent.storage import ReviewStore


def query_review_store(args: argparse.Namespace, task_id: str) -> dict:
    if args.db_url:
        return ReviewStore.from_url(args.db_url).get_task_bundle(task_id)
    return query_task(args.db_path, task_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the skills code review agent example.")
    parser.add_argument("--diff-file", type=Path)
    parser.add_argument("--patch-file",
                        type=Path,
                        help="Alias for PR patch/unified diff files. Use --diff-file - to read stdin.")
    parser.add_argument("--repo-path", type=Path)
    parser.add_argument("--file-list", type=Path)
    parser.add_argument("--fixture")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--db-url",
                        help="SQL-style review store URL. Supports sqlite:///path/to/reviews.sqlite by default.")
    parser.add_argument("--sandbox", choices=["container", "cube", "fake", "local"], default="fake")
    parser.add_argument("--container-image", default="python:3-slim")
    parser.add_argument("--docker-path")
    parser.add_argument("--docker-base-url")
    parser.add_argument("--cube-template")
    parser.add_argument("--cube-api-url")
    parser.add_argument("--cube-api-key")
    parser.add_argument("--cube-sandbox-id")
    parser.add_argument("--dry-run",
                        action="store_true",
                        help="Use fake model/sandbox mode. This is the default for offline demos.")
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--max-output-bytes", type=int, default=12000)
    parser.add_argument("--filter-timeout-budget-sec", type=float, default=30.0)
    parser.add_argument("--filter-max-output-bytes", type=int, default=20000)
    parser.add_argument("--max-diff-bytes", type=int, default=2_000_000)
    parser.add_argument("--network-policy", choices=["deny", "allowlist"], default="deny")
    parser.add_argument("--test-command",
                        help="Optional unit test command to run inside the sandbox after filter approval.")
    parser.add_argument("--custom-rule-script",
                        help="Skill-relative custom rule script under scripts/, for example scripts/static_review.py.")
    parser.add_argument("--include-network-scanners",
                        action="store_true",
                        help="Request network-backed scanners such as semgrep auto config; Filter may block them.")
    parser.add_argument("--task-id", help="Query a stored task instead of running a new review.")
    parser.add_argument("--show-db", action="store_true", help="Print the stored task bundle as JSON.")
    parser.add_argument("--skill-smoke",
                        action="store_true",
                        help="Run an SDK-native skill_load/skill_run smoke check and exit.")
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    if args.skill_smoke:
        print(json.dumps(await run_code_review_skill_smoke(), indent=2, sort_keys=True))
        return
    if args.task_id:
        print(json.dumps(query_review_store(args, args.task_id), indent=2, sort_keys=True))
        return
    sandbox_runner = None
    if args.sandbox == "container" and not args.dry_run:
        sandbox_runner = create_container_sandbox_runner(
            image=args.container_image,
            docker_path=args.docker_path,
            base_url=args.docker_base_url,
        )
    elif args.sandbox == "cube" and not args.dry_run:
        sandbox_runner = await create_cube_sandbox_runner_from_config(
            template=args.cube_template,
            api_url=args.cube_api_url,
            api_key=args.cube_api_key,
            sandbox_id=args.cube_sandbox_id,
        )

    report = await run_review(
        diff_file=args.diff_file,
        patch_file=args.patch_file,
        repo_path=args.repo_path,
        file_list=args.file_list,
        fixture=args.fixture,
        output_dir=args.output_dir,
        db_path=args.db_path,
        db_url=args.db_url,
        sandbox=args.sandbox,
        dry_run=args.dry_run or args.sandbox == "fake",
        container_image=args.container_image,
        docker_path=args.docker_path,
        docker_base_url=args.docker_base_url,
        cube_template=args.cube_template,
        cube_api_url=args.cube_api_url,
        cube_api_key=args.cube_api_key,
        cube_sandbox_id=args.cube_sandbox_id,
        timeout_sec=args.timeout_sec,
        max_output_bytes=args.max_output_bytes,
        filter_timeout_budget_sec=args.filter_timeout_budget_sec,
        filter_max_output_bytes=args.filter_max_output_bytes,
        network_policy=args.network_policy,
        test_command=args.test_command,
        custom_rule_script=args.custom_rule_script,
        include_network_scanners=args.include_network_scanners,
        max_diff_bytes=args.max_diff_bytes,
        sandbox_runner=sandbox_runner,
    )
    print(
        json.dumps(
            {
                "task_id": report.task_id,
                "status": report.status,
                "conclusion": report.conclusion,
                "outputs": report.output_files
            },
            indent=2))
    if args.show_db:
        print(json.dumps(query_review_store(args, report.task_id), indent=2, sort_keys=True))


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
