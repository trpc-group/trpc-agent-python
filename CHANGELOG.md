# Changelog

## [1.1.13](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.13) (2026-07-17)

### Features

* Plan Mode: Added Claude Code-style Plan Mode (`setup_plan` / `PlanToolSet`), so agents can enter a design-and-approval phase before implementation. Supports model-initiated entry via `enter_plan_mode` and user/UI-driven entry through session state, with plan drafting (`update_plan_content`), clarifying questions, approval gating (`exit_plan_mode`), and write-tool restrictions while planning.
* Tools: Added Tavily as a provider for `WebSearchTool` and `WebFetchTool`. Search can return LLM-ready answers plus optional image hits; fetch can use Tavily Extract as an alternative to direct HTTP fetching.
* AG-UI: Expanded long-running tool discovery so nested `ToolSet` tools (including Plan Mode tools) are recognized during AG-UI runs, and tool names can be resolved from session history when the client payload only carries a tool call id.

### Bug Fixes

* AG-UI: Fixed session state updates from the AG-UI protocol not being persisted. State-change events are now appended as non-partial events so session services apply them correctly.
* Runner: Avoided repeated string concatenation while accumulating streaming partial text. Partial chunks are kept as a list and joined only when cancellation cleanup needs the full text.
* Examples: Fixed a few example agents (LangGraph and Mem0) so the full example pipeline can run more reliably.

### Docs

* Docs: Added English and Chinese Plan Mode guides, plus dedicated pages for TodoWrite, Task, and Goal tools.
* Docs: Documented Tavily configuration and usage for web search and web fetch.
* Examples: Added `plan_mode` and `plan_mode_with_goal_and_task` AG-UI examples for trying Plan Mode end to end.

### Internal

* CI: Added a GitHub Actions release workflow for publishing releases.
* CI: Added code-review helper prompts and scripts under `.github/code_review/`.
* CI: Added `pipeline_test/run_all_examples.sh` to drive the full examples pipeline more consistently.

## [1.1.12](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.12) (2026-07-10)

### Features

* Agent: Added support for dynamically created sub-agents to forward runtime events directly into the parent agent event stream, so callers can observe child-agent progress, tool activity, and final outputs without waiting for the whole delegated task to finish.
* Agent: Added dynamic sub-agent creation support, allowing agents to create and use child agents at runtime for more flexible task decomposition and delegation.
* Goal: Added Goal support aligned with the Go implementation, giving agents a structured way to carry task objectives through the execution flow.
* A2A: Added optional `app_name` support to `TrpcA2aAgentService`, allowing the Runner app identity to differ from the exposed A2A service name while keeping the existing `service_name` fallback behavior.
* Session: Updated `list_sessions()` so `user_id` can be omitted. When `user_id=None`, InMemory, SQL, Redis, and Eval session services now return all sessions under the specified `app_name` without loading session events.
* Skill: Added the `skills_hub` module to support centralized skill discovery and management.

### Bug Fixes

* Graph: Fixed `GraphAgent` `AgentNode.last_response` so it no longer records thinking text or intermediate tool-call round text as the node's final response. The graph now uses `Event.is_final_response()` and removes thinking content before saving the last response.
* A2A: Fixed internal pipeline example scripts and paths so the example workflow can be triggered and run with the expected files.
* Docs: Fixed README optional dependency installation commands by quoting extras, removing extra spaces, and normalizing package-extra casing so shell parsing works correctly.

### Docs

* Docs: Added MkDocs site entry pages and navigation for the existing English and Chinese documentation, plus a GitHub Pages workflow so the README documentation badge can point to a published documentation site.
* Docs: Added documentation and test coverage for listing sessions across all users under an app by passing `user_id=None`.

### Internal

* CI: Added and adjusted internal pipeline test trigger files used by repository automation.

## [1.1.11](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.11) (2026-06-26)

### Features

