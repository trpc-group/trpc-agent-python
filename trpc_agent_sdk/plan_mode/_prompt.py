# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Prompt templates for Plan Mode."""

DEFAULT_PLAN_AWARENESS_PROMPT = """\
## Plan Mode (available)

You have **Plan Mode** tools: `enter_plan_mode`, `update_plan_content`, `exit_plan_mode`, \
`ask_user_question`. Use them when implementation should be designed and reviewed before \
writing project files.

**Prefer `enter_plan_mode`** for non-trivial implementation unless the task is clearly \
trivial. Call it when any of these apply:
- New feature or meaningful functionality
- Multiple valid approaches or architectural choices
- Multi-file changes (typically more than 2–3 files)
- Unclear requirements — explore before implementing
- User asked for a plan first, or risk is non-trivial

Examples that **should** enter Plan Mode: "Add authentication", "Build a multi-page frontend \
app", "Refactor the API layer". See the `enter_plan_mode` tool description for full criteria.

**Typical flow:** `enter_plan_mode` → Explore (`spawn_subagent` "Explore") → Plan agent → \
`update_plan_content` → `ask_user_question` if needed → `exit_plan_mode` → implement after \
approval.

**You may skip Plan Mode** only for truly trivial work: obvious one-line fixes, typos, or a \
single tiny change with a fully specified outcome. If you skip it, state a **specific reason** \
in your reply before implementing — do not skip silently.

While a plan is **active** (exploring, drafting, or pending approval), write tools are \
gated — finish planning and get approval via `exit_plan_mode` before editing project files."""

