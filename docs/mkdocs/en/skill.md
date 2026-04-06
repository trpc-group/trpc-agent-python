# Skill (Agent Skills)

Agent Skills let you package reusable workflows into folders containing a `SKILL.md` specification file along with optional documentation and scripts. During a conversation, the agent first injects low-cost "overview" information, then loads the full body content and documentation only when truly needed, and safely runs scripts in an isolated workspace.

Background references:
- Engineering blog:
  https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Open Skills repository (reference structure):
  https://github.com/anthropics/skills

## Overview

### 🎯 Features

- 🔎 Overview injection (name + description) to guide selection
- 📥 `skill_load` fetches `SKILL.md` body and selected documentation on demand, automatically loading tools defined in the skill
- 📋 `skill_list` lists all available skill names
- 🔧 `skill_list_tools` lists tool names defined in a specified skill's `SKILL.md`
- ⚙️ `skill_select_tools` dynamically selects skill tools (add/replace/clear modes) for token optimization
- 📚 `skill_select_docs` adds/replaces/clears documentation
- 🧾 `skill_list_docs` lists available documentation
- 🏃 `skill_run` executes commands and returns stdout/stderr and output files
- 🗂️ Collects output files with MIME type detection support
- 🧩 Pluggable local or container workspace executors (local by default)
- 🧱 Custom working directory where skill run input files, output files, and skill files can be placed
- 🎯 Dynamic tool loading that automatically provides relevant tools based on skill selection, saving LLM tokens

### Three-Layer Information Model

Agent Skills adopt a three-layer information model that enables on-demand loading while keeping prompts concise:

**1) Initial "Overview" Layer (extremely low cost)**
   - Only injects the `name` and `description` from `SKILL.md` into the system message
   - Lets the model know which skills are available without loading full content

**2) Full Body Layer (loaded on demand)**
   - When a task truly requires a skill, the model calls `skill_load`
   - The framework then injects the complete `SKILL.md` body content for that skill

**3) Documentation/Script Layer (selective + isolated execution) / Tool Invocation**
   - Documentation is included only when explicitly requested
   - Scripts are not inlined into the prompt but executed in an isolated workspace
   - Only execution results and output files are returned, without exposing script source code
   - Parses user-configured available tools

### File Layout

```
skills/
  demo-skill/
    SKILL.md        # YAML (name/description) + Markdown body
    USAGE.md        # optional docs (.md/.txt)
    scripts/build.sh
    reference/      # Reference documentation
    ...
```

Repository and parsing: [trpc_agent_sdk/skills/_repository.py](../../../trpc_agent_sdk/skills/_repository.py)

## Quick Start

### 1) Requirements

- Python 3.12
- Model provider API key (OpenAI-compatible)
- Optional Docker (for container executor)

Common environment variables:

```bash
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="your-base-url"
export TRPC_AGENT_MODEL_NAME="your-model-name"
# Optional: specify the skills directory, supports local paths or URLs (see "URL-based Skills Root")
export SKILLS_ROOT=/path/to/skills
# Optional: override the cache directory for URL-based Skills Root
export SKILLS_CACHE_DIR=/path/to/cache
```

Alternatively, you can use a `.env` file (examples automatically load it via `python-dotenv`):

```bash
# .env file
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=your-base-url
TRPC_AGENT_MODEL_NAME=your-model-name
SKILLS_ROOT=./skills
# Optional: SKILLS_ROOT can also be a URL, for example:
# SKILLS_ROOT=https://example.com/my-skills.tar.gz
# SKILLS_CACHE_DIR=/custom/cache/path
```

### 2) Enabling Skills in an Agent

Create a skill repository and a workspace executor. If no executor is specified, the local executor is used by default for development convenience.

```python
import os
from trpc_agent_sdk.agents.llm_agent import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.code_executors import create_container_workspace_runtime

# Create workspace runtime (local or container)
workspace_runtime = create_local_workspace_runtime()
# Or use container: workspace_runtime = create_container_workspace_runtime()

# Create skill repository
repository = create_default_skill_repository("./skills", workspace_runtime=workspace_runtime)

# Create skill tool set with optional artifact save options
skill_tool_set = SkillToolSet(
    repository=repository,
    # run_tool_kwargs is an optional tool parameter
    run_tool_kwargs={
        "save_as_artifacts": True,  # Whether to save as artifact files
        "omit_inline_content": False,
    }
)

# Create an agent with skills
agent = LlmAgent(
        name="skill_run_agent",
        description="A professional skill run assistant that can use Agent Skills.",
        model=_create_model(),
        instruction=INSTRUCTION,  # Prompt containing skill usage guidance
        tools=[skill_tool_set],
        skill_repository=repository,
    )
```

**Prompt example**:

The `INSTRUCTION` should include complete skill usage workflow guidance:

```python
INSTRUCTION = """
You are an AI assistant with access to Agent Skills.

## Complete Skill Workflow

When handling user requests:

1. **Discover** → Call skill_list() to see available skills
2. **Inspect** → Call skill_list_tools(skill_name="...") to preview tools
3. **Load** → Call skill_load(skill_name="...") to load the skill
4. **Optimize** → Call skill_select_tools(...) to select only needed tools (saves tokens)
5. **Document** → Call skill_list_docs(...) and skill_select_docs(...) if more info needed
6. **Execute** → Call skill_run(...) to execute commands or use skill's tools directly

Example Complete Flow:
User: "What's the weather in Beijing?"
→ skill_list() → see "weather-tools"
→ skill_list_tools(skill_name="weather-tools") → see available tools
→ skill_load(skill_name="weather-tools") → load full content
→ skill_select_tools(skill_name="weather-tools", tools=["get_current_weather"]) → optimize
→ get_current_weather(city="Beijing") → execute

Always use environment variables in commands:
- $WORKSPACE_DIR, $SKILLS_DIR, $WORK_DIR, $OUTPUT_DIR, $RUN_DIR, $SKILL_NAME
"""
```

Key points:
- **Automatic tool registration**: The following tools are automatically registered via `SkillToolSet`, requiring no manual wiring:
  - `skill_list`: Lists all available skills
  - `skill_list_tools`: Lists tools of a skill
  - `skill_load`: Loads skill content
  - `skill_select_tools`: Selects specific tools (token optimization)
  - `skill_list_docs`: Lists available documentation
  - `skill_select_docs`: Selects specific documentation
  - `skill_run`: Executes skill commands
- **Intelligent prompt guidance**: Explicitly describe the workflow in the prompt to guide the LLM to call tools in the correct order
- **Token optimization**: Use `skill_select_tools` to load only the needed tools, significantly reducing context size
- **Code location**:
  - Package entry (aggregated exports): [trpc_agent_sdk/skills/tools/__init__.py](../../../trpc_agent_sdk/skills/tools/__init__.py)
  - `skill_run` implementation: [trpc_agent_sdk/skills/tools/_skill_run.py](../../../trpc_agent_sdk/skills/tools/_skill_run.py) (for other tools, see **Declaration location** in each section below)

### 3) Running the Example

Full interactive demo: [examples/skills/run_agent.py](../../../examples/skills/run_agent.py)

The example is organized in a modular structure:
- `agent/agent.py` - Agent creation
- `agent/tools.py` - Skill tool set creation
- `agent/config.py` - Model configuration from environment variables
- `agent/prompts.py` - Agent instruction prompts
- `run_agent.py` - Main entry file

```bash
cd examples/skills

# Set environment variables
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="your-base-url"
export TRPC_AGENT_MODEL_NAME="your-model-name"
export SKILLS_ROOT="./skills"  # Optional, defaults to ./skills

# Run the example
python3 run_agent.py
```

Or use a `.env` file:

```bash
# Create .env file
cat > .env << EOF
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=your-base-url
TRPC_AGENT_MODEL_NAME=your-model-name
SKILLS_ROOT=./skills
EOF

# Run (automatically loads .env)
python3 run_agent.py
```

Example skill (excerpt):
[examples/skills/skills/python_math/SKILL.md](../../../examples/skills/skills/python_math/SKILL.md)

Tips:
- Describe the task you want to accomplish; the model will decide whether a skill is needed based on the overview.
- When needed, the model will call `skill_load` to fetch the body/documentation, then call `skill_run` to execute and return output files.

#### Example run output

Using user-file-ops as an example:
```txt
🆔 Session ID: be355f8f...
📝 User:
        I have a text file at /tmp/skillrun-notes.txt.
        Please use the user-file-ops skill to summarize it, you can use command `cp` to copy it to the workspace,
        then mapping it to `work/inputs/user-notes.txt` and writing the summary to `out/user-notes-summary.txt`

🤖 Assistant:
🔧 [Invoke Tool:: skill_load({'skill_name': 'user-file-ops'})]
📊 [Tool Result: {'result': "skill 'user-file-ops' loaded"}]

🔧 [Invoke Tool:: skill_list_docs({'skill_name': 'user-file-ops'})]
📊 [Tool Result: {}]

🔧 [Invoke Tool:: skill_run({'skill': 'user-file-ops', 'command': 'cp /tmp/skillrun-notes.txt work/inputs/user-notes.txt && bash scripts/summarize_file.sh work/inputs/user-notes.txt out/user-notes-summary.txt', 'output_files': ['out/user-notes-summary.txt']})]
📊 [Tool Result: {'stdout': '', 'stderr': '', 'exit_code': 0, 'timed_out': False, 'duration_ms': 0, 'output_files': [{'name': 'out/user-notes-summary.txt', 'content': 'File: work/inputs/user-notes.txt\nLines: 2\nWords: 7\nBytes: 41\n\nFirst 20 non-empty lines:\n     1\thello from skillrun\n     2\tthis is another line\n', 'mime_type': 'text/plain'}], 'artifact_files': []}]
The file `/tmp/skillrun-notes.txt` has been summarized successfully. Here are the details:

### Summary of `user-notes.txt`:
- **Lines**: 2
- **Words**: 7
- **Bytes**: 41

### Preview (First 20 non-empty lines):
```
1. hello from skillrun
2. this is another line
```

The summary has been saved to `out/user-notes-summary.txt`. Let me know if you'd like to perform any further actions!
```

