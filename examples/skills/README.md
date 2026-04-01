# Agent Skills: Interactive skill_run Example

This example demonstrates an end-to-end interactive chat using `Runner`
and `LlmAgent` with Agent Skills. The assistant streams content, shows
tool calls and tool responses, and executes skill scripts via the
`skill_run` tool without inlining script bodies.

## Features

- Interactive chat with streaming or non-streaming modes
- Agent Skills repository injection and overview
- `skill_load` to load SKILL.md/doc content on demand
- `skill_run` to execute commands safely in a workspace, returning
  stdout/stderr and output files
  (and optionally saving files as artifacts)
- Clear visualization of tool calls and tool responses
- Example `user-file-ops` skill to summarize user-provided text files

## Prerequisites

- Python 3.10 or later
- Valid API key and base URL for your model provider (OpenAI-compatible)

## Environment Variables

The example uses environment variables for configuration. You can set them directly or use a `.env` file (the script automatically loads `.env` using `python-dotenv`).

| Variable          | Description                              | Default                  |
| ----------------- | ---------------------------------------- | ------------------------ |
| `TRPC_AGENT_API_KEY` | API key for the model service (required) | ``                       |
| `TRPC_AGENT_BASE_URL` | Base URL for the model API endpoint (required) | `` |
| `TRPC_AGENT_MODEL_NAME` | Name of the model to use (required) | `` |
| `SKILLS_ROOT`     | Skills repository root directory         | `./skills`               |

## Configuration

The example is organized into modular components:

- **Model Configuration**: Set via environment variables (`TRPC_AGENT_API_KEY`, `TRPC_AGENT_BASE_URL`, `TRPC_AGENT_MODEL_NAME`) or modify `agent/config.py`
- **Workspace Runtime**: Configure in `agent/tools.py` via `create_skill_tool_set(workspace_runtime_type="local")` (change to `"container"` for container runtime)
- **Artifact Settings**: Configure in `agent/tools.py` via `run_tool_kwargs`:
  - `save_as_artifacts`: Save files via artifact service (default: `True`)
  - `omit_inline_content`: Omit inline file contents (default: `False`)
  - `artifact_prefix`: Artifact filename prefix (e.g., `"user:"`)
- **Agent Instructions**: Modify `agent/prompts.py` to change the agent's behavior

## Usage

### Quick Start

1. Set up environment variables (create a `.env` file or export them):

```bash
cd examples/skills

# Option 1: Create a .env file
cat > .env << EOF
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=https://api.deepseek.com/v1
TRPC_AGENT_MODEL_NAME=deepseek-chat
SKILLS_ROOT=./skills
EOF

# Option 2: Export environment variables
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="https://api.deepseek.com/v1"
export TRPC_AGENT_MODEL_NAME="deepseek-chat"
export SKILLS_ROOT="./skills"
```

2. Run the example:

```bash
python3 run_agent.py
```

Workspace paths and env vars:
- `$SKILLS_DIR/<name>`: read-only staged skill
- `$WORK_DIR`: writable shared workspace (use `$WORK_DIR/inputs` for inputs)
- `$RUN_DIR`: per-run working directory
- `$OUTPUT_DIR`: unified outputs (collector/artifact saves read from here)

Optional inputs/outputs spec with `skill_run`:
- Inputs example (map external files into workspace):
  `{ "inputs": [ {"from": "artifact://datasets/raw.csv@3",
     "to": "work/inputs/raw.csv"} ] }`
- Outputs example (collect and save artifacts):
  `{ "outputs": {"globs": ["out/**/*.csv"], "save": true,
     "name_template": "user:", "inline": false } }`

Container zero-copy hint:
- Bind a host folder as the inputs base so `host://` inputs under that
  folder become symlinks inside the container (no copy):
  Modify `agent/tools.py` in `_create_workspace_runtime()` to use `workspace_runtime_type="container"` and pass `inputs_host="/path/to/datasets"`
- When `inputs_host` is set (local or container), the host folder is
  also available inside each skill workspace under `work/inputs`
  (and `inputs/` from the skill root).

### Use with anthropics/skills

You can test against the public Anthropics skills repository.

```bash
# 1) Clone the repo anywhere you like
git clone https://github.com/anthropics/skills \
  "$HOME/src/anthropics-skills"

# 2) Point the example at that repo
export SKILLS_ROOT="$HOME/src/anthropics-skills"

# 3) Run the example (local workspace executor)
python3 run_agent.py

# Optional: Use container executor for extra isolation (needs Docker)
# Modify agent/agent.py in create_agent() to change:
# workspace_runtime_type = "container"
```

In the code:
- The example demonstrates three skill usage scenarios:
  - `user-file-ops`: Summarize user-provided text files
  - `python-math`: Calculate Fibonacci numbers
  - `file-tools`: File operations and archiving
- The script runs each example in sequence, showing tool calls and responses.
- Artifact saving is enabled by default (`save_as_artifacts: True`).
- Modify `run_tool_kwargs` in `create_skill_tool_set()` to adjust artifact settings.

