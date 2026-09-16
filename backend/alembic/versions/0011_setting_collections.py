"""Separate reusable setting collections from novels and backfill existing data.

Revision ID: 0011_setting_collections
Revises: 0010_studio_assistant
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_setting_collections"
down_revision: str | Sequence[str] | None = "0010_studio_assistant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "setting_collections",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "owner_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("workspace_root", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_setting_collections"),
        sa.UniqueConstraint("slug", name="uq_setting_collections_slug"),
    )
    op.create_index("idx_setting_collections_owner_id", "setting_collections", ["owner_id"])
    op.create_index("idx_setting_collections_status", "setting_collections", ["status"])
    op.create_index(
        "idx_setting_collections_metadata_gin",
        "setting_collections",
        ["metadata"],
        postgresql_using="gin",
    )

    op.add_column(
        "projects",
        sa.Column(
            "setting_collection_id",
            sa.UUID(),
            sa.ForeignKey(
                "setting_collections.id",
                ondelete="RESTRICT",
                name="fk_projects_setting_collection_id",
            ),
            nullable=True,
        ),
    )
    op.create_index("idx_projects_setting_collection_id", "projects", ["setting_collection_id"])

    op.add_column(
        "documents",
        sa.Column(
            "setting_collection_id",
            sa.UUID(),
            sa.ForeignKey(
                "setting_collections.id",
                ondelete="CASCADE",
                name="fk_documents_setting_collection_id",
            ),
            nullable=True,
        ),
    )
    op.alter_column("documents", "project_id", existing_type=sa.UUID(), nullable=True)
    op.drop_constraint("uq_document_project_path", "documents", type_="unique")
    op.create_index(
        "uq_documents_project_path",
        "documents",
        ["project_id", "path"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index(
        "uq_documents_setting_collection_path",
        "documents",
        ["setting_collection_id", "path"],
        unique=True,
        postgresql_where=sa.text("setting_collection_id IS NOT NULL"),
    )
    op.create_index(
        "idx_documents_setting_collection_id",
        "documents",
        ["setting_collection_id"],
    )
    op.create_check_constraint(
        "ck_documents_owner_xor",
        "documents",
        "((project_id IS NOT NULL AND setting_collection_id IS NULL) OR (project_id IS NULL AND setting_collection_id IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_documents_chapter_requires_project",
        "documents",
        "(chapter_id IS NULL OR project_id IS NOT NULL)",
    )

    # Data Backfill for existing projects and documents
    op.execute(
        sa.text(
            """
            INSERT INTO setting_collections (
                id, owner_id, slug, title, description, status, workspace_root, revision, metadata, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                p.owner_id,
                p.slug,
                p.title,
                NULL,
                'active',
                p.workspace_root,
                1,
                jsonb_build_object('migrated_from_project_id', p.id::text),
                p.created_at,
                p.updated_at
            FROM projects p
            WHERE p.setting_collection_id IS NULL;
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE projects p
            SET setting_collection_id = sc.id
            FROM setting_collections sc
            WHERE sc.metadata->>'migrated_from_project_id' = p.id::text
              AND p.setting_collection_id IS NULL;
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TEMPORARY TABLE _migrated_setting_docs AS
            SELECT
                d.id AS old_doc_id,
                p.setting_collection_id AS sc_id,
                d.type AS doc_type,
                d.title AS doc_title,
                d.path AS doc_path,
                d.metadata AS doc_meta,
                d.created_at AS doc_created_at,
                d.updated_at AS doc_updated_at,
                v.id AS old_ver_id,
                v.source AS ver_source,
                v.actor_user_id AS ver_actor_user_id,
                v.agent_role AS ver_agent_role,
                v.workflow_run_id AS ver_workflow_run_id,
                v.content_hash AS ver_content_hash,
                v.byte_size AS ver_byte_size,
                v.word_count AS ver_word_count,
                v.file_path AS ver_file_path,
                v.snapshot_path AS ver_snapshot_path,
                v.change_summary AS ver_change_summary,
                v.metadata AS ver_meta,
                gen_random_uuid() AS new_doc_id,
                gen_random_uuid() AS new_ver_id
            FROM documents d
            JOIN projects p ON d.project_id = p.id
            JOIN document_versions v ON d.current_version_id = v.id
            WHERE d.project_id IS NOT NULL
              AND d.type IN ('world_overview', 'power_system', 'factions', 'geography', 'history', 'character_profile', 'glossary');
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO documents (
                id, setting_collection_id, project_id, chapter_id, type, title, path,
                current_version_id, metadata, created_at, updated_at
            )
            SELECT
                new_doc_id,
                sc_id,
                NULL,
                NULL,
                doc_type,
                doc_title,
                doc_path,
                NULL,
                doc_meta || jsonb_build_object(
                    'migrated_from_document_id', old_doc_id::text,
                    'migrated_from_version_id', old_ver_id::text
                ),
                doc_created_at,
                doc_updated_at
            FROM _migrated_setting_docs;
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO document_versions (
                id, document_id, version_number, parent_version_id, source, actor_user_id,
                agent_role, workflow_run_id, content_hash, byte_size, word_count, file_path,
                snapshot_path, change_summary, metadata, created_at
            )
            SELECT
                new_ver_id,
                new_doc_id,
                1,
                NULL,
                ver_source,
                ver_actor_user_id,
                ver_agent_role,
                ver_workflow_run_id,
                ver_content_hash,
                ver_byte_size,
                ver_word_count,
                ver_file_path,
                ver_snapshot_path,
                COALESCE(ver_change_summary, 'Migrated setting document v1'),
                ver_meta || jsonb_build_object(
                    'migrated_from_document_id', old_doc_id::text,
                    'migrated_from_version_id', old_ver_id::text
                ),
                doc_updated_at
            FROM _migrated_setting_docs;
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE documents d
            SET current_version_id = m.new_ver_id
            FROM _migrated_setting_docs m
            WHERE d.id = m.new_doc_id;
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE documents d
            SET metadata = d.metadata || '{"legacy_setting_context": true}'::jsonb
            FROM _migrated_setting_docs m
            WHERE d.id = m.old_doc_id;
            """
        )
    )

    op.execute(sa.text("DROP TABLE IF EXISTS _migrated_setting_docs;"))

    op.alter_column("projects", "setting_collection_id", existing_type=sa.UUID(), nullable=False)


def downgrade() -> None:
    op.alter_column("projects", "setting_collection_id", existing_type=sa.UUID(), nullable=True)

    op.execute(sa.text("DELETE FROM documents WHERE setting_collection_id IS NOT NULL;"))
    op.execute(
        sa.text(
            """
            UPDATE documents
            SET metadata = metadata - 'legacy_setting_context'
            WHERE metadata ? 'legacy_setting_context';
            """
        )
    )

    op.drop_constraint("ck_documents_chapter_requires_project", "documents", type_="check")
    op.drop_constraint("ck_documents_owner_xor", "documents", type_="check")
    op.drop_index("idx_documents_setting_collection_id", "documents")
    op.drop_index("uq_documents_setting_collection_path", "documents")
    op.drop_index("uq_documents_project_path", "documents")
    op.create_unique_constraint("uq_document_project_path", "documents", ["project_id", "path"])
    op.alter_column("documents", "project_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("documents", "setting_collection_id")

    op.drop_index("idx_projects_setting_collection_id", "projects")
    op.drop_column("projects", "setting_collection_id")

    op.drop_index("idx_setting_collections_metadata_gin", "setting_collections")
    op.drop_index("idx_setting_collections_status", "setting_collections")
    op.drop_index("idx_setting_collections_owner_id", "setting_collections")
    op.drop_table("setting_collections")
