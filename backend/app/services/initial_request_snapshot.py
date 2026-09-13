"""Exact, warning-free clone of one initial draft request."""
from __future__ import annotations
from uuid import UUID

from app.agents.chapter_writer_contracts import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    InitialDraftRequest,
    WriterContextKind,
    WriterContextSnapshot,
)
from app.services.chapter_production_v2_contracts import ChapterProductionV2ValidationError


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _valid_uuid(value: object) -> bool:
    return type(value) is UUID and type(value.int) is int and 0 < value.int < 2**128


def _copy_uuid(value: UUID) -> UUID:
    return UUID(int=value.int)


def _exact_source(v: InitialDraftRequest) -> bool:
    o = v.approved_outline
    if type(o) is not ApprovedOutlineReference or type(v.allowed_segments) is not tuple or type(v.contexts) is not tuple:
        return False
    if type(o.content) is not str or not all(_valid_uuid(u) for u in (v.project_id, v.chapter_id, v.workflow_run_id, o.project_id, o.chapter_id, o.document_id, o.version_id)):
        return False
    if not all(type(s) is AllowedChapterSegment and _valid_uuid(s.segment_id) and type(s.index) is int and type(s.title) is str and type(s.brief) is str for s in v.allowed_segments):
        return False
    return all(type(c) is WriterContextSnapshot and _valid_uuid(c.project_id) and _valid_uuid(c.document_id) and _valid_uuid(c.version_id) and type(c.kind) is WriterContextKind and type(c.content) is str for c in v.contexts)


def validate_initial_request_snapshot(value: object) -> InitialDraftRequest:
    failed = False
    try:
        if type(value) is not InitialDraftRequest or not _exact_source(value):
            raise _invalid()
        o = value.approved_outline
        result = InitialDraftRequest(
            project_id=_copy_uuid(value.project_id),
            chapter_id=_copy_uuid(value.chapter_id),
            workflow_run_id=_copy_uuid(value.workflow_run_id),
            approved_outline=ApprovedOutlineReference(
                project_id=_copy_uuid(o.project_id), chapter_id=_copy_uuid(o.chapter_id),
                document_id=_copy_uuid(o.document_id), version_id=_copy_uuid(o.version_id),
                content=o.content,
            ),
            allowed_segments=tuple(
                AllowedChapterSegment(segment_id=_copy_uuid(s.segment_id), index=s.index, title=s.title, brief=s.brief)
                for s in value.allowed_segments
            ),
            contexts=tuple(
                WriterContextSnapshot(project_id=_copy_uuid(c.project_id), document_id=_copy_uuid(c.document_id), version_id=_copy_uuid(c.version_id), kind=c.kind, content=c.content)
                for c in value.contexts
            ),
        )
    except BaseException:
        failed = True
    if failed:
        raise _invalid() from None
    return result


__all__ = ["validate_initial_request_snapshot"]