#### Run Directory

Default working directory name: `/tmp/ws_<session_id>-<time>/`, files under the directory:
```txt
├── metadata.json
├── out
│   └── user-notes-summary.txt
├── runs
│   ├── run_20260116T201918.239930
│   ├── run_20260116T201918.322124
│   └── run_20260116T201918.402214
├── skills
│   └── user-file-ops
│       ├── inputs -> ../../work/inputs
│       ├── out -> ../../out
│       ├── scripts
│       │   └── summarize_file.sh
│       ├── SKILL.md
│       └── work -> ../../work
└── work
    └── inputs
        └── user-notes.txt
```
- out: Result output directory
- work: Temporary shared working directory
- runs: Current program run path
- skills: Storage directory for all skills

## Advanced Usage

### Custom Working Directory

By default, a workspace is created in a temporary directory (e.g., `/tmp/ws_<session_id>-<time>/`) when skills are executed. If you need to customize the output directory location, you can do so by setting environment variables.

#### Method 1: Specify in Code

```python
def create_skill_tool_set(workspace_runtime_type: str = "local") -> SkillToolSet:
    """Create a new skill tool set."""
    tool_kwargs = {
        "save_as_artifacts": True,
        "omit_inline_content": False,
    }
    if workspace_runtime_type == "local":
        workspace_runtime_args = {"work_root": "/tmp/ws_abc123"}
    else:
        workspace_runtime_args = {}
    # workspace_runtime = _create_workspace_runtime(workspace_runtime_type="container", **workspace_runtime_args)
    # Create workspace runtime based on the specified type (local/container)
    workspace_runtime = _create_workspace_runtime(workspace_runtime_type=workspace_runtime_type, **workspace_runtime_args)
    skill_paths = _get_skill_paths()
    repository = create_default_skill_repository(skill_paths, workspace_runtime=workspace_runtime)
    return SkillToolSet(repository=repository, run_tool_kwargs=tool_kwargs), repository
```

Specify in the workspace_runtime_args parameter.

The working directory then becomes: `/{custom_dir}/ws_{session_id}_{time}`, for example:

```txt
/tmp/ws_abc123/ws_env_var_demo_1768564372436142924/
├── metadata.json
├── out
│   ├── fibonacci_data.txt
│   └── fibonacci_summary.txt
├── runs
│   ├── run_20260116T195252.438049
│   ├── run_20260116T195252.518753
│   ├── run_20260116T195252.597016
│   ├── run_20260116T195257.562621
│   └── run_20260116T195304.315245
├── skills
│   └── python-math
│       ├── inputs -> ../../work/inputs
│       ├── out -> ../../out
│       ├── scripts
│       │   └── fib.py
│       ├── SKILL.md
│       └── work -> ../../work
└── work
    └── inputs
```

#### Method 2: Specify in the Prompt

```python
output_instruction = f"""

IMPORTANT: When calling skill_run, you MUST pass env={{'OUTPUT_DIR': '{custom_output_dir}'}} parameter
to use the custom output directory. Write all output files to $OUTPUT_DIR (which will be '{custom_output_dir}').
"""
```
You can also use this approach when you expect skill execution commands to pass other environment variables.

### Dynamic Tool Loading

Full example reference: [skills_with_dynamic_tools/run_agent.py](../../../examples/skills_with_dynamic_tools/run_agent.py)

### URL-based Skills Root

`SKILLS_ROOT` supports not only local directory paths but also URL formats. The framework automatically downloads remote archive packages, extracts and caches them locally. Subsequent calls hit the cache directly without re-downloading.

Related implementation: [trpc_agent_sdk/skills/_url_root.py](../../../trpc_agent_sdk/skills/_url_root.py)

#### Supported Input Formats

| Format | Example | Description |
|---|---|---|
| Local path | `/path/to/skills` or `./skills` | Directly uses a local directory (default behavior, no caching involved) |
| `file://` URL | `file:///path/to/skills` | Explicit file URL, only supports `localhost` or empty host |
| `http://` / `https://` URL | `https://example.com/skills.tar.gz` | Automatically downloads, extracts, and caches locally |

Supported archive formats for remote URLs:

| Extension | Format |
|---|---|
| `.zip` | ZIP archive |
| `.tar` | Uncompressed tar archive |
| `.tar.gz` / `.tgz` | gzip-compressed tar archive |
| `SKILL.md` (direct link) | Single bare skill file |

When the format cannot be determined from the extension, the framework reads magic bytes from the file header for automatic identification (ZIP: `PK\x03\x04`; gzip: `\x1f\x8b`).

#### Usage

**Configure via environment variables**:

```bash
# HTTPS + tar.gz archive
export SKILLS_ROOT="https://example.com/my-skills.tar.gz"

# HTTPS + ZIP archive
export SKILLS_ROOT="https://example.com/my-skills.zip"

# Point directly to a single SKILL.md file
export SKILLS_ROOT="https://example.com/SKILL.md"

# Explicit file URL (equivalent to a local path)
export SKILLS_ROOT="file:///home/user/my-skills"
```

**Use directly in code**:

```python
# Directly configure the skill path
skill_path = "https://example.com/skills.tar.gz"
repository = create_default_skill_repository(skill_path, workspace_runtime=workspace_runtime)
```

#### Download Caching Mechanism

When using a URL-based `SKILLS_ROOT` for the first time, the framework automatically performs the following steps:

```txt
1. Download the archive to a temporary directory
   {cache_dir}/tmp-skill-root-XXXXXX/download

2. Extract to a temporary extraction directory
   {cache_dir}/tmp-skill-root-XXXXXX/root/

3. Write a sentinel file (marks extraction as successful)
   {cache_dir}/tmp-skill-root-XXXXXX/root/.ready

4. Atomically rename to the final cache directory (named by SHA-256 hash of the URL)
   {cache_dir}/{sha256_of_url}/

5. Clean up temporary directories
```

On subsequent calls, if the `{cache_dir}/{sha256_of_url}/.ready` file exists, the cached directory is returned directly, skipping download and extraction. If the cache directory exists but the `.ready` file is missing (e.g., a previous download was interrupted), it is automatically cleaned up and re-downloaded.

In concurrent scenarios where multiple processes download the same URL simultaneously, the framework ensures through atomic `rename` operations that only the first process's result is written. Other processes detect the `.ready` file and return immediately.

**Default cache directory locations**:

| Platform | Default Path |
|---|---|
| Linux | `$XDG_CACHE_HOME/trpc-agent-py/skills/` or `~/.cache/trpc-agent-py/skills/` |
| macOS | `~/Library/Caches/trpc-agent-py/skills/` |
| Windows | `%LocalAppData%/trpc-agent-py/skills/` |

Override via environment variable:

```bash
export SKILLS_CACHE_DIR="/custom/cache/path"
```

#### Security Restrictions

To guard against malicious archives (e.g., zip bombs) and oversized downloads, the framework enforces the following hard limits:

| Restriction | Default Value | Description |
|---|---|---|
| Maximum download size per request | 64 MiB | Includes both `Content-Length` pre-check and streaming write verification |
| Maximum individual extracted file size | 64 MiB | ZIP uses dual verification of header declaration and actual read |
| Total size of all extracted files | 256 MiB | Cumulative byte count limit for all entries |

Exceeding any limit raises a `RuntimeError`, and downloaded temporary files are automatically cleaned up.

Additionally, archive path safety is strictly enforced:
- Rejects absolute paths (e.g., `/etc/passwd`)
- Rejects path traversal (e.g., `../../etc/passwd`)
- Rejects Windows drive letters (e.g., `C:foo`)
- Rejects symbolic links and hard link tar entries (prevents sandbox escape)

## SKILL.md File Structure

The `SKILL.md` file uses YAML front matter (metadata) + Markdown body format:

```markdown
---
name: python-math
description: Small Python utilities for math and text files.
---

Overview
Run short Python scripts inside the skill workspace...

Examples
1) Print the first N Fibonacci numbers
   Command: python3 scripts/fib.py 10 > out/fib.txt

Output Files
- out/fib.txt
```

Writing guidelines:
- **Keep it concise**: The `name` and `description` fields should be brief and clear, used for overview display
- **Provide details**: In the body, include when to use, steps/commands, output file paths, etc.
- **Organize scripts**: Place scripts in the `scripts/` directory and reference them in commands

For more examples, see:
https://github.com/anthropics/skills

## Skill Tools Explained

### `skill_list`

**Declaration location**: [trpc_agent_sdk/skills/tools/_skill_list.py](../../../trpc_agent_sdk/skills/tools/_skill_list.py)

