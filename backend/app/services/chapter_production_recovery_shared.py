"""Shared recovery-owned dataclasses and content/path helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.documents.chapter_segments import MAX_CHAPTER_CONTENT_BYTES
from app.models import Document, DocumentVersion, ReviewMode
from app.services.chapter_production_v2_contracts import (
    CONTRACT_VERSION as _CONTRACT_VERSION,
    REVIEWER_CLAIM_STATUS_CLAIMED as _ATTEMPT_STATUS_CLAIMED,
    REVIEWER_CLAIM_STATUS_FAILED as _ATTEMPT_STATUS_FAILED,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
    valid_sha256 as _valid_sha256,
)
from app.workspace.hashing import sha256_content
from app.workspace.markdown_store import MarkdownStore
from app.workspace.paths import version_snapshot_path


_AUTHOR_ACTION_TYPE = "chapter_author_revision"


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _reconciliation() -> ChapterProductionV2ReconciliationError:
    return ChapterProductionV2ReconciliationError()


@dataclass(frozen=True, slots=True)
class _AuthorContext:
    run: object
    state: object
    checkpoint: object
    action: object
    binding: object
    document: Document
    version: DocumentVersion


@dataclass(frozen=True, slots=True)
class _ReviewRevisionContext:
    run: object
    state: object
    checkpoint: object
    document: Document
    version: DocumentVersion
    segment_map: object
    reports: tuple[object, ...]


def _review_report_slots(
    *,
    editor_report_id: UUID | None,
    chief_editor_report_id: UUID | None,
    lore_report_id: UUID | None,
) -> tuple[tuple[UUID, str, str], ...]:
    slots = (
        (editor_report_id, ReviewMode.CHAPTER_EDITOR.value, "editor_agent"),
        (
            chief_editor_report_id,
            ReviewMode.CHAPTER_CHIEF_FINAL.value,
            "chief_editor_agent",
        ),
        (lore_report_id, ReviewMode.CHAPTER_FINAL_LORE.value, "lore_agent"),
    )
    return tuple((report_id, mode, role) for report_id, mode, role in slots if report_id)


def verified_snapshot_content(
    document: Document, version: DocumentVersion
) -> str:
    if (
        document.project is None
        or version.document_id != document.id
        or type(version.version_number) is not int
        or version.version_number < 1
        or version.file_path != document.path
        or version.snapshot_path
        != version_snapshot_path(str(document.id), version.version_number).as_posix()
        or type(version.byte_size) is not int
        or version.byte_size < 0
        or version.byte_size > MAX_CHAPTER_CONTENT_BYTES
        or not _valid_sha256(version.content_hash)
    ):
        raise _invalid()
    try:
        content = MarkdownStore(Path(document.project.workspace_root)).read_bounded(
            version.snapshot_path,
            max_bytes=MAX_CHAPTER_CONTENT_BYTES,
        )
    except Exception:
        raise _invalid() from None
    if (
        len(content.encode("utf-8")) != version.byte_size
        or sha256_content(content) != version.content_hash
    ):
        raise _invalid()
    return content


__all__ = [
    "_ATTEMPT_STATUS_CLAIMED",
    "_ATTEMPT_STATUS_FAILED",
    "_AUTHOR_ACTION_TYPE",
    "_CONTRACT_VERSION",
    "_AuthorContext",
    "_ReviewRevisionContext",
    "_invalid",
    "_reconciliation",
    "_review_report_slots",
    "verified_snapshot_content",
]
