"""Create the multi-tenant IM control-plane schema.

Revision ID: 20260910_0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mt_tenants",
        sa.Column("tenant_id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "mt_agent_apps",
        sa.Column("agent_app_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("mt_tenants.tenant_id"),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(128), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("tool_allowlist_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt_agent_apps_tenant_id", "mt_agent_apps", ["tenant_id"])
    op.create_table(
        "mt_channel_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("mt_tenants.tenant_id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("secret_ref", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.UniqueConstraint("channel", "account_id", name="uq_mt_channel_account"),
    )
    op.create_index(
        "ix_mt_channel_bindings_tenant_id", "mt_channel_bindings", ["tenant_id"]
    )
    op.create_table(
        "mt_sessions",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("mt_tenants.tenant_id"),
            nullable=False,
        ),
        sa.Column(
            "agent_app_id",
            sa.String(64),
            sa.ForeignKey("mt_agent_apps.agent_app_id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("conversation_hash", sa.String(64), nullable=False),
        sa.Column("user_hash", sa.String(64), nullable=False),
        sa.Column("last_event_seq", sa.Integer(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt_sessions_tenant_id", "mt_sessions", ["tenant_id"])
    op.create_index("ix_mt_sessions_agent_app_id", "mt_sessions", ["agent_app_id"])
    op.create_table(
        "mt_message_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("mt_tenants.tenant_id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("external_message_id", sa.String(192), nullable=False),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("mt_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("content_redacted", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "channel",
            "external_message_id",
            name="uq_mt_inbound_idempotency",
        ),
        sa.UniqueConstraint(
            "session_id",
            "sequence",
            "direction",
            name="uq_mt_session_sequence_direction",
        ),
    )
    op.create_index(
        "ix_mt_message_events_tenant_id", "mt_message_events", ["tenant_id"]
    )
    op.create_index(
        "ix_mt_message_events_session_id", "mt_message_events", ["session_id"]
    )
    op.create_table(
        "mt_memories",
        sa.Column("memory_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("mt_tenants.tenant_id"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("mt_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content_ref", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt_memories_tenant_id", "mt_memories", ["tenant_id"])
    op.create_index("ix_mt_memories_session_id", "mt_memories", ["session_id"])
    op.create_table(
        "mt_summaries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("mt_tenants.tenant_id"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("mt_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("through_sequence", sa.Integer(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "session_id", "through_sequence", name="uq_mt_summary_version"
        ),
    )
    op.create_index("ix_mt_summaries_tenant_id", "mt_summaries", ["tenant_id"])
    op.create_index("ix_mt_summaries_session_id", "mt_summaries", ["session_id"])
    op.create_table(
        "mt_artifacts",
        sa.Column("artifact_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("mt_tenants.tenant_id"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(64),
            sa.ForeignKey("mt_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("object_uri", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt_artifacts_tenant_id", "mt_artifacts", ["tenant_id"])
    op.create_index("ix_mt_artifacts_session_id", "mt_artifacts", ["session_id"])
    op.create_table(
        "mt_knowledge",
        sa.Column("knowledge_id", sa.String(64), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("mt_tenants.tenant_id"),
            nullable=False,
        ),
        sa.Column("vector_namespace", sa.String(192), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt_knowledge_tenant_id", "mt_knowledge", ["tenant_id"])
    op.create_table(
        "mt_audit_logs",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("agent_name", sa.String(128), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(128), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt_audit_logs_tenant_id", "mt_audit_logs", ["tenant_id"])
    op.create_index("ix_mt_audit_logs_session_id", "mt_audit_logs", ["session_id"])
    op.create_index("ix_mt_audit_logs_created_at", "mt_audit_logs", ["created_at"])
    op.create_table(
        "mt_session_leases",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "mt_outbox",
        sa.Column("outbox_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt_outbox_tenant_id", "mt_outbox", ["tenant_id"])
    op.create_index("ix_mt_outbox_session_id", "mt_outbox", ["session_id"])
    op.create_index("ix_mt_outbox_status", "mt_outbox", ["status"])


def downgrade() -> None:
    op.drop_table("mt_outbox")
    op.drop_table("mt_session_leases")
    op.drop_table("mt_audit_logs")
    op.drop_table("mt_knowledge")
    op.drop_table("mt_artifacts")
    op.drop_table("mt_summaries")
    op.drop_table("mt_memories")
    op.drop_table("mt_message_events")
    op.drop_table("mt_sessions")
    op.drop_table("mt_channel_bindings")
    op.drop_table("mt_agent_apps")
    op.drop_table("mt_tenants")
