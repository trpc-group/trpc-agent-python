# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Minimal graph workflow graph construction."""
from typing import Any
from typing import Dict
from typing import Optional

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
from trpc_agent_sdk.dsl.graph import GraphAgent
from trpc_agent_sdk.dsl.graph import NodeConfig
from trpc_agent_sdk.dsl.graph import STATE_KEY_LAST_RESPONSE
from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT
from trpc_agent_sdk.dsl.graph import State
from trpc_agent_sdk.dsl.graph import StateGraph
from trpc_agent_sdk.dsl.graph import StateMapper
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .callbacks import create_node_callbacks
from .config import get_model_config
from .nodes import ROUTE_CODE
from .nodes import ROUTE_KNOWLEDGE
from .nodes import ROUTE_LLM_AGENT
from .nodes import ROUTE_MCP
from .nodes import ROUTE_PREVIEW
from .nodes import ROUTE_SUBGRAPH
from .nodes import ROUTE_SUMMARIZE
from .nodes import ROUTE_TOOL
from .nodes import create_route_choice
from .nodes import decide_route
from .nodes import extract_document
from .nodes import format_output
from .nodes import prepare_mcp_request
from .nodes import resolve_knowledge_query
from .nodes import stream_preview
from .prompts import LLM_AGENT_INSTRUCTION
from .prompts import LLM_AGENT_WORKER_INSTRUCTION
from .prompts import SUMMARIZE_INSTRUCTION
from .prompts import TOOL_INSTRUCTION
from .state import DocumentState
from .tools import CODE_PYTHON_ANALYSIS
from .tools import create_mcp_toolset
from .tools import text_stats
from .tools import weather_tool


def _create_model() -> OpenAIModel:
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def _create_delegate_agent() -> GraphAgent:
    """Create a tiny sub-agent for the agent_node demo."""

    async def agent_reply(state: State) -> Dict[str, Any]:
        user_text = state.get(STATE_KEY_USER_INPUT, "")
        response = f"Agent handled this request: {user_text}" if user_text else "Agent had no input."
        return {STATE_KEY_LAST_RESPONSE: response}

    graph = StateGraph(State)
    graph.add_node(
        "agent_reply",
        agent_reply,
        config=NodeConfig(name="agent_reply", description="Simple sub-agent response"),
    )
    graph.set_entry_point("agent_reply")
    graph.set_finish_point("agent_reply")

    return GraphAgent(
        name="delegate",
        description="Sub-agent used by the main graph",
        graph=graph.compile(),
    )


def _create_llm_agent() -> LlmAgent:
    """Create an LLM sub-agent for llm_agent routing."""
    weather_query_tool = FunctionTool(weather_tool)

    llm_worker_agent = LlmAgent(
        name="domain_explainer",
        description="Domain specialist sub-agent used for delegated answers",
        model=_create_model(),
        instruction=LLM_AGENT_WORKER_INSTRUCTION,
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=300,
        ),
        disallow_transfer_to_parent=True,
    )

    return LlmAgent(
        name="query_orchestrator",
        description="LLM coordinator sub-agent used by the llm_agent route",
        model=_create_model(),
        instruction=LLM_AGENT_INSTRUCTION,
        generate_content_config=GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=300,
        ),
        tools=[weather_query_tool],
        sub_agents=[llm_worker_agent],
    )


