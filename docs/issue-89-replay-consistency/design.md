# Issue #89 设计说明: Replay Consistency Test Framework

## 架构

```
tests/sessions/
├── replay_consistency/
│   ├── cases.py        — 10 个 ReplayCase 定义 + JSONL load/save
│   ├── normalizer.py   — Event/Snapshot 归一化（strip 自动生成字段）
│   ├── comparator.py   — recursive_diff() 递归比较 + DiffEntry 输出
│   └── fixtures/       — 10 个 .jsonl replay fixture 文件
├── test_replay_consistency.py  — 主测试入口（InMemory vs SQLite E2E）
└── test_replay_redis.py        — Redis 测试（REDIS_URL env var gated）
```

## 设计决策

1. **模块化拆分** — cases/normalizer/comparator 各自独立，可被其他测试复用
2. **JSONL fixtures** — 10 个 replay case 以 JSONL 格式持久化，一行一个操作步骤。支持 round-trip
3. **轻量+集成双模式** — Redis 通过 env var 按需启用，不可用时优雅跳过
4. **对齐 Go 版** — 10 个 case 定义、DiffEntry 格式、normalizer/comparator 行为均对齐 `trpc-agent-go/session/replaytest/`
5. **allowed_diff 机制** — DiffEntry.allowed 字段预留，timestamp/auto-generated ID 等差异可标记为允许

## 依赖

- `pytest` + `pytest-asyncio` (测试框架)
- `trpc_agent_sdk.sessions` (InMemory/SQL/Redis session services)
- `trpc_agent_sdk.memory` (InMemory/SQL/Redis memory services)