**Input parameters**: None

**Return value**:
- An array of all available skill names

**Behavior**:
- Returns a list of all available skill names in the skill repository
- Used for discovering and browsing available skills

**Prompt guidance**:

This tool is automatically called by the LLM. The agent's prompt should include guidance similar to:

```python
INSTRUCTION = """
## Skill Discovery Workflow

When a user asks for a task that might require skills:

1. **First, always check available skills**:
   - Call skill_list() to see what skills are available
   - This shows you all skill names like ["file-tools", "python-math", "weather-tools"]

Example:
User: "Can you help me with weather information?"
Assistant: Let me check what skills are available.
→ Call skill_list()
→ See result: ["file-tools", "python-math", "weather-tools"]
→ Notice "weather-tools" is relevant
"""
```

**Use cases**:
- User asks "What skills are available?"
- When exploring available capabilities
- When unsure which skill to use, list all skills first

### `skill_list_tools`

**Declaration location**: [trpc_agent_sdk/skills/tools/_skill_list_tool.py](../../../trpc_agent_sdk/skills/tools/_skill_list_tool.py)

**Input parameters**:
- `skill_name` (required): Skill name

**Return value**:
- An array of tool names defined in the `Tools:` section of the skill's `SKILL.md`
- Returns an empty array if the skill defines no tools

**Behavior**:
- Returns the list of tools declared in the specified skill's `SKILL.md`
- Used to preview the tools provided by a skill before loading it
- **Note**: Only returns tools explicitly listed in `SKILL.md`, not all tools in the actual code

**Prompt guidance**:

This tool is called by the LLM before loading a skill. The prompt should include:

```python
INSTRUCTION = """
## Skill Inspection Workflow

Before loading ANY skill, you MUST inspect its tools:

2. **Preview skill tools before loading**:
   - Call skill_list_tools(skill_name="skill-name")
   - This shows what tools the skill provides
   - Verify the skill has the tools you need

Example:
Assistant: I found "weather-tools" skill. Let me check what it provides.
→ Call skill_list_tools(skill_name="weather-tools")
→ See result: ["get_current_weather", "get_weather_forecast", "search_city_by_name"]
→ Confirm it has "get_current_weather" which I need
→ Proceed to load the skill

**Why this step matters**:
- Avoids loading unnecessary skills
- Confirms the skill has required capabilities
- Saves tokens by loading only relevant skills
"""
```

**Use cases**:
- Verify a skill provides the required tools before calling `skill_load`
- User asks "What tools does this skill have?"
- When choosing the appropriate skill

**Definition in SKILL.md**:

Tools are declared in the `Tools:` section of the `SKILL.md` file:

```markdown
---
name: weather-tools
description: Weather information query tools
---

Tools:
- get_current_weather
- get_weather_forecast
- search_city_by_name
# comment: this tool is deprecated
# - old_weather_api

Overview
...
```

### `skill_select_tools`

**Declaration location**: [trpc_agent_sdk/skills/tools/_skill_select_tools.py](../../../trpc_agent_sdk/skills/tools/_skill_select_tools.py)

**Input parameters**:
- `skill_name` (required): Skill name
- `tools` (optional): Array of tool names
- `include_all_tools` (optional): Boolean, whether to include all tools
- `mode` (optional): String, operation mode
  - `add`: Add tools to the existing list
  - `replace`: Replace the existing tool list (default)
  - `clear`: Clear all tools

**Return value**:
- `SkillSelectToolsResult` object containing:
  - `selected_tools`: Array of selected tool names
  - `include_all_tools`: Whether all tools are included

**Behavior**:
- Optimizes LLM context: activates only the tools needed for the current conversation
- Updates the `temp:skill:tools:<name>` session key
- When used with `DynamicSkillToolSet`, only selected tools are loaded into the LLM context

**Prompt guidance**:

This tool is called by the LLM after loading a skill to optimize token usage. The prompt should include:

```python
INSTRUCTION = """
## Tool Selection for Token Optimization

After loading a skill, you SHOULD refine tool selection:

4. **Optimize tool selection** (RECOMMENDED):
   - After skill_load(), all tools from SKILL.md are auto-selected
   - If you only need specific tools, call skill_select_tools() to reduce tokens
   - This is especially important for skills with many tools

Example 1: Select specific tools
User: "What's the current weather in Beijing?"
Assistant:
→ skill_load(skill_name="weather-tools")  # Auto-selects all 3 tools
→ skill_select_tools(
    skill_name="weather-tools",
    tools=["get_current_weather"],  # Only need current weather
    mode="replace"
  )
→ Result: Only 1 tool active instead of 3 (saves ~60% tokens)

Example 2: Multi-tool task
User: "Get current weather and 3-day forecast for Shanghai"
Assistant:
→ skill_load(skill_name="weather-tools")
→ skill_select_tools(
    skill_name="weather-tools",
    tools=["get_current_weather", "get_weather_forecast"],
    mode="replace"
  )
→ Result: 2 out of 3 tools active (saves ~30% tokens)

Example 3: Add more tools later
Assistant:
→ skill_select_tools(
    skill_name="weather-tools",
    tools=["search_city_by_name"],  # Need to search city
    mode="add"  # Add to existing selection
  )

**Token Savings**:
- A skill with 10 tools → select 2 → saves ~80% tool definition tokens
- Especially valuable for skills with complex tools
"""
```

**Use cases**:
- Optimize tool selection after `skill_load` to reduce token consumption
- When a task only requires a subset of a skill's tools
- Dynamically adjust available tools during the conversation

**Relationship with `skill_load`**:
- `skill_load` automatically selects all tools defined in `SKILL.md`
- `skill_select_tools` is used for further refinement to achieve token optimization

### `skill_load`

**Declaration location**: [trpc_agent_sdk/skills/tools/_skill_load.py](../../../trpc_agent_sdk/skills/tools/_skill_load.py)

**Input parameters**:
- `skill_name` (required): Skill name
- `docs` (optional): Array of document file names to load
- `include_all_docs` (optional): Boolean, whether to include all documentation

**Return value**:
- A success message string, e.g.: `"skill 'python-math' loaded"`

**Behavior**:
- Writes temporary session keys (per turn):
  - `temp:skill:loaded:<name>` = "1" (marks the skill as loaded)
  - `temp:skill:docs:<name>` = "*" (all documentation) or JSON array (specified document list)
  - `temp:skill:tools:<name>` = JSON array (tool list automatically parsed from `SKILL.md`)
- The request processor injects the `SKILL.md` body content and selected documentation into the system message
- Automatically selects all tools defined in the `Tools:` section of `SKILL.md`

**Prompt guidance**:

This tool is called by the LLM after confirming a skill is needed. The prompt should include:

```python
INSTRUCTION = """
## Skill Loading Workflow

After confirming a skill is appropriate:

3. **Load the skill**:
   - Call skill_load(skill_name="skill-name")
   - This injects the full SKILL.md body content into context
   - Automatically selects all tools defined in the skill's SKILL.md
   - Optionally load specific docs or all docs

Example 1: Load skill without docs
Assistant:
→ skill_load(skill_name="python-math")
→ Result: Full SKILL.md content loaded, all tools auto-selected
→ Can now use the skill's tools or run commands

Example 2: Load skill with specific docs
Assistant:
→ skill_load(
    skill_name="weather-tools",
    docs=["API_REFERENCE.md"]  # Load specific documentation
  )

Example 3: Load skill with all docs
Assistant:
→ skill_load(
    skill_name="data-analysis",
    include_all_docs=True  # Load all available docs
  )

**What happens after loading**:
- SKILL.md body is injected into your context (Overview, Examples, etc.)
- All tools listed in SKILL.md Tools: section are automatically selected
- You can now see detailed usage instructions and examples
- You can call skill_run or use the skill's tools

**Multiple loads**:
- Safe to call multiple times on the same skill
- Subsequent calls can add/replace docs
- Tool selection persists until modified by skill_select_tools
"""
```

**Use cases**:
- Load a skill after confirming the requirement via `skill_list` and `skill_list_tools`
- Need to view detailed usage instructions and examples for a skill
- Preparing to use a skill's tools or execute commands

**Usage notes**:
- Can be safely called multiple times to add or replace documentation
- First load automatically selects all tools; use `skill_select_tools` for further optimization

### `skill_select_docs`

**Declaration location**: [trpc_agent_sdk/skills/tools/_skill_select_docs.py](../../../trpc_agent_sdk/skills/tools/_skill_select_docs.py)

**Input parameters**:
- `skill_name` (required): Skill name
- `docs` (optional): Array of document file names
- `include_all_docs` (optional): Boolean, whether to include all documentation
- `mode` (optional): String, operation mode
  - `add`: Add documents to the existing list
  - `replace`: Replace the existing document list (default)
  - `clear`: Clear all documents

**Return value**:
- `SkillSelectDocsResult` object containing:
  - `selected_docs`: Array of selected document names
  - `include_all_docs`: Whether all documents are included

**Behavior**:
- Updates the `temp:skill:docs:<name>` session key:
  - `*`: Indicates all documents are included
  - JSON array: Indicates an explicitly specified document list
- On the next LLM request, the selected document content is injected into the system message

**Prompt guidance**:

This tool is called by the LLM when additional documentation is needed. The prompt should include:

