# Issue #89 Usage Guide: Session/Memory Replay Consistency Test Framework

## Quick Start

### 1. Run Core Tests (InMemory vs SQLite)

```bash
# Core E2E tests — always run, no external dependencies
python -m pytest tests/sessions/test_replay_consistency.py -v

# Expected output:
# test_all_cases_inmemory_vs_sqlite PASSED
# test_case_count PASSED
# test_report_generation PASSED
# test_empty_session PASSED
# ================== 4 passed in 3.2s ==================
```

### 2. Run All Unit Tests

```bash
python -m pytest tests/sessions/replay_consistency/ -v

# Expected output:
# test_normalizer.py: 15 passed
# test_comparator.py: 15 passed
# test_cases.py: 9 passed
# ================== 39 passed in 1.5s ==================
```

### 3. Run with Redis (Optional)

```bash
# Set REDIS_URL and run Redis tests
# Windows (PowerShell):
$env:REDIS_URL = "redis://localhost:6379"
python -m pytest tests/sessions/test_replay_redis.py -v

# Linux/macOS:
REDIS_URL=redis://localhost:6379 python -m pytest tests/sessions/test_replay_redis.py -v

# Without REDIS_URL, tests automatically skip:
# test_redis_available SKIPPED (REDIS_URL not set)
```

### 4. Run Full Suite

```bash
python -m pytest tests/sessions/ tests/memory/ -v
```

## Replay Case Format (JSONL)

Each fixture file (`.jsonl`) contains one JSON object per line:

```jsonl
{"type":"case_header","name":"single_turn_text","app_name":"test-app","user_id":"user-1","session_id":"session-001","initial_state":{"app:welcome":"true"}}
{"type":"event","author":"user","role":"user","text":"Hello, who are you?"}
{"type":"event","author":"assistant","role":"assistant","text":"I am an AI assistant."}
{"type":"memory_write","memory":"User greeted the assistant","topics":["conversation"]}
{"type":"memory_query","query":"greeting","limit":5}
```

## Adding a New Replay Case

1. Define the case in `tests/sessions/replay_consistency/cases.py`:
```python
def _case11_custom_scenario() -> ReplayCase:
    return ReplayCase(
        name="custom_scenario",
        session_id="session-011",
        events=[
            EventSpec(author="user", role="user", text="Custom input."),
            EventSpec(author="assistant", role="assistant", text="Custom output."),
        ],
        memory_writes=[
            MemoryWriteSpec(memory="Test memory", topics=["test"]),
        ],
        memory_queries=[MemoryQuerySpec(query="test", limit=5)],
    )
```

2. Add it to `_replay_cases()`:
```python
def _replay_cases() -> list[ReplayCase]:
    return [
        ...
        _case11_custom_scenario(),
    ]
```

3. Generate the JSONL fixture:
```python
from tests.sessions.replay_consistency.cases import _case11_custom_scenario, save_case_to_jsonl
save_case_to_jsonl(_case11_custom_scenario(), "tests/sessions/replay_consistency/fixtures/case_011_custom.jsonl")
```

## Diff Report Format

When differences are found, the report shows:

```json
{
  "case": "single_turn_text",
  "diffs": [
    {
      "section": "events",
      "path": "events[0].text",
      "left": "Hello, who are you?",
      "right": "Hello, who are you",
      "allowed": false,
      "reason": "text content differs"
    }
  ],
  "summary": "1 diff(s) found, 0 allowed"
}
```

## Reproducing Results

```bash
# 1. Clone and set up
git clone https://github.com/trpc-group/trpc-agent-python
cd trpc-agent-python
pip install -e ".[dev]"

# 2. Run full replay consistency test suite
python -m pytest tests/sessions/replay_consistency/ tests/sessions/test_replay_consistency.py -v --tb=short

# 3. Optionally with Redis
# $env:REDIS_URL = "redis://localhost:6379"
python -m pytest tests/sessions/test_replay_redis.py -v --tb=short
```