* Model: Added SDK-managed model retry support for `OpenAIModel`, `AnthropicModel`, and `LiteLLMModel`, including `ModelRetryConfig`, `ExponentialBackoffConfig`, provider-aware retry decisions from headers / HTTP status / SDK exceptions, `Retry-After` and `retry-after-ms` handling, and full-jitter exponential backoff. Streaming retries are guarded so the SDK only replays a model call before any user-visible content has been emitted, avoiding duplicated partial text or tool calls.
* Model: Refactored HTTP client lifecycle management around `http_client_provider_factory`, adding explicit temporary and shared HTTP client providers plus `close_shared_http_clients()` so callers can choose per-request clients by default or opt in to connection reuse with bounded `httpx.AsyncClient` pooling. OpenAI and Anthropic model tests and documentation were updated to cover provider-owned client injection and cleanup behavior.
* Tools: Added a Claude Code-style `TodoWriteTool` that lets agents maintain a structured todo list in branch-scoped session state, with validation for complete-list replacement, unique items, and at most one `in_progress` item. Added examples for normal todo usage and human-in-the-loop todo workflows.
* Tools: Added `TaskToolSet` with `task_create`, `task_update`, `task_get`, and `task_list` tools, providing persistent structured task boards with server-assigned task ids, status updates, dependency edges, and single-in-progress enforcement. Added task tool examples and unit coverage for task lifecycle behavior.
* Skill: Added `LinkSkillStager` and renamed the file-system stager module from copy-oriented naming to file-oriented naming, allowing skills to be staged into workspaces through links while preserving the shared workspace directories required by code execution and skill artifacts.
* Skill: Added cached filesystem skill repositories via `CachedFsSkillRepository` and `use_cached_repository=True` in `create_default_skill_repository()`, caching `SKILL.md` front matter and body by file signature to reduce repeated skill scanning and loading overhead while still invalidating entries when files change or are deleted.
* Code Execution: Extended workspace staging and runtime metadata to support link-mode staging, explicit workspace stage options, TTY flags, and `work/inputs` layout initialization so skill-provided files can be prepared before skill loading and code execution steps run.
* Examples/Docs: Added runnable examples and documentation for model retry, todo tools, task tools, shared HTTP client configuration, skill link staging, cached skill repositories, and tool usage updates across English and Chinese docs.

### Bug Fixes

* Model: Fixed loss of normal assistant text when a streaming OpenAI-compatible response contains both text and a tool call. The final non-partial response now keeps user-visible text while still converting parsed tool calls into structured `function_call` parts, preventing text that appeared in the stream from being dropped from session history and later model context.
* Model: Updated LiteLLM retry and error handling so normalized LiteLLM exception headers and status codes participate in the same retry decisions as OpenAI and Anthropic, and so failures after partial streaming output are surfaced as final errors instead of replayed.
* Skill: Ensured workspace layout creates `work/inputs` up front, avoiding races where code or skill commands attempt to copy input files before `skill_load` has linked or initialized the input directory.
* Telemetry: Updated model metrics reporting to align with the retry wrapper and renamed metric attributes so model calls report consistent retry-aware execution data.

## [1.1.10](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.10) (2026-06-18)

### Bug Fixes

* Model: fix error about pickle of OpenAIModel.


## [1.1.9](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.9) (2026-06-18)

### Features

