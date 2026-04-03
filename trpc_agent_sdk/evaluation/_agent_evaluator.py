# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""TRPC Agent Evaluation Framework - Main Entry Point.

This module provides the AgentEvaluator class, which serves as the primary interface
for evaluating TRPC agents against predefined test cases.

Key Features:
    - Agent evaluation with multiple runs
    - Support for various evaluation metrics
    - Detailed result reporting
    - Integration with evaluation services

Classes:
    AgentEvaluator: Main evaluator class for running agent evaluations
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from dataclasses import dataclass
from typing import Any
from typing import Optional

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.agents import BaseAgent

from ._local_eval_service import LocalEvalService
from . import _utils
from ._eval_callbacks import Callbacks
from ._eval_config import EvalConfig
from ._eval_metrics import EvalStatus
from ._eval_pass import pass_at_k as _pass_at_k
from ._eval_pass import pass_hat_k as _pass_hat_k
from ._eval_result import EvalCaseResult
from ._eval_result import EvalSetAggregateResult
from ._eval_result import EvaluateResult
from ._eval_set import EvalSet
from ._eval_sets_manager_base import EvalSetsManager
from ._in_memory_eval_sets_manager import InMemoryEvalSetsManager
from ._local_eval_set_results_manager import LocalEvalSetResultsManager
from ._user_simulator_provider import UserSimulatorProvider

# Constants for default runs
NUM_RUNS = 1
# Default app_name when evalset does not set app_name (session/result paths)
DEFAULT_EVAL_APP_NAME = "test_app"

_RESULT_HANDLER = _utils.EvalResultHandler()


@dataclass(frozen=True)
class PassNC:
    """(n, c): n = runs, c = runs that all passed (for pass@k / pass^k)."""

    n: int
    c: int


