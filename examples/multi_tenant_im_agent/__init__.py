"""Production-oriented multi-tenant IM gateway example for tRPC-Agent."""

from .domain import AgentReply, ChannelResponse, InboundMessage, TenantConfig
from .service import MultiTenantAgentService

__all__ = [
    "AgentReply",
    "ChannelResponse",
    "InboundMessage",
    "MultiTenantAgentService",
    "TenantConfig",
]