### Examples

To customize the example, modify the corresponding files:

**1. Change model configuration** (`agent/config.py` or environment variables):

```python
# In agent/config.py, modify get_model_config() or set environment variables:
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="https://api.deepseek.com/v1"
export TRPC_AGENT_MODEL_NAME="deepseek-chat"
```

**2. Change skills root** (environment variable):

```bash
export SKILLS_ROOT=/path/to/skills
```

**3. Use container workspace executor** (`agent/agent.py`):

```python
# In agent/agent.py, modify create_agent():
workspace_runtime_type = "container"  # Change from "local" to "container"
```

**4. Adjust artifact settings** (`agent/tools.py`):

```python
# In agent/tools.py, modify create_skill_tool_set():
tool_kwargs = {
    "save_as_artifacts": True,      # Enable/disable artifact saving
    "omit_inline_content": False,    # Omit inline content
    "artifact_prefix": "user:",      # Add prefix to artifact names
}
```

**5. Customize agent instructions** (`agent/prompts.py`):

```python
# In agent/prompts.py, modify INSTRUCTION to change agent behavior
```

### User File Processing Example (`user-file-ops`)

This example shows how to let the assistant summarize a text file that
you already have on your machine, using the `user-file-ops` skill.

1. The example script automatically creates a sample file:

   ```python
   os.system("echo 'hello from skillrun' > /tmp/skillrun-notes.txt")
   os.system("echo 'this is another line' >> /tmp/skillrun-notes.txt")
   ```

2. Set up environment variables and run the example:

   ```bash
   cd examples/skills
   export TRPC_AGENT_API_KEY="your-api-key"
   export TRPC_AGENT_BASE_URL="https://api.deepseek.com/v1"
   export TRPC_AGENT_MODEL_NAME="deepseek-chat"
   python3 run_agent.py
   ```

3. The script includes a predefined request:

   ```python
   user_file_ops_request = """
       I have a text file at /tmp/skillrun-notes.txt.
       Please use the user-file-ops skill to summarize it, you can use command `cp` to copy it to the workspace,
       then mapping it to `work/inputs/user-notes.txt` and writing the summary to `out/user-notes-summary.txt`
   """
   ```

   The assistant will typically:

   - load the `user-file-ops` skill with `skill_load`
   - run a command like:

     ```bash
     bash scripts/summarize_file.sh \
       work/inputs/user-notes.txt \
       out/user-notes-summary.txt
     ```

   The skill script computes simple statistics (lines, words, bytes)
   and includes the first few non-empty lines of the file in the
   summary.

4. With artifacts enabled by default (`save_as_artifacts: True`), the
   summary file is saved as an artifact automatically. You can access
   artifacts through the artifact service API in your application.

## Tips

- You can ask the assistant to list available skills (optional).
- No need to type "load"; the assistant loads skills when needed.
- Ask to run a command exactly as shown in the skill docs.

## Project Structure

```
examples/skills/
├── agent/                    # Agent configuration module
│   ├── agent.py            # Agent creation and setup
│   ├── tools.py            # Skill toolset creation
│   ├── config.py           # Model configuration from environment
│   └── prompts.py          # Agent instruction prompts
├── run_agent.py            # Main entry point
├── skills/                  # Skills repository
│   ├── python_math/
│   ├── file_tools/
│   └── user_file_ops/
└── README.md               # This file
```

## What You'll See

```
🆔 Session ID: a1b2c3d4...
📝 User: I have a text file at /tmp/skillrun-notes.txt.
        Please use the user-file-ops skill to summarize it...

🤖 Assistant:
🔧 [Invoke Tool:: skill_load({"skill": "user-file-ops"})]
📊 [Tool Result: {"status": "loaded"}]
🔧 [Invoke Tool:: skill_run({"skill": "user-file-ops", "command": "bash scripts/summarize_file.sh work/inputs/user-notes.txt out/user-notes-summary.txt"})]
📊 [Tool Result: {"stdout": "...", "exit_code": 0, "output_files": [...]}]

🤖 Assistant: I've successfully summarized your file...

----------------------------------------
🆔 Session ID: e5f6g7h8...
📝 User: Please use the python-math skill to calculate the first 10 Fibonacci numbers...

🤖 Assistant:
🔧 [Invoke Tool:: skill_load({"skill": "python-math"})]
🔧 [Invoke Tool:: skill_run({"skill": "python-math", "command": "python3 scripts/fib.py 10 > out/fib.txt"})]
📊 [Tool Result: {"stdout": "...", "output_files": [{"name": "out/fib.txt", "content": "...", "mime_type": "text/plain"}]}]

🤖 Assistant: I've calculated the first 10 Fibonacci numbers...

----------------------------------------
```

The output shows:
- Session IDs for each query
- User requests
- Tool calls (`skill_load`, `skill_run`) with their arguments
- Tool responses with execution results
- Assistant's final response summarizing the results
