# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Langfuse metrics setup."""

import os

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.server.langfuse.tracing.opentelemetry import LangfuseConfig
from trpc_agent_sdk.server.langfuse.tracing.opentelemetry import setup as langfuse_opentelemetry_setup

from ..config import ClawConfig

# LANGFUSE_PUBLIC_KEY
# LANGFUSE_SECRET_KEY
# LANGFUSE_HOST


def setup_langfuse(config: ClawConfig) -> bool:
    """Setup Langfuse tracing."""
    public_key = config.metrics.langfuse.public_key or os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = config.metrics.langfuse.secret_key or os.environ.get("LANGFUSE_SECRET_KEY")
    host = config.metrics.langfuse.host or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    if not public_key or not secret_key or not host:
        logger.warning("Langfuse tracing setup failed: public_key or secret_key or host is not set")
        return False
    try:
        logger.info("Setup Langfuse tracing...")
        langfuse_config = LangfuseConfig(public_key=public_key, secret_key=secret_key, host=host)
        langfuse_opentelemetry_setup(langfuse_config)
        logger.info(f"Langfuse tracing setup success: {host}")
        return True
    except Exception as e:
        logger.warning(f"Langfuse tracing setup failed: {e}")
        return False