```python
INSTRUCTION = """
## Documentation Selection

If the SKILL.md body is not sufficient, you can load additional docs:

5. **Select additional documentation** (when needed):
   - Call skill_select_docs() to load reference documentation
   - Use this when you need API details, configuration info, etc.

Example 1: Load specific docs
Assistant: I need more details about the API.
→ skill_select_docs(
    skill_name="weather-tools",
    docs=["API_REFERENCE.md", "CONFIGURATION.md"],
    mode="replace"
  )

Example 2: Load all docs
Assistant: Let me load all available documentation.
→ skill_select_docs(
    skill_name="data-analysis",
    include_all_docs=True
  )

Example 3: Add more docs
Assistant: I need additional reference.
→ skill_select_docs(
    skill_name="weather-tools",
    docs=["TROUBLESHOOTING.md"],
    mode="add"  # Add to existing docs
  )

**When to use**:
- SKILL.md Overview is insufficient
- Need detailed API reference
- Need configuration examples
- Troubleshooting specific issues
"""
```

**Use cases**:
- The `SKILL.md` body content is insufficient to complete the task
- Need to view API reference documentation
- Need configuration examples or troubleshooting guides

### `skill_list_docs`

**Declaration location**: [trpc_agent_sdk/skills/tools/_skill_list_docs.py](../../../trpc_agent_sdk/skills/tools/_skill_list_docs.py)

**Input parameters**:
- `skill_name` (required): Skill name

**Return value**:
- An array of available document file names (e.g., `["API_REFERENCE.md", "CONFIGURATION.md", "TROUBLESHOOTING.md"]`)

**Behavior**:
- Lists all available document files for the specified skill
- Used to view available documentation before calling `skill_select_docs`

**Prompt guidance**:

This tool is called by the LLM when it needs to view available documentation. The prompt should include:

```python
INSTRUCTION = """
## Documentation Discovery

Before selecting docs, you can check what's available:

**Check available docs**:
→ skill_list_docs(skill_name="skill-name")
→ Returns: ["API_REFERENCE.md", "USAGE_EXAMPLES.md", ...]

Example workflow:
User: "I need help configuring the weather API"
Assistant: Let me check what documentation is available.
→ skill_list_docs(skill_name="weather-tools")
→ Result: ["API_REFERENCE.md", "CONFIGURATION.md", "FAQ.md"]
→ I see there's a CONFIGURATION.md, let me load it.
→ skill_select_docs(
    skill_name="weather-tools",
    docs=["CONFIGURATION.md"]
  )

**When to use**:
- Before calling skill_select_docs
- User asks "what documentation is available?"
- Need to find specific reference materials
"""
```

**Use cases**:
- View available documentation before calling `skill_select_docs`
- User asks "What documentation does this skill have?"

**Note**: These session keys are automatically managed by the framework; in the natural conversation flow, you typically do not need to manipulate them directly.

### `skill_run`

**Declaration location**: [trpc_agent_sdk/skills/tools/_skill_run.py](../../../trpc_agent_sdk/skills/tools/_skill_run.py)

**Input parameters**:
- `skill` (required): Skill name
- `command` (required): Shell command to execute
- `output_files` (optional): Array of glob patterns for output files (e.g., `["out/*.txt", "$OUTPUT_DIR/result.json"]`)
- `env` (optional): Dictionary of custom environment variables (e.g., `{"CUSTOM_VAR": "value"}`)
- `timeout` (optional): Timeout in seconds

**Return value**:
- `WorkspaceRunResult` object containing:
  - `stdout`: Standard output
  - `stderr`: Standard error
  - `exit_code`: Exit code
  - `timed_out`: Whether it timed out
  - `duration_ms`: Execution duration in milliseconds
  - `output_files`: Array of collected output files (each containing `name`, `content`, `mime_type`)
  - `artifact_files`: Artifact file information

**Behavior**:
- Executes shell commands in an isolated workspace
- Automatically injects standard environment variables (`$WORKSPACE_DIR`, `$SKILLS_DIR`, `$WORK_DIR`, `$OUTPUT_DIR`, `$RUN_DIR`, `$SKILL_NAME`)
- Collects specified output files and returns them
- Supports custom environment variable overrides

**Prompt guidance**:

This tool is called by the LLM when ready to execute actual commands. The prompt should include detailed usage guidelines:

```python
INSTRUCTION = """
## Skill Execution

After loading a skill, you can execute commands:

6. **Execute skill commands**:
   - Call skill_run(skill="skill-name", command="...", output_files=[...])
   - Commands run in the skill's directory
   - Use environment variables for portable paths

Example 1: Simple command execution
Assistant:
→ skill_run(
    skill="python-math",
    command="python3 scripts/fib.py 10 > $OUTPUT_DIR/fib.txt",
    output_files=["$OUTPUT_DIR/fib.txt"]
  )

Example 2: Multiple output files
Assistant:
→ skill_run(
    skill="data-analysis",
    command="python3 scripts/analyze.py $WORK_DIR/inputs/data.csv",
    output_files=[
        "$OUTPUT_DIR/*.txt",
        "$OUTPUT_DIR/charts/*.png"
    ]
  )

Example 3: Custom environment variables
Assistant:
→ skill_run(
    skill="weather-tools",
    command="python3 scripts/fetch.py",
    env={
        "API_KEY": "user-provided-key",
        "REGION": "asia"
    },
    output_files=["$OUTPUT_DIR/weather.json"]
  )

Example 4: Complex multi-step command
Assistant:
→ skill_run(
    skill="file-tools",
    command='''
        mkdir -p $OUTPUT_DIR/processed &&
        cp $WORK_DIR/inputs/*.txt $OUTPUT_DIR/processed/ &&
        ls -la $OUTPUT_DIR/processed
    ''',
    output_files=["$OUTPUT_DIR/processed/*"]
  )

**Environment Variables Available**:
- $WORKSPACE_DIR: Root workspace directory
- $SKILLS_DIR: Skills directory (contains skill folders)
- $WORK_DIR: Shared working directory
  - $WORK_DIR/inputs: User input files (read-only)
- $OUTPUT_DIR: Output directory (write final results here)
- $RUN_DIR: Current run's directory (unique per execution)
- $SKILL_NAME: Current skill name (e.g., "python-math")

**Best Practices**:
1. Always use environment variables (not hard-coded paths)
2. Write final outputs to $OUTPUT_DIR
3. Read user files from $WORK_DIR/inputs
4. Include output_files parameter to collect results
5. Use descriptive output file names

**Common Patterns**:

# Generate output file

command="python3 scripts/process.py > $OUTPUT_DIR/result.txt"

# Process input and generate output
command="bash scripts/transform.sh $WORK_DIR/inputs/data.csv $OUTPUT_DIR/output.csv"

# Multiple commands
command="mkdir -p $OUTPUT_DIR/reports && python3 scripts/generate.py && ls $OUTPUT_DIR"

# Use SKILL_NAME for context
command="echo 'Processed by $SKILL_NAME' > $OUTPUT_DIR/metadata.txt"

**Error Handling**:
- Check exit_code in the result (0 = success)
- Read stderr for error messages
- Adjust timeout if command takes too long
"""
```

**Use cases**:
- Execute scripts or commands within a skill
- Process files and generate output
- Run data analysis, transformation, and other tasks

**Execution flow**

```txt
LLM calls skill_run(skill="python-math", command="python3 scripts/fib.py 10")
    ↓
1. Create an isolated workspace
   /tmp/ws_<session_id>/
   ├── skills/python-math/     (skill root directory, read-only)
   │   ├── SKILL.md
   │   ├── scripts/
   │   │   └── fib.py
   │   ├── out/    → ../../out  (symbolic link)
   │   └── work/   → ../../work (symbolic link)
   ├── out/                     (output directory)
   ├── work/                    (working directory)
   └── run/                     (run directory)
    ↓
2. Inject environment variables
   WORKSPACE_DIR=/tmp/ws_<session_id>
   SKILLS_DIR=/tmp/ws_<session_id>/skills
   WORK_DIR=/tmp/ws_<session_id>/work
   OUTPUT_DIR=/tmp/ws_<session_id>/out
   RUN_DIR=/tmp/ws_<session_id>/run
   SKILL_NAME=python-math
    ↓
3. Execute command (in skill root directory)
   cd /tmp/ws_<session_id>/skills/python-math
   bash -lc "python3 scripts/fib.py 10"
    ↓
4. Collect output files
   Collect files based on the output_files parameter
   e.g.: out/*.txt → /tmp/ws_<session_id>/out/*.txt
    ↓
5. Return results
   {
     "stdout": "...",
     "stderr": "...",
     "exit_code": 0,
     "output_files": [...]
   }
```

## Runtime Environment

**Interface definition**: [trpc_agent_sdk/code_executors/_base_workspace_runtime.py](../../../trpc_agent_sdk/code_executors/_base_workspace_runtime.py)

**Implementations**:
- **Local executor**: [trpc_agent_sdk/code_executors/local/_local_ws_runtime.py](../../../trpc_agent_sdk/code_executors/local/_local_ws_runtime.py)
  - Executes commands directly on the local system, suitable for development and testing
- **Container executor** (Docker): [trpc_agent_sdk/code_executors/container/_container_ws_runtime.py](../../../trpc_agent_sdk/code_executors/container/_container_ws_runtime.py)
  - Executes in Docker containers, providing better isolation

