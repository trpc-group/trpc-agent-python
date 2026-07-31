"""Isolated process entry point for a live AgentOptimizer invocation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt

from .live_adapter import load_verified_callback
from .schema import parse_strict_json, sanitize, sanitized_exception_message


async def _run(request_path: Path) -> None:
    request = parse_strict_json(request_path.read_text(encoding="utf-8"))
    callback = load_verified_callback(
        str(request["callbackSpec"]),
        expected_source_path=str(request["callbackSourcePath"]),
        expected_source_sha256=str(request["callbackSourceSha256"]),
        expected_callable_sha256=str(request["callbackCallableSha256"]),
    )
    prompt_paths = request["promptPaths"]
    if not isinstance(prompt_paths, dict) or not prompt_paths:
        raise ValueError("promptPaths must be a non-empty object")
    target = TargetPrompt()
    for name, path in prompt_paths.items():
        target.add_path(str(name), str(path))
    result = await AgentOptimizer.optimize(
        config_path=str(request["configPath"]),
        call_agent=callback,
        target_prompt=target,
        train_dataset_path=str(request["trainPath"]),
        validation_dataset_path=str(request["validationPath"]),
        output_dir=str(request["outputDir"]),
        update_source=False,
        verbose=int(request.get("verbose", 0)),
    )
    output_dir = Path(str(request["outputDir"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    result.dump_to(str(output_dir / "result.json"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    request_path = Path(parser.parse_args().request).resolve()
    try:
        asyncio.run(_run(request_path))
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        try:
            request = parse_strict_json(request_path.read_text(encoding="utf-8"))
            output_dir = Path(str(request["outputDir"]))
            output_dir.mkdir(parents=True, exist_ok=True)
            payload = sanitize(
                {
                    "errorType": type(error).__name__,
                    "message": sanitized_exception_message(
                        error,
                        max_text_chars=4000,
                    ),
                },
                max_text_chars=None,
            )
            (output_dir / "worker_error.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