class _EvalExecuter:
    """Returned by get_executer(); await .evaluate() then .get_result()."""

    def __init__(
        self,
        agent_module: str,
        eval_dataset_file_path_or_dir: str,
        num_runs: int = NUM_RUNS,
        agent_name: Optional[str] = None,
        print_detailed_results: bool = True,
        eval_result_output_dir: Optional[str] = None,
        runner: Optional[Runner] = None,
        case_parallelism: Optional[int] = None,
        case_eval_parallelism: Optional[int] = None,
        callbacks: Optional[Callbacks] = None,
    ):
        self._agent_module = agent_module
        self._eval_dataset_file_path_or_dir = eval_dataset_file_path_or_dir
        self._num_runs = num_runs
        self._agent_name = agent_name
        self._print_detailed_results = print_detailed_results
        self._eval_result_output_dir = eval_result_output_dir
        self._runner = runner
        self._case_parallelism = case_parallelism
        self._case_eval_parallelism = case_eval_parallelism
        self._callbacks = callbacks
        self._result: Optional[EvaluateResult] = None
        self._task: Optional[asyncio.Task] = None

    async def _run(self) -> None:
        agent_module = self._agent_module
        eval_dataset_file_path_or_dir = self._eval_dataset_file_path_or_dir
        num_runs = self._num_runs
        agent_name = self._agent_name
        print_detailed_results = self._print_detailed_results
        eval_result_output_dir = self._eval_result_output_dir
        runner = self._runner
        case_parallelism = self._case_parallelism
        case_eval_parallelism = self._case_eval_parallelism
        callbacks = self._callbacks

        test_files = []
        if os.path.isdir(eval_dataset_file_path_or_dir):
            for root, _, files in os.walk(eval_dataset_file_path_or_dir):
                for file in files:
                    if file.endswith(".test.json") or file.endswith(".evalset.json"):
                        test_files.append(os.path.join(root, file))
        else:
            test_files = [eval_dataset_file_path_or_dir]

        eval_set_results_manager = None
        if eval_result_output_dir:
            eval_set_results_manager = LocalEvalSetResultsManager(eval_history_dir=eval_result_output_dir)

        all_failures: list[tuple[str, dict]] = []
        all_details: list[tuple[str, list[str]]] = []
        all_results: list[tuple[str, list[str]]] = []
        results_by_eval_set_id: dict[str, EvalSetAggregateResult] = {}
        for test_file in test_files:
            eval_config = AgentEvaluator.find_config_for_test_file(test_file)
            eval_set = AgentEvaluator._load_eval_set_from_file(test_file, eval_config)
            # Config (test_config.json) overrides parameter
            config_path = os.path.join(os.path.dirname(test_file), "test_config.json")
            num_runs_for_set = (eval_config.num_runs if os.path.exists(config_path) else num_runs)
            failed_summary, details_lines, result_lines, eval_results_by_eval_id = (
                await AgentEvaluator.evaluate_eval_set(
                    agent_module=agent_module,
                    eval_set=eval_set,
                    eval_config=eval_config,
                    num_runs=num_runs_for_set,
                    agent_name=agent_name,
                    callbacks=callbacks,
                    print_detailed_results=print_detailed_results,
                    eval_set_results_manager=eval_set_results_manager,
                    runner=runner,
                    case_parallelism=case_parallelism,
                    case_eval_parallelism=case_eval_parallelism,
                ))
            if failed_summary is not None:
                all_failures.append((eval_set.eval_set_id, failed_summary))
            if print_detailed_results and details_lines:
                all_details.append((eval_set.eval_set_id, details_lines))
            if result_lines:
                all_results.append((eval_set.eval_set_id, result_lines))
            results_by_eval_set_id[eval_set.eval_set_id] = EvalSetAggregateResult(
                eval_results_by_eval_id=eval_results_by_eval_id,
                num_runs=num_runs_for_set,
            )
        if all_details or all_results:
            _RESULT_HANDLER.print_evaluation_report(
                all_details=all_details,
                all_results=all_results,
                display_agent_name=agent_name or agent_module,
                num_runs=num_runs_for_set,
            )
        self._result = EvaluateResult(results_by_eval_set_id=results_by_eval_set_id)
        if all_failures:
            combined = json.dumps(
                [{
                    "evalSetId": eid,
                    "summary": s
                } for eid, s in all_failures],
                indent=2,
                ensure_ascii=False,
            )
            assert False, combined

    async def _ensure_run(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        await self._task

    async def evaluate(self) -> None:
        """Run evaluation. Must be awaited; does not run when executer is created.

        Returns:
            None. Use get_result() after await to get EvaluateResult.
        """
        await self._ensure_run()

    def get_result(self) -> Optional[EvaluateResult]:
        """Return evaluation result. Call only after evaluate() has been awaited.

        Returns:
            EvaluateResult, or None if evaluate() has not run or failed before setting result.
        """
        return self._result


class AgentEvaluator:
    """Main interface for evaluating agents.

    This class provides a simple API for running evaluations:

    Example:
        ```python
        # Create evaluation set
        eval_set = EvalSet(
            eval_set_id="test_set",
            eval_cases=[...],
        )

        # Configure evaluation
        eval_config = EvalConfig(
            criteria={
                "tool_trajectory_avg_score": 1.0,
                "response_match_score": 0.8,
            }
        )

        # Run evaluation
        await AgentEvaluator.evaluate_eval_set(
            agent=my_agent,
            eval_set=eval_set,
            eval_config=eval_config,
        )
        ```
    """

    @staticmethod
    def find_config_for_test_file(test_file: str) -> EvalConfig:
        """Find the test_config.json file in the same folder as the test file.

        Args:
            test_file: Path to the test file

        Returns:
            EvalConfig loaded from test_config.json or default config
        """
        test_folder = os.path.dirname(test_file)
        config_path = os.path.join(test_folder, "test_config.json")
        return AgentEvaluator._load_config_from_file(config_path)

    @staticmethod
    async def evaluate(
        agent_module: str,
        eval_dataset_file_path_or_dir: str,
        num_runs: int = NUM_RUNS,
        agent_name: Optional[str] = None,
        print_detailed_results: bool = True,
        eval_result_output_dir: Optional[str] = None,
        runner: Optional[Runner] = None,
        case_parallelism: Optional[int] = None,
        case_eval_parallelism: Optional[int] = None,
        callbacks: Optional[Callbacks] = None,
    ) -> None:
        """Run evaluation; no result returned. Use get_executer() if you need the result.

        Args:
            agent_module: Python module path containing the agent (look for 'root_agent' or 'get_agent_async').
            eval_dataset_file_path_or_dir: Path to eval dataset file or directory
                (recursively .test.json / .evalset.json).
            num_runs: Number of runs per eval set.
            agent_name: Display name of the agent.
            print_detailed_results: Whether to print per-case details.
            eval_result_output_dir: Optional dir for .evalset_result.json output.
            runner: Optional Runner instance.
            case_parallelism: Max concurrent cases for inference; None uses default.
            case_eval_parallelism: Max concurrent cases for evaluation (scoring);
                None uses default.
            callbacks: Optional lifecycle callbacks.
        """
        executer = AgentEvaluator.get_executer(
            agent_module=agent_module,
            eval_dataset_file_path_or_dir=eval_dataset_file_path_or_dir,
            num_runs=num_runs,
            agent_name=agent_name,
            print_detailed_results=print_detailed_results,
            eval_result_output_dir=eval_result_output_dir,
            runner=runner,
            case_parallelism=case_parallelism,
            case_eval_parallelism=case_eval_parallelism,
            callbacks=callbacks,
        )
        await executer.evaluate()

    @staticmethod
    def get_executer(
        agent_module: str,
        eval_dataset_file_path_or_dir: str,
        num_runs: int = NUM_RUNS,
        agent_name: Optional[str] = None,
        print_detailed_results: bool = True,
        eval_result_output_dir: Optional[str] = None,
        runner: Optional[Runner] = None,
        case_parallelism: Optional[int] = None,
        case_eval_parallelism: Optional[int] = None,
        callbacks: Optional[Callbacks] = None,
    ) -> _EvalExecuter:
        """Return an executer (does not run). Await executer.evaluate() then executer.get_result() for result.

        Args:
            agent_module: Python module path containing the agent (look for 'root_agent' or 'get_agent_async').
            eval_dataset_file_path_or_dir: Path to eval dataset file or directory
                (recursively .test.json / .evalset.json).
            num_runs: Number of runs per eval set.
            agent_name: Display name of the agent.
            print_detailed_results: Whether to print per-case details.
            eval_result_output_dir: Optional dir for .evalset_result.json output.
            runner: Optional Runner instance.
            case_parallelism: Max concurrent cases for inference; None uses default.
            case_eval_parallelism: Max concurrent cases for evaluation (scoring);
                None uses default.
            callbacks: Optional lifecycle callbacks.

        Returns:
            _EvalExecuter: Await .evaluate() to run, then .get_result() for EvaluateResult.
        """
        return _EvalExecuter(
            agent_module=agent_module,
            eval_dataset_file_path_or_dir=eval_dataset_file_path_or_dir,
            num_runs=num_runs,
            agent_name=agent_name,
            print_detailed_results=print_detailed_results,
            eval_result_output_dir=eval_result_output_dir,
            runner=runner,
            case_parallelism=case_parallelism,
            case_eval_parallelism=case_eval_parallelism,
            callbacks=callbacks,
        )

    @staticmethod
    def _nc_from_set(
        eval_results_by_eval_id: dict[str, list[EvalCaseResult]],
        num_runs: int,
    ) -> tuple[int, int]:
        """From one eval set's results, compute (n, c). n = num_runs; c = runs where every case passed."""
        if num_runs <= 0:
            raise ValueError("num_runs must be > 0")
        n = num_runs
        c = 0
        for run_i in range(num_runs):
            run_passed = True
            for eval_id, results in eval_results_by_eval_id.items():
                if len(results) <= run_i:
                    raise ValueError(f"eval_id {eval_id!r} has only {len(results)} runs, expected at least {num_runs}")
                if results[run_i].final_eval_status != EvalStatus.PASSED:
                    run_passed = False
                    break
            if run_passed:
                c += 1
        return (n, c)

    @staticmethod
    def parse_pass_nc(result: EvaluateResult) -> dict[str, PassNC]:
        """From EvaluateResult, get PassNC (n, c) per eval set (key = eval set id)."""
        out: dict[str, PassNC] = {}
        for eid, set_result in result.results_by_eval_set_id.items():
            n, c = AgentEvaluator._nc_from_set(set_result.eval_results_by_eval_id, set_result.num_runs)
            out[eid] = PassNC(n=n, c=c)
        return out

    @staticmethod
    def pass_at_k(n: int, c: int, k: int) -> float:
        """Probability that at least one of k attempts succeeds. Delegates to _eval_pass.pass_at_k."""
        return _pass_at_k(n, c, k)

    @staticmethod
    def pass_hat_k(n: int, c: int, k: int) -> float:
        """Probability that all k consecutive runs succeed. Delegates to _eval_pass.pass_hat_k."""
        return _pass_hat_k(n, c, k)

    @staticmethod
    async def evaluate_eval_set(
        agent_module: str,
        eval_set: EvalSet,
        eval_config: Optional[EvalConfig] = None,
        num_runs: int = NUM_RUNS,
        agent_name: Optional[str] = None,
        print_detailed_results: bool = True,
        eval_set_results_manager: Optional[Any] = None,
        runner: Optional[Runner] = None,
        case_parallelism: Optional[int] = None,
        case_eval_parallelism: Optional[int] = None,
        callbacks: Optional[Callbacks] = None,
    ) -> tuple[Optional[dict], list[str], list[str], dict[str, list[EvalCaseResult]]]:
        """Evaluates an agent using the given EvalSet.

        Args:
            agent_module: The path to python module that contains the definition of
                the agent. There is convention in place here, where the code is going to
                look for 'root_agent' or `get_agent_async` in the loaded module.
            eval_set: The eval set.
            eval_config: The evaluation config.
            num_runs: Number of times all entries in the eval dataset should be
                assessed.
            agent_name: The name of the agent, if trying to evaluate something other
                than root agent. If left empty or none, then root agent is evaluated.
            print_detailed_results: When True, include Execution Details in
                details_lines; Evaluation Result summary is always in result_lines.
            eval_set_results_manager: Optional manager for saving eval set results
                (e.g. LocalEvalSetResultsManager).
            runner: Optional user-provided Runner; when set, use it as-is and only
                update its session when session_input exists and values are set in case.
            case_parallelism: Max concurrent case inferences. If None, use
                InferenceConfig default (4).
            case_eval_parallelism: Max concurrent cases for evaluation (scoring). If None,
                use EvaluateConfig default (4).
            callbacks: Optional lifecycle callbacks (before/after inference and evaluate).

        Returns:
            Tuple of (failed_summary or None, details_lines, result_lines, eval_results_by_eval_id).
            When print_detailed_results is False, details_lines is [].
        """
        if eval_config is None:
            raise ValueError("`eval_config` is required.")

        agent_for_eval = await AgentEvaluator._get_agent_for_eval(module_name=agent_module, agent_name=agent_name)
        eval_metrics = eval_config.get_eval_metrics()

        user_simulator_provider = UserSimulatorProvider(user_simulator_config=eval_config.user_simulator_config)

        # Step 1: Perform evals, basically inferencing and evaluation of metrics
        eval_results_by_eval_id = await AgentEvaluator._get_eval_results_by_eval_id(
            agent_for_eval=agent_for_eval,
            eval_set=eval_set,
            eval_set_results_manager=eval_set_results_manager,
            eval_metrics=eval_metrics,
            num_runs=num_runs,
            user_simulator_provider=user_simulator_provider,
            runner=runner,
            case_parallelism=case_parallelism,
            case_eval_parallelism=case_eval_parallelism,
            callbacks=callbacks,
        )

        # Step 2: Post-process the results
        failures: list[str] = []
        display_agent_name = agent_name or agent_module
        details_lines: list[str] = []
        result_lines: list[str] = []

        for eval_id, eval_results_per_eval_id in sorted(eval_results_by_eval_id.items()):
            eval_metric_results = (AgentEvaluator._get_eval_metric_results_with_invocation(eval_results_per_eval_id))
            sink = details_lines if print_detailed_results else None
            failures_per_eval_case = _RESULT_HANDLER.process_metrics_and_get_failures(
                eval_metric_results=eval_metric_results,
                print_detailed_results=print_detailed_results,
                agent_module=display_agent_name,
                eval_id=eval_id,
                eval_set_id=eval_set.eval_set_id,
                details_sink=sink,
            )
            failures.extend(failures_per_eval_case)

        summary = _RESULT_HANDLER.build_summary(
            eval_set=eval_set,
            eval_results_by_eval_id=eval_results_by_eval_id,
            agent_name=display_agent_name,
            num_runs=num_runs,
        )
        # Evaluation Result summary is always built and printed
        result_lines = _RESULT_HANDLER.build_evaluation_result_lines(
            summary,
            include_completed_line=False,
            include_agent_runs=False,
        )
        failed_summary = (_RESULT_HANDLER.summary_to_export_dict(summary) if failures else None)
        return (failed_summary, details_lines, result_lines, eval_results_by_eval_id)

    @staticmethod
    async def _get_agent_for_eval(module_name: str, agent_name: Optional[str] = None) -> BaseAgent:
        """Get agent for evaluation from a Python module.

        Supports both ADK Python strict convention and TRPC flexible loading:
        1. ADK convention: module.agent.root_agent or module.agent.get_agent_async()
        2. TRPC flexible: module.root_agent, module.get_agent_async(), module.get_agent()

        Args:
            module_name: Module path (e.g. "my_package.my_agent")
            agent_name: Optional name of specific agent to load

        Returns:
            The loaded agent instance

        Raises:
            ValueError: If agent cannot be found in module
        """
        module_path = f"{module_name}"
        agent_module_obj = importlib.import_module(module_path)

        root_agent = None

        # Try ADK convention first: module.agent.* or module name ends with .agent
        if hasattr(agent_module_obj, "agent") or module_name.endswith(".agent"):
            agent_module_with_agent = (agent_module_obj.agent
                                       if hasattr(agent_module_obj, "agent") else agent_module_obj)

            if hasattr(agent_module_with_agent, "root_agent"):
                root_agent = agent_module_with_agent.root_agent
            elif hasattr(agent_module_with_agent, "get_agent_async"):
                root_agent, _ = await agent_module_with_agent.get_agent_async()

        # Fallback to TRPC flexible convention
        if root_agent is None:
            if hasattr(agent_module_obj, "root_agent"):
                root_agent = agent_module_obj.root_agent
            elif hasattr(agent_module_obj, "get_agent_async"):
                root_agent, _ = await agent_module_obj.get_agent_async()
            elif hasattr(agent_module_obj, "get_agent"):
                root_agent = agent_module_obj.get_agent()

        if root_agent is None:
            raise ValueError(f"Module '{module_name}' does not have a 'root_agent', 'agent', "
                             f"'get_agent_async', or 'get_agent' attribute following either "
                             f"ADK or TRPC conventions.")

        agent_for_eval = root_agent
        if agent_name:
            agent_for_eval = root_agent.find_agent(agent_name)
            assert agent_for_eval, f"Sub-Agent `{agent_name}` not found."

        return agent_for_eval

    @staticmethod
    def _load_eval_set_from_file(
        eval_set_file: str,
        eval_config: EvalConfig,
    ) -> EvalSet:
        """Load evaluation set from JSON file.

        Supports ADK-style syntax: "file.json:eval_case_id" to select a single case.
        Session input is specified per case in the EvalSet file (session_input).

        Args:
            eval_set_file: Path to the JSON file, optionally with ":eval_case_id" suffix
            eval_config: Evaluation configuration (for compatibility with ADK)

        Returns:
            Loaded EvalSet instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file format is invalid or eval case not found
        """
        # Check if file_path contains a case selector (ADK style: "file.json:case_id")
        selected_case_id = None
        actual_file_path = eval_set_file

        if ":" in eval_set_file:
            parts = eval_set_file.split(":", 1)
            actual_file_path = parts[0]
            selected_case_id = parts[1]

        if not os.path.exists(actual_file_path):
            raise FileNotFoundError(f"Eval set file not found: {actual_file_path}")

        with open(actual_file_path, "r", encoding="utf-8") as f:
            content = f.read()

        try:
            eval_set = EvalSet.model_validate_json(content)

            # If a specific case was selected, filter the eval set
            if selected_case_id:
                matching_cases = [case for case in eval_set.eval_cases if case.eval_id == selected_case_id]

                if not matching_cases:
                    raise ValueError(f"Eval case '{selected_case_id}' not found in {actual_file_path}. "
                                     f"Available cases: {[c.eval_id for c in eval_set.eval_cases]}")

                # Create a new eval set with only the selected case
                eval_set = EvalSet(
                    eval_set_id=f"{eval_set.eval_set_id}_{selected_case_id}",
                    name=f"{eval_set.name} - {selected_case_id}",
                    description=eval_set.description,
                    eval_cases=matching_cases,
                )

            return eval_set
        except Exception as ex:
            raise ValueError(f"Failed to load eval set from {actual_file_path}: {ex}")

    @staticmethod
    def _load_config_from_file(file_path: Optional[str]) -> EvalConfig:
        """Load evaluation config from JSON file.

        Args:
            file_path: Path to the config JSON file (optional)

        Returns:
            Loaded EvalConfig instance or default config
        """
        if file_path is None or not os.path.exists(file_path):
            # Return default config
            return EvalConfig(criteria={
                "tool_trajectory_avg_score": 1.0,
                "response_match_score": 0.7,
            })

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        try:
            eval_config = EvalConfig.model_validate_json(content)
            return eval_config
        except Exception as ex:
            raise ValueError(f"Failed to load config from {file_path}: {ex}")

    @staticmethod
    def _get_eval_sets_manager(app_name: str, eval_set: EvalSet) -> EvalSetsManager:
        """Create and populate an in-memory eval sets manager.

        Args:
            app_name: Application name
            eval_set: The eval set to add

        Returns:
            Populated EvalSetsManager
        """
        eval_sets_manager = InMemoryEvalSetsManager()

        eval_sets_manager.create_eval_set(app_name=app_name, eval_set_id=eval_set.eval_set_id)
        for eval_case in eval_set.eval_cases:
            eval_sets_manager.add_eval_case(
                app_name=app_name,
                eval_set_id=eval_set.eval_set_id,
                eval_case=eval_case,
            )

        return eval_sets_manager

    @staticmethod
    async def _get_eval_results_by_eval_id(
        agent_for_eval: BaseAgent,
        eval_set: EvalSet,
        eval_metrics: list,
        num_runs: int,
        user_simulator_provider,
        eval_set_results_manager: Optional[Any] = None,
        runner: Optional[Runner] = None,
        case_parallelism: Optional[int] = None,
        case_eval_parallelism: Optional[int] = None,
        callbacks: Optional[Callbacks] = None,
    ) -> dict[str, list[EvalCaseResult]]:
        """Returns EvalCaseResults grouped by eval case id.

        The grouping happens because of the "num_runs" argument, where for any value
        greater than 1, we would have generated inferences num_runs times and so
        by extension we would have evaluated metrics on each of those inferences.

        Args:
            agent_for_eval: The agent to evaluate
            eval_set: The eval set
            eval_metrics: List of metrics to evaluate
            num_runs: Number of times to run each eval case
            user_simulator_provider: Provider for user simulators
            eval_set_results_manager: Optional manager for saving eval set results
            runner: Optional user-provided Runner; when set, use it as-is and only
                update its session when session_input exists and values are set in case.
            case_parallelism: Max concurrent case inferences. If None, use
                InferenceConfig default (4).
            case_eval_parallelism: Max concurrent cases for evaluation (scoring). If None,
                use EvaluateConfig default (4).
            callbacks: Optional lifecycle callbacks (before/after inference and evaluate).

        Returns:
            Dictionary mapping eval_id to list of EvalCaseResult
        """
        from ._eval_service_base import (
            InferenceRequest,
            InferenceConfig,
            EvaluateRequest,
            EvaluateConfig,
        )

        # app_name: evalset.app_name or configured default (case session_input.app_name overrides per case)
        request_app_name = eval_set.app_name or DEFAULT_EVAL_APP_NAME

        eval_service = LocalEvalService(
            root_agent=agent_for_eval,
            eval_sets_manager=AgentEvaluator._get_eval_sets_manager(app_name=request_app_name, eval_set=eval_set),
            user_simulator_provider=user_simulator_provider,
            eval_set_results_manager=eval_set_results_manager,
            runner=runner,
            callbacks=callbacks,
        )

        inference_config = (InferenceConfig(
            parallelism=case_parallelism) if case_parallelism is not None else InferenceConfig())
        inference_requests = [
            InferenceRequest(
                app_name=request_app_name,
                eval_set_id=eval_set.eval_set_id,
                inference_config=inference_config,
            )
        ] * num_runs  # Repeat inference request num_runs times.

        # Generate inferences
        inference_results = []
        for run_id, inference_request in enumerate(inference_requests, start=1):
            async for inference_result in eval_service.perform_inference(inference_request=inference_request):
                inference_result.run_id = run_id
                inference_results.append(inference_result)

        # Evaluate metrics
        # As we perform more than one run for an eval case, we collect eval results
        # by eval id.
        eval_results_by_eval_id: dict[str, list[EvalCaseResult]] = {}
        evaluate_config = (EvaluateConfig(eval_metrics=eval_metrics, parallelism=case_eval_parallelism)
                           if case_eval_parallelism is not None else EvaluateConfig(eval_metrics=eval_metrics))
        evaluate_request = EvaluateRequest(
            inference_results=inference_results,
            evaluate_config=evaluate_config,
        )

        async for eval_result in eval_service.evaluate(evaluate_request=evaluate_request):
            eval_id = eval_result.eval_id
            if eval_id not in eval_results_by_eval_id:
                eval_results_by_eval_id[eval_id] = []

            eval_results_by_eval_id[eval_id].append(eval_result)

        return eval_results_by_eval_id

    @staticmethod
    def _get_eval_metric_results_with_invocation(
        eval_results_per_eval_id: list[EvalCaseResult], ) -> dict[str, list[_utils.MetricRunRecord]]:
        """Returns MetricRunRecord grouped by metric.

        EvalCaseResult contain results for each metric per invocation.

        This method flips it around and returns a structure that groups metric
        results per invocation by eval metric.

        This is a convenience function.

        Args:
            eval_results_per_eval_id: List of eval case results for the same eval_id

        Returns:
            Dictionary mapping metric names to lists of results with invocations
        """
        eval_metric_results: dict[str, list[_utils.MetricRunRecord]] = {}

        # Go over the EvalCaseResult one by one
        for eval_case_result in eval_results_per_eval_id:
            # For the given eval_case_result, we go over metric results for each
            # invocation
            for eval_metrics_per_invocation in eval_case_result.eval_metric_result_per_invocation:
                # Go over each eval_metric_result for an invocation
                for eval_metric_result in eval_metrics_per_invocation.eval_metric_results:
                    metric_name = eval_metric_result.metric_name
                    if metric_name not in eval_metric_results:
                        eval_metric_results[metric_name] = []

                    actual_invocation = eval_metrics_per_invocation.actual_invocation
                    expected_invocation = eval_metrics_per_invocation.expected_invocation

                    eval_metric_results[metric_name].append(
                        _utils.MetricRunRecord(
                            actual_invocation=actual_invocation,
                            expected_invocation=expected_invocation,
                            eval_metric_result=eval_metric_result,
                        ))
        return eval_metric_results
