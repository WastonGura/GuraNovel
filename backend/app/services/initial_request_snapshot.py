"""Exact, warning-free clone of one initial draft request."""
from __future__ import annotations
from uuid import UUID

from app.agents.chapter_writer_contracts import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    InitialDraftRequest,
)
from app.services.chapter_production_v2_contracts import ChapterProductionV2ValidationError


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _valid_uuid(value: object) -> bool:
    if type(value) is not UUID:
        return False
    integer = value.int
    return type(integer) is int and 0 < integer < 2**128


def _copy_uuid(value: UUID) -> UUID:
    return UUID(int=value.int)


def _exact_source(value: InitialDraftRequest) -> bool:
    outline = value.approved_outline
    if (
        type(outline) is not ApprovedOutlineReference
        or type(value.allowed_segments) is not tuple
        or not all(
            _valid_uuid(item)
            for item in (
                value.project_id,
                value.chapter_id,
                value.workflow_run_id,
                outline.project_id,
                outline.chapter_id,
                outline.document_id,
                outline.version_id,
            )
        )
    ):
        return False
    return all(
        type(segment) is AllowedChapterSegment
        and _valid_uuid(segment.segment_id)
        and type(segment.index) is int
        and type(segment.title) is str
        and type(segment.brief) is str
        for segment in value.allowed_segments
    )


def validate_initial_request_snapshot(value: object) -> InitialDraftRequest:
    failed = False
    try:
        if type(value) is not InitialDraftRequest or not _exact_source(value):
            raise _invalid()
        outline = value.approved_outline
        result = InitialDraftRequest(
            project_id=_copy_uuid(value.project_id),
            chapter_id=_copy_uuid(value.chapter_id),
            workflow_run_id=_copy_uuid(value.workflow_run_id),
            approved_outline=ApprovedOutlineReference(
                project_id=_copy_uuid(outline.project_id),
                chapter_id=_copy_uuid(outline.chapter_id),
                document_id=_copy_uuid(outline.document_id),
                version_id=_copy_uuid(outline.version_id),
            ),
            allowed_segments=tuple(
                AllowedChapterSegment(
                    segment_id=_copy_uuid(segment.segment_id),
                    index=segment.index,
                    title=segment.title,
                    brief=segment.brief,
                )
                for segment in value.allowed_segments
            ),
        )
    except BaseException:
        failed = True
    if failed:
        raise _invalid() from None
    return result


__all__ = ["validate_initial_request_snapshot"]
