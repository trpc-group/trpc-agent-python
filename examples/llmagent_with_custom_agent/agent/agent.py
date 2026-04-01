# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module - Smart Document Processor with Custom Agent"""

from typing import AsyncGenerator

from pydantic import ConfigDict
from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.agents import ChainAgent
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.events import create_text_event
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel

from .config import get_model_config
from .prompts import COMPLEX_ANALYZER_INSTRUCTION
from .prompts import COMPLEX_PROCESSOR_INSTRUCTION
from .prompts import DOCUMENT_ANALYZER_INSTRUCTION
from .prompts import QUALITY_VALIDATOR_INSTRUCTION
from .prompts import SIMPLE_PROCESSOR_INSTRUCTION
from .prompts import TECHNICAL_PROCESSOR_INSTRUCTION


class SmartDocumentProcessor(BaseAgent):
    """Smart document processing Custom Agent

    Dynamically select processing strategies based on document type and complexity:
    - Simple documents: Direct processing
    - Complex documents: Analyze → Process → Validate
    - Technical documents: Special processing process

    This demonstrates the core capabilities of Custom Agents: conditional logic and dynamic agent selection
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    document_analyzer: LlmAgent
    simple_processor: LlmAgent
    complex_processor_chain: ChainAgent
    technical_processor: LlmAgent
    quality_validator: LlmAgent

    def __init__(self, model, **kwargs):
        document_analyzer = LlmAgent(
            name="document_analyzer",
            model=model,
            description="Analyze document type and complexity",
            instruction=DOCUMENT_ANALYZER_INSTRUCTION,
            output_key="doc_type",
        )

        simple_processor = LlmAgent(
            name="simple_processor",
            model=model,
            description="Process simple documents efficiently",
            instruction=SIMPLE_PROCESSOR_INSTRUCTION,
            output_key="processed_content",
        )

        complex_analyzer = LlmAgent(
            name="complex_analyzer",
            model=model,
            description="Analyze complex document structure",
            instruction=COMPLEX_ANALYZER_INSTRUCTION,
            output_key="complex_analysis",
        )

        complex_processor = LlmAgent(
            name="complex_processor",
            model=model,
            description="Process complex documents based on analysis",
            instruction=COMPLEX_PROCESSOR_INSTRUCTION,
            output_key="processed_content",
        )

        complex_processor_chain = ChainAgent(
            name="complex_processor_chain",
            description="Complex document processing: analyze → process",
            sub_agents=[complex_analyzer, complex_processor],
        )

        technical_processor = LlmAgent(
            name="technical_processor",
            model=model,
            description="Process technical documents with specialized approach",
            instruction=TECHNICAL_PROCESSOR_INSTRUCTION,
            output_key="processed_content",
        )

        quality_validator = LlmAgent(
            name="quality_validator",
            model=model,
            description="Validate processing quality and suggest improvements",
            instruction=QUALITY_VALIDATOR_INSTRUCTION,
            output_key="quality_feedback",
        )

        sub_agents_list = [
            document_analyzer,
            simple_processor,
            complex_processor_chain,
            technical_processor,
            quality_validator,
        ]

        super().__init__(
            document_analyzer=document_analyzer,
            simple_processor=simple_processor,
            complex_processor_chain=complex_processor_chain,
            technical_processor=technical_processor,
            quality_validator=quality_validator,
            sub_agents=sub_agents_list,
            **kwargs,
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Customized orchestration logic for smart document processing

        1. Conditional logic: Select different processing flows based on document type
        2. State management: Pass analysis results between agents
        3. Dynamic decision: Based on processing results, decide whether to validate
        """
        print(f"  📋 [{self.name}] Start smart document processing workflow")

        internal_start = create_text_event(
            ctx=ctx,
            text=f"[Internal log] Workflow started, session_id={ctx.session.id}",
        )
        yield internal_start

        # First stage: Document type analysis
        print(f"  🔍 [{self.name}] Stage 1: Analyze document type...")
        async for event in self.document_analyzer.run_async(ctx):
            yield event

        doc_type = ctx.session.state.get("doc_type", "simple").lower().strip()
        print(f"  📊 [{self.name}] Document type recognition: {doc_type}")

        # Second stage: Select processing strategy based on document type
        if doc_type == "simple":
            print(f"  ⚡ [{self.name}] Stage 2: Use simple processing flow...")
            async for event in self.simple_processor.run_async(ctx):
                yield event

        elif doc_type == "complex":
            print(f"  🧠 [{self.name}] Stage 2: Use complex document processing flow...")
            print(f"  🔗 [{self.name}] Use ChainAgent: Analyze → Process")
            async for event in self.complex_processor_chain.run_async(ctx):
                yield event

        elif doc_type == "technical":
            print(f"  🔧 [{self.name}] Stage 2: Use technical document processing flow...")
            async for event in self.technical_processor.run_async(ctx):
                yield event
        else:
            print(f"  ❓ [{self.name}] Unknown document type, use simple processing...")
            async for event in self.simple_processor.run_async(ctx):
                yield event

        # Third stage: Quality validation decision
        if doc_type in ["complex", "technical"]:
            print(f"  ✅ [{self.name}] Stage 3: Execute quality validation...")
            async for event in self.quality_validator.run_async(ctx):
                yield event

            quality_feedback = ctx.session.state.get("quality_feedback", "")
            if "Quality validation passed" in quality_feedback:
                print(f"  🎉 [{self.name}] Quality validation passed, processing completed!")
            else:
                print(f"  📝 [{self.name}] Quality validation found improvement points, provided suggestions")
        else:
            print(f"  ⏭️  [{self.name}] Simple document skipped quality validation stage")

        internal_complete = create_text_event(
            ctx=ctx,
            text=f"[Internal log] Workflow completed, document type={doc_type}",
        )
        yield internal_complete

        print(f"  ✨ [{self.name}] Smart document processing workflow completed!")


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent() -> SmartDocumentProcessor:
    """ Create the smart document processor agent"""
    return SmartDocumentProcessor(
        name="smart_document_processor",
        description="A smart document processing system that dynamically selects the optimal processing strategy based on document type",
        model=_create_model(),
    )


root_agent = create_agent()