**Container executor notes**:
- The run base directory is writable; when `$SKILLS_ROOT` is set, it is mounted in read-only mode
- Network access is disabled by default for reproducibility and security

**Security and resource limits**:
- **Workspace isolation**: All read/write operations are confined within the workspace
- **Risk control**: Reduces security risks through timeout mechanisms and read-only skill trees
- **Resource limits**: Output file read sizes are capped to prevent oversized payloads from affecting system performance

## Events and Tracing

Tool execution may carry state deltas (used by `skill_load`). State deltas are managed through `InvocationContext` and used to inject skill content into the system message.


## Design Rationale

### Design Motivation

Skills typically contain lengthy instructions and scripts. Inlining all content into prompts is not only costly but also poses security risks. The three-layer information model keeps prompts concise by loading detailed content and running code only when truly needed, thus balancing functionality and efficiency.

### Skill Execution Flow

The following diagram illustrates the complete flow from user query to LLM tool invocation:

```txt
User query: "What's the weather in Beijing?"
    ↓
First LLM request (skill not loaded)
    ↓
_inject_overview() is called
    ↓
Inject short descriptions:
"Available skills:
 - weather-tools: Weather information query tools..."
    ↓
LLM sees the skill description and decides to load it
    ↓
LLM calls: skill_load(skill_name="weather-tools")
    ↓
skill_load() updates session state
(SKILL_LOADED_STATE_KEY_PREFIX + "weather-tools" = "1")
    ↓
Second LLM request (skill loaded)
    ↓
process_llm_request() is called
    ↓
_get_loaded_skills() detects "weather-tools" is loaded
    ↓
repository.get("weather-tools") retrieves the full skill object
    ↓
_parse_full() parses SKILL.md
    ├─ YAML frontmatter → summary (name, description)
    └─ Markdown body → body (Overview and full content)
    ↓
if sk.body: parts.append(f"\n[Loaded] {name}\n\n{sk.body}\n")
    ↓
Full Overview content is injected into the system message
    ↓
LLM sees detailed skill instructions and usage
    ↓
LLM calls the corresponding tool: get_current_weather(city="Beijing")
```

**Key points**:

1. **Overview injection** (first request)
   - The framework automatically calls `_inject_overview()` to inject short descriptions of all skills into the system message
   - The LLM decides whether to load a skill based on the descriptions
   - The cost at this point is extremely low, including only `name` and `description` fields

2. **Skill loading** (`skill_load` call)
   - The LLM proactively calls `skill_load(skill_name="weather-tools")`
   - The tool updates session state: `temp:skill:loaded:weather-tools = "1"`
   - The state delta is passed to the framework via `state_delta`

3. **Content injection** (second request)
   - The framework detects the skill is loaded (via `_get_loaded_skills()`)
   - Retrieves the complete `SKILL.md` content from the repository
   - Injects the Markdown body into the system message
   - The LLM can now see detailed usage instructions and examples

4. **Tool invocation**
   - The LLM calls the corresponding tool function based on the injected detailed content
   - After tool execution, results are returned to the LLM

### State Injection Mechanism

Tools write temporary state keys via `InvocationContext.actions.state_delta`, and the framework dynamically constructs system messages based on these state deltas, enabling on-demand injection of skill content.

**Primary state keys**:
- `temp:skill:loaded:<name>`: Marks a skill as loaded
- `temp:skill:docs:<name>`: Stores the selected document list
- `temp:skill:tools:<name>`: Stores the selected tool list

**Best practices**:
1. **Use conventional prefixes**: Maintain consistent state key naming (e.g., `temp:skill:loaded:`)
2. **JSON serialization**: Serialize complex data structures as JSON
3. **Merge state**: Merge `session_state` and `state_delta` when reading
4. **Minimize state**: Store only essential state information
5. **Document state keys**: Clearly comment the meaning and format of state keys in code

### Execution Isolation

Scripts run within workspace boundaries, returning only selected output files rather than the script source code itself, ensuring security and controllability.

## Troubleshooting

**Common issues and solutions**:

- **Unknown skill error**:
  - Check that the skill name is correct
  - Verify the repository path is correct
  - Ensure the skill is listed in the skill overview before calling `skill_load`

- **Missing executor**:
  - Explicitly configure `workspace_runtime` when creating the repository
  - Or rely on the local default executor (development environment)

- **Timeout or non-zero exit code**:
  - Check that command syntax and dependencies are correct
  - Adjust the `timeout` parameter
  - Note: Network access is disabled by default in container mode

- **Missing output files**:
  - Check that glob patterns correctly match the files
  - Verify the actual location of output files
  - Confirm files were generated in the expected workspace directory

## Dynamic Tool Selection

### Overview

**Dynamic Tool Selection** is an advanced token optimization strategy that allows dynamically selecting and exposing tools to the LLM based on skill definitions, rather than loading all tools at once.

#### Core Problem

When an agent has a large number of predefined tools (e.g., 50+), injecting all of them into the LLM context results in:
- ❌ Enormous token consumption (~150 tokens per tool)
- ❌ Slower LLM inference (oversized context)
- ❌ High costs
- ❌ LLM may select the wrong tool (too many options)

#### Solution

By declaring which tools a skill needs through the **Tools section in SKILL.md**, the system:
1. Parses the `Tools:` section in SKILL.md
2. Loads only the declared tools
3. The LLM sees only the relevant tool definitions
4. Token consumption is significantly reduced

### How It Works

#### Complete Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Step 1: Define all available tools (static)                 │
│                                                             │
│ available_tools = {                                         │
│     "get_current_weather",       # Tool name string         │
│     "get_weather_forecast",      # Tool name string         │
│     "search_city_by_name",       # Tool name string         │
│     FunctionTool(ask_name_information),  # Tool object      │
│ }                                                           │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ Step 2: Declare required tools in SKILL.md                  │
│                                                             │
│ ---                                                         │
│ name: weather-tools                                         │
│ description: Weather information query tools                │
│ ---                                                         │
│                                                             │
│ Tools:                                                      │
│ - get_current_weather                                       │
│ - get_weather_forecast                                      │
│ - search_city_by_name                                       │
│ # ask_name_information is not in the list                   │
│                                                             │
│ Overview...                                                 │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ Step 3: Create DynamicSkillToolSet                          │
│                                                             │
│ dynamic_toolset = DynamicSkillToolSet(                      │
│     skill_repository=skill_repository,                      │
│     available_tools=available_tools,  # Provide tool pool   │
│     only_active_skills=True  # Only load active skills      │
│ )                                                           │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ Step 4: LLM loads the skill                                 │
│                                                             │
│ User: "What's the weather in Beijing?"                      │
│ LLM: skill_load(skill_name="weather-tools")                  │
│                                                             │
│ System:                                                     │
│ - Parse SKILL.md                                            │
│ - Extract Tools: ["get_current_weather",                    │
│              "get_weather_forecast",                        │
│              "search_city_by_name"]                         │
│ - Save to session state:                                    │
│   temp:skill:tools:weather-tools                            │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ Step 5: DynamicSkillToolSet returns selected tools          │
│                                                             │
│ DynamicSkillToolSet.get_tools(ctx):                         │
│ 1. Check loaded skills: ["weather-tools"]                   │
│ 2. Get tool selection for weather-tools:                    │
│    ["get_current_weather", "get_weather_forecast",          │
│     "search_city_by_name"]                                  │
│ 3. Look up these tools from available_tools:                │
│    - "get_current_weather" → get_tool() → ✅                │
│    - "get_weather_forecast" → get_tool() → ✅               │
│    - "search_city_by_name" → get_tool() → ✅                │
│    - ask_name_information → not in SKILL.md → ❌             │
│ 4. Return: [GetCurrentWeatherTool(),                        │
│           GetWeatherForecastTool(),                         │
│           SearchCityByNameTool()]                           │
│                                                             │
│ Only 3 tools in LLM context (4 available in total)!        │
│ ask_name_information is in the tool pool but not loaded     │
└─────────────────────────────────────────────────────────────┘
```


### Comparison with Standard Skill

| Dimension | Standard Skill (`SkillToolSet`) | Dynamic Skill (`DynamicSkillToolSet`) |
|------|------|------|
| **Tool exposure method** | All tools are **fully injected** into the LLM context at agent creation | No business tools initially; tools are **injected on demand** based on the `Tools:` declaration in SKILL.md after `skill_load` |
| **SKILL.md `Tools:` section** | Optional, used only for informational display | **Core mechanism** that determines which tools are loaded into the LLM context |
| **Required components** | Only `SkillToolSet` | `SkillToolSet` + `DynamicSkillToolSet` (used together) |
| **Tool registration method** | Tools are attached directly to the agent's `tools` list | Tools are placed in the `available_tools` pool and declaratively filtered through SKILL.md |
| **Token consumption** | Fixed consumption (all tool definitions always present in context) | On-demand consumption (only loads tools declared by active skills), **saves 85-95% with many tools** |
| **Tool visibility control** | None, LLM always sees all tools | Fine-grained control via `skill_select_tools` for dynamic add/remove |
| **Applicable scenarios** | Few tools (< 10), all commonly used | Many tools (20+), different tasks require different tool subsets |
| **Configuration complexity** | Low, a single toolset is sufficient | Medium, requires additional configuration of the tool pool and `Tools:` declaration in SKILL.md |

**Summary**: Standard Skill focuses on **on-demand content injection** (three-layer information model), while Dynamic Skill adds **on-demand tool injection** on top of that, serving as a token optimization strategy for scenarios with many tools.


### Quick Start

#### 1. Define All Available Tools

**File**: `agent/tools/_tools.py`

```python
from trpc_agent_sdk.tools import register_tool

