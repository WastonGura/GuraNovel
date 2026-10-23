"""Preserve stable legacy defaults and allow explicit setting Agent sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0021_setting_agent_sessions"
down_revision = "0020_setting_agent_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("setting_collaborations", sa.Column("title", sa.Text(), nullable=False, server_default="新对话"))
    op.add_column("setting_collaborations", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("setting_collaborations", sa.Column("client_request_id", sa.UUID()))
    op.execute(sa.text("UPDATE setting_collaborations SET is_default = true WHERE document_id IS NULL"))
    op.execute(sa.text("""
        UPDATE setting_collaborations AS conversation SET title = COALESCE(
            (SELECT NULLIF(left(btrim(regexp_replace(message.content, '\\s+', ' ', 'g')), 48), '')
             FROM setting_collaboration_messages AS message
             WHERE message.conversation_id = conversation.id AND message.role = 'user'
             ORDER BY message.created_at, message.id LIMIT 1),
            (SELECT NULLIF(left(btrim(regexp_replace(attempt.request->>'content', '\\s+', ' ', 'g')), 48), '')
             FROM setting_collaboration_attempts AS attempt
             WHERE attempt.conversation_id = conversation.id
             ORDER BY attempt.created_at, attempt.id LIMIT 1),
            '新对话'
        )
    """))
    op.drop_index("uq_setting_collaboration_collection", table_name="setting_collaborations")
    op.create_index("uq_setting_collaboration_collection", "setting_collaborations", ["setting_collection_id"], unique=True,
                    postgresql_where=sa.text("document_id IS NULL AND is_default"))
    op.create_index("idx_setting_collaboration_collection_updated", "setting_collaborations", ["setting_collection_id", "updated_at", "id"],
                    postgresql_where=sa.text("document_id IS NULL"))
    op.create_unique_constraint("uq_setting_collaboration_creation", "setting_collaborations", ["setting_collection_id", "client_request_id"])
    op.create_check_constraint("ck_setting_collaboration_default_scope", "setting_collaborations", "NOT is_default OR document_id IS NULL")
    op.create_check_constraint("ck_setting_collaboration_request_scope", "setting_collaborations", "client_request_id IS NULL OR document_id IS NULL")
    op.create_check_constraint("ck_setting_collaboration_title", "setting_collaborations", "char_length(title) BETWEEN 1 AND 80")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("""
        SELECT EXISTS (SELECT 1 FROM setting_collaborations WHERE client_request_id IS NOT NULL)
            OR EXISTS (SELECT 1 FROM setting_collaborations WHERE document_id IS NULL
                       GROUP BY setting_collection_id HAVING count(*) > 1)
    """)):
        raise RuntimeError("Export setting Agent sessions before downgrade; refusing to discard conversations or creation identities.")
    op.drop_constraint("ck_setting_collaboration_title", "setting_collaborations", type_="check")
    op.drop_constraint("ck_setting_collaboration_request_scope", "setting_collaborations", type_="check")
    op.drop_constraint("ck_setting_collaboration_default_scope", "setting_collaborations", type_="check")
    op.drop_constraint("uq_setting_collaboration_creation", "setting_collaborations", type_="unique")
    op.drop_index("idx_setting_collaboration_collection_updated", table_name="setting_collaborations")
    op.drop_index("uq_setting_collaboration_collection", table_name="setting_collaborations")
    op.create_index("uq_setting_collaboration_collection", "setting_collaborations", ["setting_collection_id"], unique=True,
                    postgresql_where=sa.text("document_id IS NULL"))
    op.drop_column("setting_collaborations", "client_request_id")
    op.drop_column("setting_collaborations", "is_default")
    op.drop_column("setting_collaborations", "title")
