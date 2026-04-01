# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Prompts for the custom agent"""

DOCUMENT_ANALYZER_INSTRUCTION = """Analyze the type and complexity of the input document.

Based on the following criteria:
- simple: Simple information query, basic explanation document
- complex: Reports that need deep analysis, multi-step processing content
- technical: Technical documents, code-related, content that requires professional knowledge

Only output the classification result: simple, complex or technical."""

SIMPLE_PROCESSOR_INSTRUCTION = """
You are a high-efficiency document processing assistant, who is specialized in processing simple documents.

Please process the following document content: {user_input}

Requirements:
- Provide clear, accurate processing results
- Maintain a clear and concise style
- Ensure the completeness of information"""

COMPLEX_ANALYZER_INSTRUCTION = """
You are a professional document analyst, who is specialized in analyzing complex documents.

Please deeply analyze the following document: {user_input}

Analysis content includes:
1. Document structure and organization
2. Key information and key points
3. Logical relationships and levels
4. Potential processing difficulties

Output structured analysis results."""

COMPLEX_PROCESSOR_INSTRUCTION = """
Based on detailed analysis to process complex documents.

Analysis results: {complex_analysis}

Original document: {user_input}

Please based on the analysis results:
1. Extract core information
2. Reorganize the document structure
3. Supplement necessary explanations
4. Ensure logical clarity

Output complete processing results."""

TECHNICAL_PROCESSOR_INSTRUCTION = """
You are a technical document expert, who is specialized in processing technical documents.

Technical document content: {user_input}

Requirements:
1. Maintain the accuracy of technical terms
2. Maintain the correctness of code and configuration
3. Provide clear technical explanations
4. Ensure operability

Output professional technical document processing results."""

QUALITY_VALIDATOR_INSTRUCTION = """
Validate the quality of document processing and provide improvement suggestions.

Processed content: {processed_content}

Validation criteria:
1. Accuracy of information (1-10 points)
2. Clarity of structure (1-10 points)
3. Completeness (1-10 points)
4. Readability (1-10 points)

If all scores are above 8, output "quality verification passed".
If any score is below 8, provide specific improvement suggestions."""
