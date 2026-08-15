"""Corrective-review claim and session-free Revision-provider handoff.

The claim walk stays in the facade and is reached dynamically through the
passed service object. The provider call receives only pure values and runs
after the claim transaction is committed. The result is a frozen content-safe
replacement plan; Phase 3 persistence/finalization stays in the facade.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.agents import CandidateChapterOutput, ReviewDrivenRevisionRequest, RevisionAgent
from app.documents.chapter_segments import ChapterSegmentMap
from app.llm import ProviderInvalidOutputError, ProviderTimeoutError
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ProviderError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
)
from app.workflows.chapter_production import (
    ChapterFailureCode,
    ChapterProductionStatus,
)


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _safe_cancelled_error(_: BaseException) -> asyncio.CancelledError:
    return asyncio.CancelledError()


def _new_attempt_id() -> str:
    return str(uuid4())


def _expiry_precludes_resolution(expires_at: object, database_now: object) -> bool:
    return expires_at is not None and database_now >= expires_at


def _normalize_uuid(value: object) -> UUID:
    try:
        if isinstance(value, (str, bytes)) or not hasattr(value, "int"):
            raise _invalid()
        text = str(value)
        parsed = UUID(text)
    except (AttributeError, TypeError, ValueError):
        raise _invalid() from None
    if parsed.int == 0 or str(parsed) != text:
        raise _invalid()
    return parsed


@dataclass(frozen=True, slots=True)
class _Scope:
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    actor_user_id: UUID
    report_ids: tuple[UUID, ...]
    target_segment_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _Claim:
    source_document_id: UUID
    source_version_id: UUID
    source_content_hash: str
    operation_key: str
    attempt_id: str
    attempt_checkpoint_index: int
    report_ids: tuple[UUID, ...]
    report_input_hash: str
    target_segment_ids: tuple[UUID, ...]
    segment_map: ChapterSegmentMap = field(repr=False)
    request: ReviewDrivenRevisionRequest = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class ReviewRevisionPlan:
    """Frozen, content-safe handoff between the provider and Phase 3 persistence."""

    source_document_id: UUID
    source_version_id: UUID
    source_content_hash: str
    operation_key: str
    attempt_id: str
    attempt_checkpoint_index: int
    report_ids: tuple[UUID, ...]
    report_input_hash: str
    target_segment_ids: tuple[UUID, ...]
    segment_map: ChapterSegmentMap = field(repr=False)
    candidate: CandidateChapterOutput = field(repr=False)

    def __repr__(self) -> str:
        return (
            "ReviewRevisionPlan("
            f"source_document_id={self.source_document_id!r}, "
            f"source_version_id={self.source_version_id!r}, "
            f"source_content_hash={self.source_content_hash!r}, "
            f"operation_key={self.operation_key!r}, "
            f"attempt_id={self.attempt_id!r}, "
            f"attempt_checkpoint_index={self.attempt_checkpoint_index!r}, "
            f"report_ids={self.report_ids!r}, "
            f"report_input_hash={self.report_input_hash!r}, "
            f"target_segment_ids={self.target_segment_ids!r}"
            ")"
        )


class ReviewRevisionHandoff:
    """Claim the corrective review gate and hand off a pure provider snapshot."""

    def __init__(self, service: object, revision_agent: RevisionAgent | None) -> None:
        if revision_agent is not None and type(revision_agent) is not RevisionAgent:
            raise _invalid() from None
        self.service = service

    async def execute(
        self, *, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID,
        report_ids: tuple[UUID, ...], target_segment_ids: tuple[UUID, ...],
        actor_user_id: UUID,
    ) -> ReviewRevisionPlan:
        self.service._validated_ids(  # type: ignore[attr-defined]
            project_id, chapter_id, workflow_run_id, actor_user_id)
        project_id = _normalize_uuid(project_id)
        chapter_id = _normalize_uuid(chapter_id)
        workflow_run_id = _normalize_uuid(workflow_run_id)
        actor_user_id = _normalize_uuid(actor_user_id)
        report_ids = self.service._validated_uuid_sequence(  # type: ignore[attr-defined]
            report_ids, maximum=16)
        target_segment_ids = self.service._validated_uuid_sequence(  # type: ignore[attr-defined]
            target_segment_ids, maximum=64)
        scope = _Scope(
            project_id, chapter_id, workflow_run_id, actor_user_id,
            report_ids, target_segment_ids)
        await self.service._recover_failed_attempt(  # type: ignore[attr-defined]
            project_id=scope.project_id, chapter_id=scope.chapter_id,
            workflow_run_id=scope.workflow_run_id, actor_user_id=scope.actor_user_id,
            kind="review", report_ids=scope.report_ids,
            target_segment_ids=scope.target_segment_ids)
        claim = await self._claim(scope)
        candidate = await self._provider(scope.workflow_run_id, claim)
        return ReviewRevisionPlan(
            source_document_id=claim.source_document_id,
            source_version_id=claim.source_version_id,
            source_content_hash=claim.source_content_hash,
            operation_key=claim.operation_key,
            attempt_id=claim.attempt_id,
            attempt_checkpoint_index=claim.attempt_checkpoint_index,
            report_ids=claim.report_ids, report_input_hash=claim.report_input_hash,
            target_segment_ids=claim.target_segment_ids,
            segment_map=claim.segment_map, candidate=candidate)

    async def _claim(self, scope: _Scope) -> _Claim:
        service = self.service
        try:
            context = await service._review_revision_context(  # type: ignore[attr-defined]
                project_id=scope.project_id, chapter_id=scope.chapter_id,
                workflow_run_id=scope.workflow_run_id, report_ids=scope.report_ids,
                actor_user_id=scope.actor_user_id)
            if service.revision_agent is None:
                raise ChapterProductionV2ProviderError() from None
            request = service._review_revision_request(  # type: ignore[attr-defined]
                context=context, project_id=scope.project_id, chapter_id=scope.chapter_id,
                target_segment_ids=scope.target_segment_ids)
            report_input_hash = service._review_report_input_hash(  # type: ignore[attr-defined]
                context.reports)
            operation_key = service._review_operation_key(  # type: ignore[attr-defined]
                workflow_run_id=scope.workflow_run_id, source_version_id=context.version.id,
                report_ids=scope.report_ids, target_segment_ids=scope.target_segment_ids,
                report_input_hash=report_input_hash)
            attempt_id = _new_attempt_id()
            attempt_checkpoint_index = context.checkpoint.checkpoint_index
            metadata = service._run_metadata(context.run)  # type: ignore[attr-defined]
            if metadata["provider_attempt"] is not None:
                raise ChapterProductionV2ReconciliationError()
            service._set_attempt(  # type: ignore[attr-defined]
                context.run,
                service._attempt_payload(  # type: ignore[attr-defined]
                    attempt_id=attempt_id, key=operation_key, kind="review",
                    checkpoint_index=attempt_checkpoint_index,
                    source_document_id=context.document.id,
                    source_version_id=context.version.id,
                    target_segment_ids=scope.target_segment_ids,
                    report_ids=scope.report_ids, report_input_hash=report_input_hash))
            await service._commit()
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ProviderError:
            await service._rollback()
            raise
        except ChapterProductionV2ReconciliationError:
            await service._rollback()
            raise
        except ChapterProductionV2ValidationError:
            await service._rollback()
            raise
        except Exception:
            await service._rollback()
            raise _invalid() from None
        return _Claim(
            source_document_id=context.document.id,
            source_version_id=context.version.id,
            source_content_hash=context.version.content_hash,
            operation_key=operation_key,
            attempt_id=attempt_id,
            attempt_checkpoint_index=attempt_checkpoint_index,
            report_ids=scope.report_ids,
            report_input_hash=report_input_hash,
            target_segment_ids=scope.target_segment_ids,
            segment_map=context.segment_map,
            request=request)

    async def _provider(self, workflow_run_id: UUID, claim: _Claim) -> CandidateChapterOutput:
        cancellation: asyncio.CancelledError | None = None
        provider_failure: ChapterFailureCode | None = None
        try:
            return await self.service.revision_agent.review_driven_revision(claim.request)
        except asyncio.CancelledError as error:
            cancellation = _safe_cancelled_error(error)
        except ProviderTimeoutError:
            provider_failure = ChapterFailureCode.PROVIDER_TIMEOUT
        except ProviderInvalidOutputError:
            provider_failure = ChapterFailureCode.INVALID_PROVIDER_OUTPUT
        except Exception:
            provider_failure = ChapterFailureCode.PROVIDER_UNAVAILABLE
        if cancellation is not None:
            await self.service._release_attempt(  # type: ignore[attr-defined]
                workflow_run_id,
                expected_key=claim.operation_key,
                expected_attempt_id=claim.attempt_id,
                expected_kind="review",
                expected_checkpoint_index=claim.attempt_checkpoint_index)
            raise cancellation from None
        await self.service._fail_provider(  # type: ignore[attr-defined]
            workflow_run_id,
            provider_failure,
            expected_status=ChapterProductionStatus.REVIEW_REVISION,
            expected_checkpoint_index=claim.attempt_checkpoint_index,
            expected_attempt_key=claim.operation_key,
            expected_attempt_id=claim.attempt_id)
        raise ChapterProductionV2ProviderError() from None


__all__ = ["ReviewRevisionHandoff", "ReviewRevisionPlan"]