* Model: Added `http_client_factory` support to `OpenAIModel`, allowing callers to inject a custom `httpx.AsyncClient` factory to control HTTP connection lifecycle and pool settings such as keepalive expiry (#83).

### Bug Fixes

* Telemetry: Switched `agent_run` and `invocation` spans back to `start_as_current_span` so child spans such as `call_llm` inherit the correct parent context, restoring complete trace attributes (including system instructions and tools) in Langfuse reporting.

## [1.1.8](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.8) (2026-06-12)

### Features

* Session: Reworked session history storage from `Event.model_flags`-based model visibility to an active/historical split, with `Session.events` holding the active model window and `Session.historical_events` optionally retaining events moved out by max-event filtering, TTL, or summarization.
* Session: Added `SessionServiceConfig.store_historical_events`, updated Redis, SQL, and InMemory persistence semantics for active/historical events, and kept list APIs lightweight by omitting both active and historical events from `list_sessions()`.
* Session: Optimized summarization by keeping `[summary_event, recent_events...]` as the new active window and checking only the leading summary anchor instead of repeatedly scanning the event list.
* Model: Added configuration support for OpenAI/Anthropic APIs and LiteLLM prompt cache.

### Bug Fixes

* Telemetry: Propagated span context correctly in async generators by using `start_span` with context attach/detach, and fixed member-agent input tracing to prefer `override_messages` over `user_content`.

## [1.1.7](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.7) (2026-06-05)

### Bug Fixes

* Runner: Added `close_session_service_on_close` and `close_memory_service_on_close` controls so short-lived runners can skip closing externally managed session and memory services, such as shared Redis-backed services.
* MCP: Updated Streamable HTTP session creation to prefer the non-deprecated `streamable_http_client` API, with fallback support for older MCP SDKs that only expose `streamablehttp_client`.
* MCP: Moved Streamable HTTP headers and timeout configuration onto an owned `httpx.AsyncClient`, avoiding deprecated transport arguments while keeping the HTTP client lifecycle tied to the MCP session context.
* Storage: Fixed frequent sqlite warnings in `SqlSessionService` by consistently using database-side `func.now()` for update timestamps.


## [1.1.6](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.6) (2026-06-03)

### Features

* Skill: Added a recoverable Cube sandbox runtime for skills, including `CubeClientConfig`, a unified `create_cube_sandbox_client` entry point, optional `auto_recover` support in `CubeSandboxClient`, sandbox lifecycle helpers, and direct `CubeWorkspaceRuntime` creation from the client.
* Skill: Unified skill load/run/exec/stager paths around repository-level workspace runtime resolution via `repository.get_workspace_runtime(ctx)`, so tools under the same skill repository share one workspace runtime context.
* MCP: Added MCP tool caching to avoid repeated network access.
* Tools: Added `GraphAgent` support in `AgentTool`, allowing wrapped graph agents to return results from tool context state.
* Examples/Eval: Restored evaluation examples that were previously removed during open-source cleanup.
* Optimizer: Added support for the prompt self-optimization `AgentOptimizer`.

### Bug Fixes

* Storage: Fixed frequent sqlite warnings in `SqlSessionService` by consistently using database-side `func.now()` for update timestamps.

## [1.1.5](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.5) (2026-05-19)

### Features

* Tools: Added `StreamingProgressTool` with matching `ToolsProcessor` plumbing so tools can surface intermediate progress as `partial=True` events while still emitting a single final `function_response`; included `BaseTool` streaming hooks, the `llmagent_with_streaming_progress_tool` example and verification script.
* Eval: Added `RemoteEvalService` to drive evaluations against agents exposed over remote interfaces, refactored `AgentEvaluator` to support remote agent calls, and expanded English/Chinese evaluation docs.
* Model: Landed the OpenAI-compatible adapter layer (`models/openai_adapter/{_base,_deepseek,_hunyuan}.py`) that isolates provider-specific behavior from `OpenAIModel`, including DeepSeek v4 thinking / `response_format` / `reasoning_content` / token usage handling and hy3-preview ToolPrompt text parsing with streaming filter.
* Examples: Added `examples/mempalace_mcp` (MemPalace via MCP) and updated `examples/llmagent_with_thinking` to enable `add_tools_to_prompt` only for hy3-preview and display thinking / tool calls / final answer separately.

* Utils: Added `json_loads_repair` and `json_repair_string` helpers (backed by `json_repair`) under `trpc_agent_sdk.utils`, with full unit test coverage.
* Model/Tools: Adopted `json_repair` only on JSON-tolerant paths — `JsonToolPrompt` / `XmlToolPrompt` parse_function, non-streaming OpenAI tool-call args, `AgentTool` structured-output validation, skills tool result parsing — while keeping strict `json.loads` for the streaming tool-call accumulator (to preserve "wait for next chunk" semantics) and Hunyuan plain-text `<arg_value>` parsing (to avoid silently coercing plain text into empty strings).
* Model: Fixed ToolPrompt streaming parsing so multiple tool calls in a single response are all preserved instead of only the last one being kept.

### Bug Fixes

* Teams: TeamAgent now honors `actions.skip_summarization` from custom tool events, so tools like `AgentTool(skip_summarization=True)` and `StreamingProgressTool(skip_summarization=True)` end the leader loop without an extra summarization turn (previously masked by leader's `disable_react_tool=True`).

## [1.1.4](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.4) (2026-05-13)

### Bug Fixes

* Tools: Removed default `mempalace_tool` exports from `trpc_agent_sdk.tools` to avoid forcing MemPalace optional dependencies during base package import.

## [1.1.3](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.3) (2026-05-12)

### Features

* Model: Added an OpenAI-compatible adapter layer to isolate provider-specific behaviors, including DeepSeek v4 reasoning/format handling and hy3-preview tool-prompt parsing support.
* Memory: Added MemPalace integration with `MemPalaceMemoryService` and `mempalace_tool`, plus related examples and documentation.
* Code Execution: Added Cube/E2B sandbox executor and workspace runtime with optional dependency support and end-to-end example coverage.
* Eval: Added support for evaluating the same metric across different LLMs.

### Bug Fixes

* Model: Fixed ToolPrompt streaming parsing so multiple tool calls in one response are preserved instead of only the last call.
* Storage: Improved SQL storage compatibility by filtering empty content parts, fixing MySQL `DynamicPickleType` serialization, and stabilizing session timestamp updates.
* Eval: Fixed judge-agent JSON output handling in the eval module.
* CI: Added missing `e2b-code-interpreter` test dependency to prevent cube test collection failures.

## [1.1.2.post1](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.2.post1) (2026-04-29)

### Features

* Session: Updated session summarization to retain full conversation history while marking summarized events as model-invisible.
* Session: Added backend-threaded summarization execution to avoid blocking front-end conversation turns.
* Skill: Added multi-user support for skill operations.

### Bug Fixes

* Code Execution: Fixed the conflict between code execution and tool invocation where tool data could be lost after code execution.
* MCP: Added support for parsing and returning multiple MCP tool results in a single response.

## [1.1.2](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.2) (2026-04-24)

### Features

* Telemetry: Added OpenTelemetry metrics reporting and introduced `custom_metrics` to support framework metric reporting when parsing remote agent responses.
* Tools: Added `web_search` with DuckDuckGo and Google providers, and added `web_fetch` for webpage content retrieval.
* Docs/Examples: Added usage documentation and examples for `web_search` and `web_fetch`.

### Bug Fixes

* Teams: Fixed parallel delegation signal loss and enabled streaming output in team delegation flows.

## [1.1.1](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.1) (2026-04-20)

### Features

* Storage: Added the `usage_metadata` field to SQL storage and introduced automatic migration for missing columns.
* Skill: Added cross-session skill state persistence so loaded skills can be reused across sessions, reducing repeated skill loading and unnecessary retry turns.
* Skill: Added skill install/uninstall awareness so the model can detect skill lifecycle changes and avoid missing-skill lookups or calls to uninstalled skills.
* Session: Added `usage_metadata` support in SQL session storage for persisting and reading token usage statistics.

### Bug Fixes

* Skill: Reduced hallucinated skill command generation when users intend to run commands, lowering invalid command attempts and retry loops.


## [1.1.0](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.1.0) (2026-04-07)

### Features

* Unified Agent framework with `LlmAgent`, `LangGraphAgent` and `TransferAgent`
* Multi-agent orchestration with built-in `Chain`, `Parallel`, and `Cycle` patterns, plus Team and nested Team collaboration
* Human-in-the-loop workflows with pause, review, and resume support for long-running tasks
* Rich tool ecosystem including built-in file/shell tools, MCP tools, LangChain tools, and extensible third-party integrations
* Extensible Skill system with local and HTTP distribution, dynamic loading, timeout control, and sandbox execution
* Code execution support with async runtime and sandbox/container execution options
* Session and memory services with in-memory, Redis, and SQL backends, including filtering, summarization, and scheduled cleanup
* RAG and knowledge capabilities through `LangchainKnowledge` with loaders, splitters, embedders, vector stores, retrievers, and prompt templates
* Evaluation framework with trajectory and response quality assessment, LLM-judge metrics, parallel evaluation, and JSON reporting
* Service and protocol integrations for A2A, AG-UI, and OpenClaw runtime scenarios
* OpenClaw runtime capabilities for gateway/chat/ui/deps workflows with pluggable channels, tools, skills, session and memory integration
* OpenClaw skill dependency management with profile-based inspection and install planning for common runtime environments
* Observability via tracing support, including end-to-end execution flow, tool-call traces, and cancellation traces
* Developer experience support with practical examples and DebugServer for local development and validation
