# Evaluation + Optimization Pipeline

Auto-regression testing and prompt optimization closed loop built with
tRPC-Agent SDK + Hy3 LLM.

## Features

- **Eval cases**: Built-in test cases with keyword-based scoring
- **Auto-regression**: Compare scores across prompt iterations
- **Optimization**: Iterative prompt improvement based on eval feedback
- **Extensible**: Add custom eval cases and scoring functions

## Quick Start

```bash
export TRPC_AGENT_API_KEY=your-key
export TRPC_AGENT_BASE_URL=https://tokenhub.tencentmaas.com/v1
python run_agent.py
```

## Pipeline

```
Eval Cases → score_response → optimize_prompt → new prompt → re-eval → regress
```