def _create_document_workflow_graph(
    delegate_agent: GraphAgent,
    llm_agent: LlmAgent,
    knowledge_tool: Optional[LangchainKnowledgeSearchTool],
) -> StateGraph:
    model = _create_model()
    callbacks = create_node_callbacks()
    mcp_toolset = create_mcp_toolset()

    graph = StateGraph(DocumentState, callbacks=callbacks)

    # -- Common nodes --
    graph.add_node(
        "extract",
        extract_document,
        config=NodeConfig(name="extract", description="Extracts user input"),
    )

    graph.add_node(
        "decide",
        decide_route,
        config=NodeConfig(name="decide", description="Chooses which branch to run"),
    )

    graph.add_llm_node(
        "summarize",
        model,
        SUMMARIZE_INSTRUCTION,
        tools={},
        generation_config=GenerateContentConfig(temperature=0.2, max_output_tokens=500),
        config=NodeConfig(name="summarize", description="Summarizes long documents"),
    )

    tool = FunctionTool(text_stats)
    tools = {"text_stats": tool}

    graph.add_llm_node(
        "request_stats",
        model,
        TOOL_INSTRUCTION,
        tools=tools,
        tool_parallel=False,
        max_tool_iterations=4,
        generation_config=GenerateContentConfig(temperature=0.1, max_output_tokens=200),
        config=NodeConfig(name="request_stats", description="Requests tool stats"),
    )

    graph.add_node(
        "preview",
        stream_preview,
        config=NodeConfig(name="preview", description="Streams a short preview"),
    )

    graph.add_agent_node(
        "delegate",
        delegate_agent,
        config=NodeConfig(name="delegate", description="Delegates to a sub-agent"),
        input_mapper=StateMapper.rename({"document": STATE_KEY_USER_INPUT}),
        output_mapper=StateMapper.merge_response("subgraph_reply"),
    )

    graph.add_agent_node(
        "llm_agent",
        llm_agent,
        config=NodeConfig(name="llm_agent", description="Delegates query text to LLM sub-agent"),
        input_mapper=StateMapper.rename({"document": STATE_KEY_USER_INPUT}),
        output_mapper=StateMapper.merge_response("query_reply"),
    )

    # -- Code execution node --
    graph.add_code_node(
        "code_exec",
        UnsafeLocalCodeExecutor(timeout=30, work_dir="", clean_temp_files=True),
        CODE_PYTHON_ANALYSIS,
        "python",
        config=NodeConfig(name="code_exec", description="Executes a Python analysis script"),
    )

    # -- MCP nodes (stdio, self-contained) --
    graph.add_node(
        "prepare_mcp_request",
        prepare_mcp_request,
        config=NodeConfig(name="prepare_mcp_request", description="Prepares MCP request args"),
    )
    graph.add_mcp_node(
        "mcp_call",
        mcp_toolset,
        selected_tool_name="calculate",
        req_src_node="prepare_mcp_request",
        config=NodeConfig(name="mcp_call", description="Calls the MCP calculate tool"),
    )

    # -- Build dynamic route map --
    path_map: dict[str, str] = {
        ROUTE_PREVIEW: "preview",
        ROUTE_SUMMARIZE: "summarize",
        ROUTE_SUBGRAPH: "delegate",
        ROUTE_LLM_AGENT: "llm_agent",
        ROUTE_TOOL: "request_stats",
        ROUTE_CODE: "code_exec",
        ROUTE_MCP: "prepare_mcp_request",
    }

    # -- Knowledge search node (optional, needs TRAG_NAMESPACE env var) --
    if knowledge_tool is not None:
        graph.add_knowledge_node(
            "knowledge_search",
            resolve_knowledge_query,
            knowledge_tool,
            config=NodeConfig(name="knowledge_search", description="Searches knowledge base"),
        )
        path_map[ROUTE_KNOWLEDGE] = "knowledge_search"

    graph.add_node(
        "format_output",
        format_output,
        config=NodeConfig(name="format_output", description="Formats the final response"),
    )

    # -- Edges --
    graph.set_entry_point("extract")
    graph.set_finish_point("format_output")

    graph.add_edge("extract", "decide")
    graph.add_conditional_edges(
        "decide",
        create_route_choice(set(path_map.keys())),
        path_map,
    )

    graph.add_edge("preview", "format_output")
    graph.add_edge("summarize", "format_output")
    graph.add_edge("delegate", "format_output")
    graph.add_edge("llm_agent", "format_output")
    graph.add_edge("request_stats", "format_output")
    graph.add_edge("code_exec", "format_output")
    graph.add_edge("prepare_mcp_request", "mcp_call")
    graph.add_edge("mcp_call", "format_output")

    if knowledge_tool is not None:
        graph.add_edge("knowledge_search", "format_output")

    return graph


def create_agent(enable_knowledge: bool = False) -> GraphAgent:
    delegate_agent = _create_delegate_agent()
    llm_agent = _create_llm_agent()

    knowledge_tool: Optional[LangchainKnowledgeSearchTool] = None

    graph = _create_document_workflow_graph(
        delegate_agent,
        llm_agent,
        knowledge_tool,
    ).compile()
    return GraphAgent(
        name="graph",
        description="Minimal graph example showing conditional edges and node signatures",
        graph=graph,
    )


root_agent = create_agent()