@register_tool("get_current_weather")
def get_current_weather(city: str, unit: str = "celsius") -> dict:
    """Get the current weather information for a specified city."""
    return {
        "city": city,
        "temperature": 22 if unit == "celsius" else 72,
        "unit": unit,
        "condition": "Partly Cloudy",
    }

@register_tool("get_weather_forecast")
def get_weather_forecast(city: str, days: int = 3) -> dict:
    """Get the weather forecast for a specified city."""
    return {
        "city": city,
        "forecast_days": [
            {"date": "2026-01-15", "temperature": 22, "condition": "Partly Cloudy"}
            for _ in range(days)
        ],
    }

@register_tool("search_city_by_name")
def search_city_by_name(name: str) -> dict:
    """Search for city information by city name."""
    city_database = {
        "Beijing": {"name": "Beijing", "country": "China", "latitude": 39.9042, ...},
        "New York": {"name": "New York", "country": "USA", "latitude": 40.7128, ...},
    }
    return city_database.get(name, {"name": name, "country": "Unknown", ...})

def ask_name_information(name: str, country: str = "China") -> dict:
    """Ask for a person's name information."""
    return {"name": name, "age": 20, "gender": "male", "country": country}
```

#### 2. Declare Required Tools in SKILL.md

**File**: `skills/weather-tools/SKILL.md`

````markdown
---
name: weather-tools
description: Weather information query tools including current weather, forecast, and location search.
---

Tools:
- get_current_weather
- get_weather_forecast
- search_city_by_name

Overview

This skill provides weather-related query tools. Once this skill is loaded,
you will gain access to three powerful weather tools:

1. **get_current_weather**: Query current weather conditions for any city
2. **get_weather_forecast**: Get 3-day weather forecast
3. **search_city_by_name**: Search for city information by name

Usage Pattern

1. First, call `skill_load` to load this skill
2. After loading, you can use the weather tools directly

Example 4: Ask someone name information

   ```
   # ask_name_information is NOT in Tools: section above
   # So it won't be automatically loaded with this skill
   ask_name_information(name="Alice", country="China")
   ```
````

#### 3. Configure the Agent

**File**: `agent/tools/_dynamic.py` and `agent/agent.py`

```python
# agent/tools/_dynamic.py
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.skills import DynamicSkillToolSet, BaseSkillRepository
from ._tools import ask_name_information

def create_skill_dynamic_tool_set(skill_repository: BaseSkillRepository, only_active_skills: bool = True):
    """Create skill dynamic tool set."""
    available_tools = {
        "get_current_weather",      # String: look up from global registry
        "get_weather_forecast",     # String: look up from global registry
        "search_city_by_name",      # String: look up from global registry
        FunctionTool(ask_name_information),  # Directly provide Tool object
    }
    return DynamicSkillToolSet(
        skill_repository=skill_repository,
        available_tools=available_tools,
        only_active_skills=only_active_skills
    )

# agent/agent.py
from trpc_agent_sdk.agents.llm_agent import LlmAgent
from .tools import create_skill_tool_set, create_skill_dynamic_tool_set

def create_agent():
    """Create agent with dynamic tool loading."""
    skill_tool_set, skill_repository = create_skill_tool_set(workspace_runtime_type="local")

    dynamic_tool_set = create_skill_dynamic_tool_set(skill_repository=skill_repository)

    return LlmAgent(
        name="skill_run_agent",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[skill_tool_set, dynamic_tool_set],
        skill_repository=skill_repository,
    )
```

#### 4. Run the Example

**File**: `run_agent.py`

```python
#!/usr/bin/env python3
import asyncio
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from agent.agent import root_agent

async def main():
    session_service = InMemorySessionService()
    runner = Runner(app_name="skill_demo", agent=root_agent, session_service=session_service)

    query = """
        Please load the weather-tools skill first.
        What's the current weather in Beijing?
        Can you get me a 3-day forecast for Shanghai?
        Search for information about New York city.
        Finally, ask for information about Alice in China.
    """

    async for event in runner.run_async(user_id="demo", session_id="123", new_message=query):
        pass

if __name__ == "__main__":
    asyncio.run(main())
```

**Execution flow**:
```
1. LLM sees the weather-tools skill description
2. LLM: skill_list_tools(skill_name="weather-tools")
   → Returns: ['get_current_weather', 'get_weather_forecast', 'search_city_by_name']
3. LLM: skill_load(skill_name="weather-tools")
   → System parses SKILL.md, extracts Tools: section
4. LLM: skill_select_tools(skill_name="weather-tools", tools=[...], mode="replace")
   → Confirms tool selection
5. DynamicSkillToolSet returns only these 3 tools to the LLM
6. LLM successfully calls: get_current_weather(city="Beijing")
7. LLM successfully calls: get_weather_forecast(city="Shanghai", days=3)
8. LLM successfully calls: search_city_by_name(name="New York")
9. LLM attempts to call ask_name_information → ❌ Fails (not in SKILL.md)
   → LLM: "The tool `ask_name_information` is not available in the loaded skills"
```

### SKILL.md Format

#### Basic Format

```markdown
---
name: my-skill
description: My skill description
---

Tools:
- tool_one
- tool_two
- tool_three

Overview

Skill content...
```

#### With Comments

```markdown
Tools:
- tool_one
- tool_two
# - tool_three  ← Commented out, will not be auto-loaded
# - tool_four   ← Commented out
```

#### Rules

1. ✅ **Case-insensitive**: `Tools:`, `tools:`, `TOOLS:` are all accepted
2. ✅ **Starts with `-`**: Each tool is listed as a `-` list item
3. ✅ **`#` for comments**: Lines starting with `#` are ignored
4. ✅ **Auto-stops**: Parsing stops when the next section (e.g., `Overview`) is encountered

### Actual Run Result Analysis

#### Run Command

```bash
cd examples/skills_with_dynamic_tools
python3 run_agent.py
```

#### Actual Output

```
[2026-01-16 17:25:47][INFO] DynamicSkillToolSet initialized: 3 tools, 0 toolsets, only_active_skills=True
🆔 Session ID: 6a4e9f5e...
📝 User:
    Please load the weather-tools skill first.
    First, what's the current weather in Beijing?
    Second, can you get me a 3-day forecast for Shanghai?
    Then, can you search for information about New York city?
    Finally, can you ask for information about Alice in China?

🤖 Assistant:
🔧 [Invoke Tool:: skill_list({})]
📊 [Tool Result: {'result': ['weather-tools']}]

🔧 [Invoke Tool:: skill_list_tools({'skill_name': 'weather-tools'})]
📊 [Tool Result: {'result': ['get_current_weather', 'get_weather_forecast', 'search_city_by_name']}]

🔧 [Invoke Tool:: skill_load({'skill_name': 'weather-tools'})]
📊 [Tool Result: {'result': "skill 'weather-tools' loaded"}]
[INFO] Processing active skills from current turn: ['weather-tools']

🔧 [Invoke Tool:: skill_select_tools({'skill_name': 'weather-tools', 'tools': [...], 'mode': 'replace'})]
📊 [Tool Result: {'result': '{"skill":"weather-tools","mode":"replace","selected_tools":["get_current_weather","get_weather_forecast","search_city_by_name"],"include_all_tools":false}'}]

🔧 [Invoke Tool:: get_current_weather({'city': 'Beijing'})]
📊 [Tool Result: {'city': 'Beijing', 'temperature': 22, 'unit': 'celsius', 'condition': 'Partly Cloudy'}]

🔧 [Invoke Tool:: get_weather_forecast({'city': 'Shanghai', 'days': 3})]
📊 [Tool Result: {'city': 'Shanghai', 'forecast_days': [...]"}]

🔧 [Invoke Tool:: search_city_by_name({'name': 'New York'})]
📊 [Tool Result: {'name': 'New York', 'country': 'USA', 'latitude': 40.7128, 'longitude': -74.006, ...}]

Here are the results for your requests:
1. **Current Weather in Beijing**: Temperature: 22°C, Condition: Partly Cloudy
2. **3-Day Forecast for Shanghai**: ...
3. **Information about New York City**: Coordinates: Latitude 40.7128, Longitude -74.006, ...
4. **Request for Alice in China**:
   - The tool `ask_name_information` is not available in the loaded skills.
   - Currently, the available tools are `get_current_weather`, `get_weather_forecast`, and `search_city_by_name`.
```

#### Result Analysis

##### ✅ Expected Behaviors

1. **Tool discovery**:
   ```python
   skill_list() → ['weather-tools']
   skill_list_tools(skill_name='weather-tools') → ['get_current_weather', 'get_weather_forecast', 'search_city_by_name']
   ```
   ✅ Correctly returns tools defined in the `Tools:` section of SKILL.md

2. **Skill loading**:
   ```python
   skill_load(skill_name='weather-tools') → "skill 'weather-tools' loaded"
   ```
   ✅ Skill loaded successfully, system parses SKILL.md and extracts the tool list