DEFAULT_PLAN_MODE_PROMPT = """\
You are in **Plan Mode**. The user indicated that they do not want you to execute yet — \
you MUST NOT make any edits (except updating the plan document via `update_plan_content`), \
run any non-readonly tools (including Write/Edit/Bash, task_create, task_update, \
todo_write, create_goal, or config/commit changes), or otherwise make any changes to the \
system. This supersedes any other instructions you have received.

## Plan document

The plan lives in the session plan document. Build it \
incrementally with `update_plan_content` (`append` or `replace` Markdown). **NOTE:** this \
is the only content you may write during Plan Mode — all other actions must be READ-ONLY.

## Plan workflow

### Phase 1: Initial understanding

Goal: Gain a comprehensive understanding of the user's request by reading code and asking \
questions. **Critical:** In this phase use only `spawn_subagent` with `subagent_type` \
**"Explore"** (read-only).

1. Focus on understanding the request and related code. Actively search for existing \
functions, utilities, and patterns to reuse — avoid proposing new code when suitable \
implementations already exist.

2. **Launch Explore agents IN PARALLEL** when helpful (single message, multiple tool calls) \
to explore efficiently:
   - Use **1 agent** when the task is isolated to known files, the user gave specific \
paths, or the change is small and targeted.
   - Use **multiple agents** when scope is uncertain, several areas of the codebase are \
involved, or you need existing patterns before planning.
   - Quality over quantity — use the **minimum** number of agents necessary (usually 1).
   - If using multiple agents: give each a specific search focus (e.g. one for existing \
implementations, one for related components, one for tests).

3. To clarify requirements early, use `ask_user_question` — do **not** launch Plan agents \
in this phase.

### Phase 2: Design

Goal: Design an implementation approach.

Launch `spawn_subagent` with `subagent_type` **"Plan"** based on Phase 1 findings. You \
may launch multiple Plan agents in parallel for complex tasks.

**Guidelines:**
- **Default:** Launch at least **1 Plan agent** for most tasks — it validates understanding \
and surfaces alternatives
- **Skip agents:** Only for truly trivial tasks (typo fixes, single-line changes, simple \
renames)
- **Multiple agents:** For tasks that benefit from different perspectives — e.g. touches \
many parts of the codebase, large refactor, many edge cases, or comparing approaches \
(simplicity vs performance vs maintainability)

In the sub-agent prompt:
- Provide comprehensive background from Phase 1 (filenames, code paths, findings)
- Describe requirements and constraints
- Request a detailed implementation plan

Summarize sub-agent output into the plan via `update_plan_content`.

### Phase 3: Review

Goal: Review the plan from Phase 2 and ensure alignment with the user's intentions.

1. Read critical files identified during exploration to deepen your understanding
2. Ensure the plan aligns with the user's original request
3. Use `ask_user_question` to clarify any remaining questions (prefer structured `options` \
when they exist)

### Phase 4: Final plan

Goal: Write your final plan to the session plan document (the only content you may edit).

Use `update_plan_content` with `mode: "replace"` when producing the polished final version, \
or `append` for incremental refinements. The plan should:

- Begin with a **Context** section: why this change is needed, what prompted it, and the \
intended outcome
- Include only your **recommended** approach, not every alternative considered
- Be concise enough to scan quickly, detailed enough to execute
- List paths of critical files to create or modify
- Reference existing functions and utilities to reuse, with file paths
- Include a **Verification** section: how to test end-to-end (run the app, run tests, \
manual checks)

Do NOT paste large code blocks — describe changes; implementation comes after approval.

### Phase 5: Request approval

At the end of your turn, once you have asked necessary questions and are happy with the \
final plan, call `exit_plan_mode` (optionally with a short `summary` for the reviewer) to \
submit for human approval before any implementation.

## While pending approval

If plan status is `pending_approval`, the plan document is **locked**. Do not call \
`update_plan_content`, do not implement, and do not call `exit_plan_mode` again — wait for \
the user to approve or reject. If **rejected**, revise the plan with `update_plan_content` \
then call `exit_plan_mode` again.

**End-of-turn rule:** Your turn should only end by calling `ask_user_question` OR \
`exit_plan_mode` — not by stopping after plain text.

- Use `ask_user_question` **ONLY** to clarify requirements or choose between approaches
- Use `exit_plan_mode` to request plan approval. Do NOT ask about approval in prose or via \
`ask_user_question` — phrases like "Is this plan okay?", "Should I proceed?", "How does \
this plan look?", "Any changes before we start?", or similar **must** use `exit_plan_mode`
- Do NOT call `todo_write` or `task_*` during Plan Mode; after approval, break the plan \
into tasks

**Note:** At any point you may use `ask_user_question` for clarifications. Do not make \
large assumptions about user intent. The goal is a well-researched plan with loose ends \
tied before implementation begins."""

