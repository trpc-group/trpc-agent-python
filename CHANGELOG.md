# Changelog

## [1.0.0](https://github.com/trpc-group/trpc-agent-python/releases/tag/v1.0.0) (2026-04-06)

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