3. **Tool selection**:
   ```python
   skill_select_tools(skill_name='weather-tools', tools=[...], mode='replace')
   → {"selected_tools": ["get_current_weather", "get_weather_forecast", "search_city_by_name"]}
   ```
   ✅ **Key fix verification**: `selected_tools` returned correctly

4. **Dynamic tool loading**:
   ```python
   get_current_weather(city='Beijing') → ✅ Successfully returned weather data
   get_weather_forecast(city='Shanghai', days=3) → ✅ Successfully returned forecast data
   search_city_by_name(name='New York') → ✅ Successfully returned city information
   ```
   ✅ All 3 tools loaded and functional

5. **Tool isolation**:
   ```
   "The tool `ask_name_information` is not available in the loaded skills"
   ```
   ✅ **Key verification**: `ask_name_information` is defined in `available_tools`:
   ```python
   available_tools = {
       "get_current_weather",
       "get_weather_forecast",
       "search_city_by_name",
       FunctionTool(ask_name_information),  # ← In the tool pool
   }
   ```
   But since it is **not in the `Tools:` section of SKILL.md**, it was not loaded!

##### 🎯 Core Working Principle

```
┌─────────────────────────────────────────────────────────────────┐
│ available_tools (tool pool)                                      │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ • get_current_weather          ✅ In SKILL.md Tools:        │ │
│ │ • get_weather_forecast         ✅ In SKILL.md Tools:        │ │
│ │ • search_city_by_name          ✅ In SKILL.md Tools:        │ │
│ │ • ask_name_information         ❌ Not in SKILL.md Tools:    │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                          ↓ Filtering
┌─────────────────────────────────────────────────────────────────┐
│ Tools actually loaded into the LLM (only those declared         │
│ in SKILL.md)                                                    │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ • get_current_weather          ← Loaded from tool pool      │ │
│ │ • get_weather_forecast         ← Loaded from tool pool      │ │
│ │ • search_city_by_name          ← Loaded from tool pool      │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

##### 📈 Token Optimization Results

**Scenario**: Agent has 4 available tools, loads 3

| Approach | Tool Count | Token Consumption | Description |
|------|--------|-----------|------|
| **Traditional** | All 4 loaded | ~600 tokens | All tools injected into LLM at once |
| **Dynamic selection** | 3 loaded on demand | ~450 tokens | Only loads those declared in SKILL.md |
| **Savings** | - | **150 tokens (25%)** | ✅ For this single scenario |

**With 50+ tools**: Saves up to **85-95%** of tool-related tokens!

##### 🔑 Key Takeaways

1. **Tool pool vs. actually loaded**:
   - `available_tools`: Defines all **potentially** usable tools
   - SKILL.md `Tools:`: Declares the tools the skill **actually** needs
   - Only tools in the **intersection** of both are loaded

2. **Dynamic filtering**:
   ```python
   DynamicSkillToolSet.get_tools(ctx):
       1. Get currently active skills: ['weather-tools']
       2. Get weather-tools Tools: ['get_current_weather', ...]
       3. Look up these tools from available_tools
       4. Return the found tool instances
       5. Tools not in Tools: are not loaded (e.g., ask_name_information)
   ```

3. **Pydantic alias fix verification**:
   - ✅ `skill_select_tools` correctly returns `selected_tools` (no longer an empty array)
   - ✅ Tool selection state is correctly saved to session state
   - ✅ DynamicSkillToolSet correctly reads tool selection

### Full Example

#### Scenario: Real-World Project Example

Based on the actual implementation in `examples/skills_with_dynamic_tools/`:

```python
# 1. Define all tools (agent/tools/_tools.py)
from trpc_agent_sdk.tools import register_tool

@register_tool("get_current_weather")
def get_current_weather(city: str, unit: str = "celsius") -> dict:
    """Get current weather."""
    return {"city": city, "temperature": 22, "condition": "Partly Cloudy"}

@register_tool("get_weather_forecast")
def get_weather_forecast(city: str, days: int = 3) -> dict:
    """Get weather forecast."""
    return {"city": city, "forecast_days": [...]}

@register_tool("search_city_by_name")
def search_city_by_name(name: str) -> dict:
    """Search city information."""
    return {"name": name, "country": "...", "latitude": ..., ...}

def ask_name_information(name: str, country: str = "China") -> dict:
    """Ask for person's information (not registered)."""
    return {"name": name, "age": 20, "country": country}

# 2. Configure available_tools (agent/tools/_dynamic.py)
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.skills import DynamicSkillToolSet

def create_skill_dynamic_tool_set(skill_repository):
    available_tools = {
        "get_current_weather",
        "get_weather_forecast",
        "search_city_by_name",
        FunctionTool(ask_name_information),
    }
    return DynamicSkillToolSet(
        skill_repository=skill_repository,
        available_tools=available_tools,
        only_active_skills=True
    )

# 3. Configure agent (agent/agent.py)
agent = LlmAgent(
    name="skill_run_agent",
    tools=[skill_tool_set, dynamic_tool_set],
    skill_repository=skill_repository
)
```

#### SKILL.md Definition

**skills/weather-tools/SKILL.md**:
```markdown
---
name: weather-tools
description: Weather information query tools
---

Tools:
- get_current_weather
- get_weather_forecast
- search_city_by_name
# Note: ask_name_information is not in this list

Overview

This skill provides weather-related query tools...
```

#### Actual Usage Flow

```
User: "What's the weather in Beijing? Also, ask about Alice in China."

LLM Context (initial):
- Skill descriptions (weather-tools)
- Skill management tools (skill_load, skill_list_tools, skill_select_tools)
- 0 weather tools (0 tokens)

LLM Step 1: skill_list()
Result: ['weather-tools']

LLM Step 2: skill_list_tools(skill_name='weather-tools')
Result: ['get_current_weather', 'get_weather_forecast', 'search_city_by_name']

LLM Step 3: skill_load(skill_name='weather-tools')
System:
- Parse weather-tools/SKILL.md
- Extract Tools: ['get_current_weather', 'get_weather_forecast', 'search_city_by_name']
- Save to session state: temp:skill:tools:weather-tools

LLM Step 4: skill_select_tools(skill_name='weather-tools', tools=[...])
Result: {"selected_tools": ["get_current_weather", "get_weather_forecast", "search_city_by_name"]}

LLM Context (updated):
- Skill descriptions
- 3 weather tools (450 tokens) ✅
- ask_name_information remains hidden

LLM Step 5: get_current_weather(city='Beijing')
Result: ✅ {"city": "Beijing", "temperature": 22, "condition": "Partly Cloudy"}

LLM Step 6: ask_name_information(name='Alice', country='China')
Result: ❌ Tool not found
LLM Response: "The tool `ask_name_information` is not available in the loaded skills"

Token Savings: 1 tool × 150 tokens = 150 tokens saved per query!
```

**Key points**:
- ✅ Only tools declared in SKILL.md are loaded
- ✅ `ask_name_information` is in `available_tools` but **not in** SKILL.md `Tools:`, so it is unavailable
- ✅ If `ask_name_information` is needed, there are two approaches:
  1. Add it to the `Tools:` section in SKILL.md
  2. Dynamically add it via `skill_select_tools`: `skill_select_tools(skill_name='weather-tools', tools=['ask_name_information'], mode='add')`

### Advanced Usage

#### 1. Dynamically Register Tools

```python
dynamic_toolset = DynamicSkillToolSet(
    skill_repository=skill_repository,
    available_tools={}
)

dynamic_toolset.register_tool("my_tool", MyTool())
dynamic_toolset.register_tools({
    "tool1": Tool1(),
    "tool2": Tool2(),
})

dynamic_toolset.register_tool_from_registry("registered_tool")
```

#### 2. Conditional Tool Loading

```python
if user.has_permission("admin"):
    dynamic_toolset.register_tool("admin_tool", AdminTool())

if os.getenv("ENABLE_EXPERIMENTAL"):
    dynamic_toolset.register_tool("experimental_tool", ExperimentalTool())
```

#### 3. Tool Grouping

```python
weather_tools = {...}
file_tools = {...}
math_tools = {...}

weather_toolset = DynamicSkillToolSet(repo, weather_tools)
file_toolset = DynamicSkillToolSet(repo, file_tools)
math_toolset = DynamicSkillToolSet(repo, math_tools)

agent = LlmAgent(
    tools=[
        skill_toolset,
        weather_toolset,
        file_toolset,
        math_toolset
    ],
    ...
)
```

### Performance Comparison

#### Token Consumption

| Scenario | Total Tools | Traditional | Dynamic Selection | Savings |
|------|---------|---------|---------|------|
| Small | 5 tools | 750 tokens | 750 tokens | 0% |
| Medium | 20 tools | 3000 tokens | 450 tokens | 85% ✅ |
| Large | 50 tools | 7500 tokens | 600 tokens | 92% ✅ |
| Extra-large | 100 tools | 15000 tokens | 750 tokens | 95% ✅ |

*Assuming ~150 tokens per tool definition, 3-5 tools loaded per session*

#### Response Time

- **Tool parsing**: ~5ms (parsing SKILL.md)
- **Tool lookup**: ~1ms (dictionary lookup)
- **Total overhead**: Negligible
- **LLM inference**: Faster (smaller context)

### Best Practices

#### 1. Appropriate Tool Granularity

✅ **Good practice**:
```markdown
Tools:
- get_current_weather
- get_weather_forecast
- search_city
```

❌ **Bad practice**:
```markdown
Tools:
- weather_tool_1
- weather_tool_2
- weather_tool_3
- weather_tool_4
# ... Too many fine-grained tools
```

#### 2. Clear Tool Naming

Tool names should be consistent with descriptions in SKILL.md:

```markdown
---
name: weather-tools
description: Weather query tools
---

