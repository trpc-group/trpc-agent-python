"""Agent configuration for the calculator agent."""

from dataclasses import dataclass, field


@dataclass
class AgentConfig:
    """Configuration for the agent under evaluation."""

    # Model settings
    model_name: str = "fake"
    temperature: float = 0.0
    max_tokens: int = 1024

    # Agent behavior
    system_prompt_path: str = "data/prompts/system.md"
    use_tools: bool = True
    strict_json_output: bool = False
    step_by_step_reasoning: bool = True

    # Tool configuration
    available_tools: list[str] = field(default_factory=lambda: [
        "calculate", "convert_unit", "lookup_formula",
    ])

    # Fake mode settings
    fake_deterministic: bool = True
    fake_seed: int = 42