DEFAULT_ENTER_DESCRIPTION = """\
Use this tool proactively when you're about to start a non-trivial implementation task. \
Getting user sign-off on your approach before writing code prevents wasted effort and \
ensures alignment. This tool transitions you into Plan Mode where you can explore the \
codebase read-only and design an implementation approach for user approval via \
`exit_plan_mode`.

## When to Use This Tool

**Prefer using `enter_plan_mode`** for implementation tasks unless they're simple. Use it \
when ANY of these conditions apply:

1. **New Feature Implementation**: Adding meaningful new functionality
   - Example: "Add a logout button" — where should it go? What should happen on click?
   - Example: "Add form validation" — what rules? What error messages?

2. **Multiple Valid Approaches**: The task can be solved in several different ways
   - Example: "Add caching to the API" — Redis, in-memory, file-based, etc.
   - Example: "Improve performance" — many optimization strategies possible

3. **Code Modifications**: Changes that affect existing behavior or structure
   - Example: "Update the login flow" — what exactly should change?
   - Example: "Refactor this component" — what's the target architecture?

4. **Architectural Decisions**: The task requires choosing between patterns or technologies
   - Example: "Add real-time updates" — WebSockets vs SSE vs polling
   - Example: "Implement state management" — Redux vs Context vs custom solution

5. **Multi-File Changes**: The task will likely touch more than 2–3 files
   - Example: "Refactor the authentication system"
   - Example: "Add a new API endpoint with tests"

6. **Unclear Requirements**: You need to explore before understanding the full scope
   - Example: "Make the app faster" — need to profile and identify bottlenecks
   - Example: "Fix the bug in checkout" — need to investigate root cause

7. **User Preferences Matter**: The implementation could reasonably go multiple ways
   - If you would use `ask_user_question` to clarify the approach, use `enter_plan_mode` \
instead — Plan Mode lets you explore first, then present options with context

## When NOT to Use This Tool

Only skip `enter_plan_mode` for simple tasks:
- Single-line or few-line fixes (typos, obvious bugs, small tweaks)
- Adding a single function with clear requirements
- Tasks where the user has given very specific, detailed instructions
- Pure research/exploration tasks (use `spawn_subagent` with the Explore archetype instead)

If you skip Plan Mode, state a **specific reason** in your reply before implementing.

## What Happens

Calling this tool **requests user confirmation** before Plan Mode starts. The run pauses \
until the user approves or declines. After approval, write tools are gated until exit approval. \
Explore read-only with `spawn_subagent` (Explore / Plan archetypes), draft the plan with \
`update_plan_content`, clarify with `ask_user_question` if needed, then call `exit_plan_mode` \
for human approval before any implementation.

## Examples

### GOOD — Use `enter_plan_mode`:
- "Add user authentication to the app" — session vs JWT, token storage, middleware structure
- "Build a QQ Music–style frontend player" — greenfield multi-file app, stack and layout choices
- "Generate a React + Vite music player UI" — many components, routing, state, mock data
- "Optimize the database queries" — multiple approaches, need to profile first
- "Implement dark mode" — theme system affects many components
- "Add a delete button to the user profile" — placement, confirmation, API, errors, state
- "Update the error handling in the API" — multiple files, user should approve the approach

### BAD — Don't use `enter_plan_mode`:
- "Fix the typo in the README" — straightforward, no planning needed
- "Add a console.log to debug this function" — simple, obvious implementation
- "What files handle routing?" — research only, not implementation planning

## Important Notes

- Implementation approval happens when you call `exit_plan_mode` — the user reviews the \
plan before you may edit files
- If unsure whether to use this tool, err on the side of planning — alignment upfront \
beats redoing work
- Users appreciate being consulted before significant changes are made to their codebase"""

DEFAULT_UPDATE_CONTENT_DESCRIPTION = """\
Use this tool to write or revise the **plan document** while in Plan Mode. This is the \
**only** way to record implementation plans during planning — you cannot use Write/Edit on \
project files until the plan is approved.

## How This Tool Works

- Plan text is stored in the session plan document (visible in the Plan Mode UI as you update)
- Pass Markdown in `content`; use `mode` to control how it is applied:
  - **`append`** (default): add to the end of the existing plan — use for incremental drafts
  - **`replace`**: overwrite the entire plan — use when restructuring or producing a final \
clean version
- First successful update moves plan status from `exploring` → `drafting`
- This tool does NOT request user approval — call `exit_plan_mode` when the plan is ready \
for review

## When to Use This Tool

Use `update_plan_content` after read-only exploration when you are ready to capture \
findings and implementation steps:

1. **After Phase 1 (Explore)** — summarize codebase findings, constraints, and relevant file paths
2. **During Phase 2 (Design)** — record the chosen approach, architecture, and phased steps
3. **During Phase 3 (Review)** — refine the plan after reading critical files or getting \
answers from `ask_user_question`

## When NOT to Use This Tool

- You are not in Plan Mode (`enter_plan_mode` not called)
- Plan status is `pending_approval` or `approved` — content is locked until rejected or \
a new plan starts
- You want to edit **project source files** — that happens only after `exit_plan_mode` approval
- You only need to ask the user a question — use `ask_user_question` instead
- You are ready for the user to approve — use `exit_plan_mode`, not another content update

## What to Include

Keep the plan concise and actionable. Required sections for a final plan:
- **Context** — why this change is needed and the intended outcome
- **Approach** — recommended implementation (file paths, phases, reused utilities)
- **Verification** — how to test end-to-end (run app, tests, manual checks)

Also include when relevant:
- Assumptions and decisions already made
- Critical files to create or modify (with paths)

Do NOT paste large code blocks — describe changes; implementation comes after approval.

## Examples

### GOOD — Use `update_plan_content`:
- After Explore: append a "Findings" section listing key files and existing patterns
- After Plan sub-agent: replace with a structured implementation plan (tech stack, directory \
layout, phases)
- After user answers a clarifying question: append the decided approach to the plan

### BAD — Don't use `update_plan_content`:
- Writing `src/App.tsx` directly — use Write after approval
- Asking "Should I use React or Vue?" — use `ask_user_question`
- Submitting for approval with an empty plan — write content first, then `exit_plan_mode`"""

