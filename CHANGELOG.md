# Changelog

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
