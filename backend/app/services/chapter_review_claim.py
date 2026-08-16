"""Reviewer-claim and immutable request-snapshot coordination for Chapter Production V2."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents import (
    ApprovedOutlineSnapshot,
    ChapterReviewTarget,
    ChiefEditorChapterFinalRequest,
    EditorReviewRequest,
    LoreChapterFinalRequest,
    ReviewContextKind,
    ReviewContextSnapshot,
    ReviewSegmentSnapshot,
)
from app.documents.chapter_segments import ChapterSegmentMap
from app.models import (
    Chapter,
    Document,
    DocumentType,
    DocumentVersion,
    WorkflowCheckpoint,
    WorkflowRun,
)
from app.services.chapter_production_v2_contracts import (
    CONTRACT_VERSION,
    REVIEWER_CLAIM_STATUS_CLAIMED,
    REVIEWER_CLAIM_STATUS_FAILED,
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
    new_attempt_id,
)
from app.services.chapter_review_protocols import ChapterReviewService
from app.workflows.chapter_production import (
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterReviewStage,
)
from app.workspace.hashing import sha256_content


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


_REVIEW_STATUSES = {
    ChapterProductionStatus.EDITOR_REVIEW,
    ChapterProductionStatus.CHIEF_FINAL_REVIEW,
    ChapterProductionStatus.LORE_FINAL_REVIEW,
}


async def _recover_failed_review_claim(
    service: ChapterReviewService,
    *,
    run: WorkflowRun,
    state: ChapterProductionState,
    checkpoint: WorkflowCheckpoint,
    claim: object,
) -> None:
    if (
        state.failed_from_status not in _REVIEW_STATUSES
        or state.failure_code
        not in {
            ChapterFailureCode.PROVIDER_UNAVAILABLE,
            ChapterFailureCode.PROVIDER_TIMEOUT,
            ChapterFailureCode.INVALID_PROVIDER_OUTPUT,
        }
        or type(claim) is not dict
        or claim.get("status") != REVIEWER_CLAIM_STATUS_FAILED
    ):
        raise ChapterProductionV2ReconciliationError()
    recovered = state.recover()
    set_reviewer_claim(run, None)
    service._append_state(run, checkpoint, recovered)
    await service._commit()


@dataclass(frozen=True, slots=True)
class ReviewClaimContext:
    run: WorkflowRun
    state: ChapterProductionState
    checkpoint: WorkflowCheckpoint
    document: Document
    version: DocumentVersion
    segment_map: ChapterSegmentMap
    request: EditorReviewRequest | ChiefEditorChapterFinalRequest | LoreChapterFinalRequest
    stage: ChapterReviewStage
    request_hash: str
    operation_key: str


def _review_stage_for_status(status: ChapterProductionStatus) -> ChapterReviewStage | None:
    return {
        ChapterProductionStatus.EDITOR_REVIEW: ChapterReviewStage.EDITOR,
        ChapterProductionStatus.CHIEF_FINAL_REVIEW: ChapterReviewStage.CHIEF_EDITOR,
        ChapterProductionStatus.LORE_FINAL_REVIEW: ChapterReviewStage.LORE,
    }.get(status)


def _validate_policy_metadata(
    metadata: dict[str, str],
    state: ChapterProductionState,
    stage: ChapterReviewStage,
) -> None:
    if (
        state.review_policy_version != metadata["review_policy_version"]
        or state.chief_editor_required is not metadata["chief_editor_required"]
        or (stage is ChapterReviewStage.CHIEF_EDITOR and not state.chief_editor_required)
    ):
        raise _invalid()


def _build_review_request(
    *,
    project_id: UUID,
    chapter_id: UUID,
    run: WorkflowRun,
    document: Document,
    version: DocumentVersion,
    segment_map: ChapterSegmentMap,
    outline: Document,
    outline_version: DocumentVersion,
    outline_content: str,
    stage: ChapterReviewStage,
    contexts: tuple[ReviewContextSnapshot, ...],
    review_policy_version: str,
    checkpoint_index: int,
) -> tuple[
    EditorReviewRequest | ChiefEditorChapterFinalRequest | LoreChapterFinalRequest,
    str,
    str,
]:
    target = ChapterReviewTarget(
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=document.id,
        version_id=version.id,
        segments=tuple(
            ReviewSegmentSnapshot(
                segment_id=item.segment_id,
                index=item.ordinal,
                title=item.structural_path,
                content=item.content,
            )
            for item in segment_map.segments
        ),
    )
    outline_snapshot = ApprovedOutlineSnapshot(
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=outline.id,
        version_id=outline_version.id,
        content=outline_content.strip(),
    )
    request_type = {
        ChapterReviewStage.EDITOR: EditorReviewRequest,
        ChapterReviewStage.CHIEF_EDITOR: ChiefEditorChapterFinalRequest,
        ChapterReviewStage.LORE: LoreChapterFinalRequest,
    }[stage]
    request = request_type(
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_run_id=run.id,
        target=target,
        approved_outline=outline_snapshot,
        contexts=contexts,
    )
    request_hash = sha256_content(
        json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    operation_key = sha256_content(
        ":".join(
            (
                CONTRACT_VERSION,
                str(run.id),
                str(version.id),
                review_policy_version,
                stage.value,
                str(checkpoint_index),
                segment_map.map_hash,
                request_hash,
            )
        )
    )
    return request, request_hash, operation_key


def set_reviewer_claim(
    run: WorkflowRun, claim: dict[str, object] | None
) -> None:
    run.metadata_ = {**run.metadata_, "reviewer_claim": claim}


async def claim_current_review(
    service: ChapterReviewService,
    *,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    actor_user_id: UUID,
) -> ReviewClaimContext:
    while True:
        retry_recovered = False
        try:
            await service._require_project_owner(project_id, actor_user_id)
            chapter = await service._chapter(project_id, chapter_id, lock=True)
            run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await service._locked_state(run)
            claim = service._run_metadata(run)["reviewer_claim"]
            if state.status is ChapterProductionStatus.FAILED:
                await _recover_failed_review_claim(
                    service,
                    run=run,
                    state=state,
                    checkpoint=checkpoint,
                    claim=claim,
                )
                retry_recovered = True
            elif state.status not in _REVIEW_STATUSES or state.awaiting_user:
                raise _invalid()
            elif claim is not None:
                raise ChapterProductionV2ReconciliationError()
            if not retry_recovered:
                context = await build_review_context_locked(
                    service=service,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    chapter=chapter,
                    run=run,
                    state=state,
                    checkpoint=checkpoint,
                )
                if await service._exact_review_report_count(
                    run=run,
                    version=context.version,
                    stage=context.stage,
                ):
                    raise ChapterProductionV2ReconciliationError()
                claim_id = new_attempt_id()
                set_reviewer_claim(
                    run,
                    {
                        "claim_id": claim_id,
                        "operation_key": context.operation_key,
                        "stage": context.stage.value,
                        "checkpoint_index": checkpoint.checkpoint_index,
                        "document_id": str(context.document.id),
                        "document_version_id": str(context.version.id),
                        "content_hash": context.version.content_hash,
                        "review_policy_version": state.review_policy_version,
                        "segment_map_hash": context.segment_map.map_hash,
                        "request_hash": context.request_hash,
                        "status": REVIEWER_CLAIM_STATUS_CLAIMED,
                    },
                )
                await service._commit()
                return context
        except ChapterProductionV2CommitIndeterminateError:
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
        if retry_recovered:
            continue
        raise _invalid() from None


async def build_review_context_locked(
    service: ChapterReviewService,
    *,
    project_id: UUID,
    chapter_id: UUID,
    chapter: Chapter,
    run: WorkflowRun,
    state: ChapterProductionState,
    checkpoint: WorkflowCheckpoint,
) -> ReviewClaimContext:
    stage = _review_stage_for_status(state.status)
    if stage is None:
        raise _invalid()
    metadata = service._run_metadata(run)
    _validate_policy_metadata(metadata, state, stage)
    document, version = await service._locked_review_document(
        project_id=project_id,
        chapter_id=chapter_id,
        state=state,
        chapter=chapter,
    )
    segment_map = await service.documents.derive_chapter_segment_map(
        project_id=project_id,
        chapter_id=chapter_id,
        document_id=document.id,
        version_id=version.id,
    )
    if len(segment_map.segments) > 64 or segment_map.map_hash != segment_map.map_hash.lower():
        raise _invalid()
    outline, outline_version = await service._outline_for_chapter(
        chapter, project_id, lock=True
    )
    service._validate_outline_metadata(metadata, outline, outline_version)
    outline_content = service._verified_snapshot_content(outline, outline_version)
    contexts = await review_context_snapshots(service, project_id=project_id, stage=stage)
    request, request_hash, operation_key = _build_review_request(
        project_id=project_id,
        chapter_id=chapter_id,
        run=run,
        document=document,
        version=version,
        segment_map=segment_map,
        outline=outline,
        outline_version=outline_version,
        outline_content=outline_content,
        stage=stage,
        contexts=contexts,
        review_policy_version=state.review_policy_version,
        checkpoint_index=checkpoint.checkpoint_index,
    )
    return ReviewClaimContext(
        run,
        state,
        checkpoint,
        document,
        version,
        segment_map,
        request,
        stage,
        request_hash,
        operation_key,
    )


async def review_context_snapshots(
    service: ChapterReviewService, *, project_id: UUID, stage: ChapterReviewStage
) -> tuple[ReviewContextSnapshot, ...]:
    if stage in {ChapterReviewStage.EDITOR, ChapterReviewStage.CHIEF_EDITOR}:
        allowed_types = {
            DocumentType.STYLE_GUIDE.value: ReviewContextKind.STYLE_GUIDE,
            DocumentType.CHAPTER_SUMMARY.value: ReviewContextKind.PREVIOUS_CHAPTER_SUMMARY,
        }
    else:
        allowed_types = {
            DocumentType.WORLD_OVERVIEW.value: ReviewContextKind.LORE_BOUNDARY,
            DocumentType.POWER_SYSTEM.value: ReviewContextKind.LORE_BOUNDARY,
            DocumentType.FACTIONS.value: ReviewContextKind.LORE_BOUNDARY,
            DocumentType.GEOGRAPHY.value: ReviewContextKind.LORE_BOUNDARY,
            DocumentType.HISTORY.value: ReviewContextKind.TIMELINE,
            DocumentType.CHARACTER_PROFILE.value: ReviewContextKind.CHARACTER_STATE,
            DocumentType.MAIN_CAST.value: ReviewContextKind.CHARACTER_STATE,
            DocumentType.FORESHADOWING.value: ReviewContextKind.FORESHADOWING,
            DocumentType.UNRESOLVED_THREADS.value: ReviewContextKind.FORESHADOWING,
        }
    documents = list(
        await service.session.scalars(
            select(Document)
            .options(selectinload(Document.project))
            .execution_options(populate_existing=True)
            .where(
                Document.project_id == project_id,
                Document.type.in_(tuple(allowed_types)),
                Document.current_version_id.is_not(None),
            )
            .order_by(Document.type, Document.path, Document.id)
            .limit(16)
            .with_for_update()
        )
    )
    snapshots: list[ReviewContextSnapshot] = []
    for document in documents:
        version_id = document.current_version_id
        if version_id is None:
            raise _invalid()
        version = await service.session.scalar(
            select(DocumentVersion)
            .execution_options(populate_existing=True)
            .where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document.id,
            )
            .with_for_update()
        )
        if version is None:
            raise _invalid()
        content = service._verified_snapshot_content(document, version)
        snapshots.append(
            ReviewContextSnapshot(
                project_id=project_id,
                document_id=document.id,
                version_id=version.id,
                kind=allowed_types[document.type],
                content=content.strip(),
            )
        )
    if not snapshots:
        raise _invalid()
    return tuple(snapshots)


async def release_reviewer_claim(
    service: ChapterReviewService,
    workflow_run_id: UUID,
    *,
    expected_operation_key: str,
    expected_claim_id: str,
) -> None:
    try:
        projection = await service.session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
        )
        if (
            projection is None
            or projection.project_id is None
            or projection.chapter_id is None
        ):
            await service._rollback()
            return
        await service._chapter(projection.project_id, projection.chapter_id, lock=True)
        run = await service._run(
            projection.project_id,
            projection.chapter_id,
            workflow_run_id,
            lock=True,
        )
        claim = service._run_metadata(run)["reviewer_claim"]
        if (
            type(claim) is dict
            and claim.get("operation_key") == expected_operation_key
            and claim.get("claim_id") == expected_claim_id
            and claim.get("status") == REVIEWER_CLAIM_STATUS_CLAIMED
        ):
            set_reviewer_claim(run, None)
            await service._commit()
        else:
            await service._rollback()
    except BaseException:
        await service._rollback()


async def fail_reviewer(
    service: ChapterReviewService,
    *,
    project_id: UUID,
    chapter_id: UUID,
    workflow_run_id: UUID,
    actor_user_id: UUID,
    expected_operation_key: str,
    expected_claim_id: str,
    failure_code: ChapterFailureCode,
) -> None:
    try:
        await service._require_project_owner(project_id, actor_user_id)
        await service._chapter(project_id, chapter_id, lock=True)
        run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
        state, checkpoint = await service._locked_state(run)
        claim = service._run_metadata(run)["reviewer_claim"]
        if (
            state.status
            not in {
                ChapterProductionStatus.EDITOR_REVIEW,
                ChapterProductionStatus.CHIEF_FINAL_REVIEW,
                ChapterProductionStatus.LORE_FINAL_REVIEW,
            }
            or type(claim) is not dict
            or claim.get("operation_key") != expected_operation_key
            or claim.get("claim_id") != expected_claim_id
            or claim.get("checkpoint_index") != checkpoint.checkpoint_index
            or claim.get("status") != REVIEWER_CLAIM_STATUS_CLAIMED
        ):
            raise ChapterProductionV2ReconciliationError()
        failed = state.fail(failure_code)
        set_reviewer_claim(run, {**claim, "status": REVIEWER_CLAIM_STATUS_FAILED})
        service._append_state(run, checkpoint, failed)
        await service._commit()
    except ChapterProductionV2CommitIndeterminateError:
        raise
    except ChapterProductionV2ReconciliationError:
        await service._rollback()
        raise
    except Exception:
        await service._rollback()


__all__ = [
    "ReviewClaimContext",
    "build_review_context_locked",
    "claim_current_review",
    "fail_reviewer",
    "release_reviewer_claim",
    "review_context_snapshots",
    "set_reviewer_claim",
]