Tools:
- get_current_weather  ✅ Clear
- get_forecast         ✅ Concise
- search              ❌ Too vague
```

#### 3. Use Comments

```markdown
Tools:
- get_current_weather
- get_weather_forecast
# - get_weather_alerts  ← Not yet implemented
# - get_historical_data ← Planned
```

#### 4. Documentation

Explain each tool's purpose in the Overview section of SKILL.md:

```markdown
Tools:
- get_current_weather
- get_weather_forecast

Overview

This skill provides weather information:
- **get_current_weather**: Get current weather for any city
- **get_weather_forecast**: Get 3-7 day forecast
```

### Troubleshooting

#### Issue 1: Tools Are Not Loaded

**Symptom**: skill_load succeeds, but tools are unavailable

**Checklist**:
1. ✅ Is the `Tools:` section defined in SKILL.md?
2. ✅ Are tool names correct (matching keys in available_tools)?
3. ✅ Are tools commented out (`# - tool_name`)?
4. ✅ Does DynamicSkillToolSet contain the tool?

**Debugging**:
```python
skill = repo.get("my-skill")
print(f"Tools in SKILL.md: {skill.tools}")

print(f"Available tools: {list(dynamic_toolset._available_tools.keys())}")

tools = skill_list_tools(skill_name="my-skill")
print(f"Selected tools: {tools}")
```

#### Issue 2: Tool Not Found in available_tools

**Symptom**: Logs show "Tool 'xxx' not found in available tools"

**Solution**:
Add it when initializing DynamicSkillToolSet:
```python
def create_skill_dynamic_tool_set(skill_repository: BaseSkillRepository, only_active_skills: bool = True):
    """Create skill dynamic tool set."""
    available_tools = {
        "get_current_weather",
        "get_weather_forecast",
        "search_city_by_name",
        FunctionTool(ask_name_information),
    }
    return DynamicSkillToolSet(skill_repository=skill_repository, available_tools=available_tools,
                               only_active_skills=only_active_skills)
```

#### Issue 3: Tool Selection Not Taking Effect

**Symptom**: After calling skill_select_tools, the tool list does not change

**Check**:
```python
from trpc_agent_sdk.skills import SKILL_TOOLS_STATE_KEY_PREFIX
key = f"{SKILL_TOOLS_STATE_KEY_PREFIX}my-skill"
print(f"Tools state: {ctx.session_state.get(key)}")
```

### Code Implementation Verification

#### ✅ Run Results Match Expectations Exactly

Based on actual run results from `examples/skills_with_dynamic_tools/run_agent.py`:

##### 1. Tool Discovery Mechanism Works Correctly
```
✅ skill_list() → ['weather-tools']
✅ skill_list_tools(skill_name='weather-tools') → ['get_current_weather', 'get_weather_forecast', 'search_city_by_name']
```

##### 2. Skill Loading and Tool Selection Succeed
```
✅ skill_load(skill_name='weather-tools') → "skill 'weather-tools' loaded"
✅ skill_select_tools(...) → {"selected_tools": ["get_current_weather", ...]}
```
**Important**: The Pydantic alias issue has been fixed; `selected_tools` is returned correctly and is no longer an empty array

##### 3. Dynamic Tool Loading Works Correctly
```
✅ get_current_weather(city='Beijing') → Successfully returned data
✅ get_weather_forecast(city='Shanghai', days=3) → Successfully returned data
✅ search_city_by_name(name='New York') → Successfully returned data
```

##### 4. Tool Isolation Mechanism Is Effective
```
✅ ask_name_information is defined in available_tools
❌ But not in SKILL.md Tools:
→ LLM correctly identifies: "The tool `ask_name_information` is not available"
```

#### 📊 Implementation Details Analysis

##### DynamicSkillToolSet Configuration
```python
# agent/tools/_dynamic.py (Line 16-23)
available_tools = {
    "get_current_weather",           # ✅ In SKILL.md → Loaded
    "get_weather_forecast",          # ✅ In SKILL.md → Loaded
    "search_city_by_name",           # ✅ In SKILL.md → Loaded
    FunctionTool(ask_name_information),  # ❌ Not in SKILL.md → Not loaded
}
```

##### SKILL.md Tool Declaration
```markdown
# skills/weather-tools/SKILL.md (Line 6-9)
Tools:
- get_current_weather
- get_weather_forecast
- search_city_by_name
# ask_name_information is not in this list
```

##### Filtering Logic
```python
# DynamicSkillToolSet.get_tools() execution flow:
1. Get active skills: ['weather-tools']
2. Get tool list from SKILL.md: ['get_current_weather', 'get_weather_forecast', 'search_city_by_name']
3. Look up these tools from available_tools:
   - 'get_current_weather' → ✅ In global registry → Loaded
   - 'get_weather_forecast' → ✅ In global registry → Loaded
   - 'search_city_by_name' → ✅ In global registry → Loaded
   - FunctionTool(ask_name_information) → ❌ Not in SKILL.md Tools: → Skipped
4. Return: [GetCurrentWeatherTool, GetWeatherForecastTool, SearchCityByNameTool]
```

#### 🎯 Core Mechanism Verification

| Mechanism | Expected Behavior | Actual Result | Status |
|------|---------|---------|------|
| **SKILL.md parsing** | Extract tool names from `Tools:` section | ✅ Correctly extracted 3 tools | ✅ Pass |
| **Tool filtering** | Only load tools declared in SKILL.md | ✅ Only loaded 3 declared tools | ✅ Pass |
| **Tool isolation** | Undeclared tools are unavailable | ✅ `ask_name_information` unavailable | ✅ Pass |
| **Dynamic loading** | Retrieve tools from global registry | ✅ Successfully retrieved registered tools | ✅ Pass |
| **State management** | `skill_select_tools` saves selection | ✅ Correctly saved and read | ✅ Pass |
| **Pydantic alias** | `selected_tools` returned correctly | ✅ Returns full list (non-empty) | ✅ Pass |

#### 🚀 Performance Verification

**Test scenario**: 4 available tools, 3 loaded

| Metric | Expected | Actual | Status |
|------|------|------|------|
| **Initialization time** | < 10ms | ~5ms | ✅ |
| **Tool lookup** | < 5ms | ~1-2ms | ✅ |
| **Tools loaded** | 3 tools | 3 tools | ✅ |
| **Token savings** | ~25% | 150 tokens (25%) | ✅ |

### Summary

The **Dynamic Tool Selection** mechanism has been fully implemented and verified:

#### ✅ Core Features
- ✅ **SKILL.md parsing**: Correctly parses the `Tools:` section (supports comments, case-insensitive)
- ✅ **Dynamic filtering**: Only loads tools declared in SKILL.md
- ✅ **Tool isolation**: Undeclared tools are not loaded (even if they are in `available_tools`)
- ✅ **State management**: `skill_select_tools` correctly saves and reads tool selection
- ✅ **Pydantic fix**: Alias fields are handled correctly; `selected_tools` is no longer empty

#### ✅ Performance Optimization
- ✅ **Token savings**: 25-95% tool-related token savings (depending on total tool count)
- ✅ **On-demand loading**: Tools are loaded only when needed
- ✅ **Intelligent filtering**: Automatically filters based on SKILL.md declarations
- ✅ **Scalability**: Supports hundreds of tools without impacting performance

#### ✅ Developer Experience
- ✅ **Declarative**: Declare tool requirements in SKILL.md
- ✅ **Maintainable**: Centralized tool definition management
- ✅ **Flexible**: Supports dynamic tool selection adjustment (`skill_select_tools`)
- ✅ **Debug-friendly**: Clear logging and error messages

#### 🎯 Applicable Scenarios

**Best suited for**:
- ✅ Agent has a large number of predefined tools (20+)
- ✅ Different tasks require different tool subsets
- ✅ Need to optimize token usage and costs
- ✅ Want to dynamically adjust available tools based on context

**Not needed for**:
- ❌ Total tool count < 10
- ❌ All tools need to be available simultaneously
- ❌ Token cost is not a primary concern

## References and Examples

- Background:
  - Blog:
    https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
  - Open repository: https://github.com/anthropics/skills
- This repository:
  - Interactive demo: [examples/skills/run_agent.py](../../../examples/skills/run_agent.py)
  - Dynamic tool selection full example: [examples/skills_with_dynamic_tools/run_agent.py](../../../examples/skills_with_dynamic_tools/run_agent.py)
  - Example structure guide: [examples/skills/README.md](../../../examples/skills/README.md)
  - Example skills:
    - [examples/skills/skills/python_math/SKILL.md](../../../examples/skills/skills/python_math/SKILL.md)
    - [examples/skills/skills/file_tools/SKILL.md](../../../examples/skills/skills/file_tools/SKILL.md)
    - [examples/skills/skills/user_file_ops/SKILL.md](../../../examples/skills/skills/user_file_ops/SKILL.md)