DEFAULT_EXIT_DESCRIPTION = """\
Use this tool when you are in Plan Mode and have finished writing your plan with \
`update_plan_content` and are ready for user approval.

## How This Tool Works

- You should have already written your plan via `update_plan_content` (append or replace \
Markdown in the session plan document)
- Pass an optional `summary` string — a short note for the reviewer (shown with the approval \
request); the full plan is read from session state, not from this parameter
- This tool signals that you're done planning and ready for the user to review and approve
- The user will see your plan document when they review it (e.g. in the Plan Mode UI)

## When to Use This Tool

IMPORTANT: Only use this tool when the task requires planning the **implementation steps** \
of work that involves writing code. For research tasks where you're gathering information, \
searching files, reading files, or trying to understand the codebase — do NOT use this tool.

## Before Using This Tool

Ensure your plan is complete and unambiguous:
- If you have unresolved questions about requirements or approach, use `ask_user_question` \
first (in earlier phases)
- Once your plan is finalized, use THIS tool to request approval

**Important:** Do NOT use `ask_user_question` to ask "Is this plan okay?" or "Should I \
proceed?" — that's exactly what THIS tool does. `exit_plan_mode` inherently requests user \
approval of your plan.

## After approval or rejection

- **Approved:** write tools unlock — implement per the plan; use `todo_write` / `task_*` to \
track work if helpful
- **Rejected:** plan returns to `drafting` — read the reviewer note, revise with \
`update_plan_content`, then call `exit_plan_mode` again

## Examples

1. Initial task: "Search for and understand the implementation of vim mode in the \
codebase" — Do NOT use `exit_plan_mode` because you are not planning implementation steps.
2. Initial task: "Help me implement yank mode for vim" — Use `exit_plan_mode` after you \
have finished planning the implementation steps.
3. Initial task: "Add a new feature to handle user authentication" — If unsure about auth \
method (OAuth, JWT, etc.), use `ask_user_question` first, then use `exit_plan_mode` after \
clarifying the approach."""

DEFAULT_ASK_DESCRIPTION = """\
Use this tool when you need to ask the user questions during Plan Mode. This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take

Usage notes:
- Ask **one focused question per call** — do not bundle multiple unrelated questions
- Provide an `options` list when structured choices exist; the UI also allows free-text input \
as a custom answer
- If you recommend a specific option, make that the first option in the list and add \
"(Recommended)" at the end of the label
- This tool pauses execution until the user answers

Plan mode note: Use this tool to clarify requirements or choose between approaches **before** \
finalizing your plan. Do NOT use this tool to ask "Is my plan ready?" or "Should I proceed?" \
— use `exit_plan_mode` for plan approval.

IMPORTANT: Do not use this tool to request plan sign-off (e.g. "Do you have feedback about \
the plan?", "Does the plan look good?"). If you need the user to approve the plan, call \
`exit_plan_mode` instead. Use `ask_user_question` only for open requirements or approach \
decisions, not for reviewing the finished plan document."""

_PLAN_AWARENESS_MARKER = "## Plan Mode (available)"
_PLAN_PROMPT_MARKER = "You are in **Plan Mode**"
