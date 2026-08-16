"""Deterministic Editor, Chief Editor, and Lore review coordination.

The coordinator owns the review lifecycle around the facade's frozen DB
primitives: it selects the server-chosen reviewer, keeps provider calls outside
every transaction, normalizes provider failures/cancellations, and drives the
exact action-resolution and claim-acknowledgement transactions.  The facade
retains the shared lock/query helpers used by other workflows and the
coordinator calls them dynamically so the frozen #115 branches stay
byte-equivalent.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.agents import ChapterReviewReport
from app.llm import ProviderInvalidOutputError, ProviderTimeoutError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Document,
    DocumentVersion,
    ReviewReport,
    WorkflowRun,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ProviderError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.workflows.chapter_production import (
    ChapterActionDecision,
    ChapterActionKind,
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterReviewStage,
)


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _valid_nonzero_uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return parsed.int != 0 and str(parsed) == value


def _valid_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )



def _typed_review_decision(decision: str) -> ChapterActionDecision:
    try:
        typed_decision = ChapterActionDecision(decision)
    except (TypeError, ValueError):
        raise _invalid() from None
    if typed_decision not in {
        ChapterActionDecision.ACCEPT_WARNING,
        ChapterActionDecision.REQUEST_REVISION,
    }:
        raise _invalid() from None
    return typed_decision


def new_review_action(
    *,
    run: WorkflowRun,
    project_id: UUID,
    chapter_id: UUID,
    document: Document,
    version: DocumentVersion,
    report: ReviewReport,
    stage: ChapterReviewStage,
    action_kind: ChapterActionKind,
    operation_key: str,
) -> ActionRequest:
    if action_kind is ChapterActionKind.REVIEW_WARNING:
        request_type = "chapter_review_warning"
        options = ["accept_warning", "request_revision"]
        default_option = None
        prompt = "Review the warning for the current chapter version."
    elif action_kind is ChapterActionKind.REVIEW_REVISION:
        request_type = "chapter_review_revision"
        options = ["request_revision"]
        default_option = "request_revision"
        prompt = "Request a revision for the blocking chapter review."
    else:
        raise _invalid()
    return ActionRequest(
        workflow_run_id=run.id,
        project_id=project_id,
        chapter_id=chapter_id,
        request_type=request_type,
        status=ActionRequestStatus.PENDING.value,
        prompt=prompt,
        options=options,
        default_option=default_option,
        metadata_={
            "contract_version": "chapter-production-v2",
            "action_kind": action_kind.value,
            "document_id": str(document.id),
            "document_version_id": str(version.id),
            "content_hash": version.content_hash,
            "operation_key": operation_key,
            "review_report_id": str(report.id),
            "review_stage": stage.value,
        },
    )


def review_action_metadata(action: ActionRequest) -> dict[str, str]:
    metadata = action.metadata_
    if (
        type(metadata) is not dict
        or set(metadata)
        != {
            "contract_version",
            "action_kind",
            "document_id",
            "document_version_id",
            "content_hash",
            "operation_key",
            "review_report_id",
            "review_stage",
        }
        or metadata.get("contract_version") != "chapter-production-v2"
        or metadata.get("action_kind")
        not in {
            ChapterActionKind.REVIEW_WARNING.value,
            ChapterActionKind.REVIEW_REVISION.value,
        }
        or metadata.get("review_stage") not in {item.value for item in ChapterReviewStage}
        or not _valid_nonzero_uuid(metadata.get("document_id"))
        or not _valid_nonzero_uuid(metadata.get("document_version_id"))
        or not _valid_nonzero_uuid(metadata.get("review_report_id"))
        or not _valid_sha256(metadata.get("content_hash"))
        or not _valid_sha256(metadata.get("operation_key"))
        or action.status != ActionRequestStatus.PENDING.value
        or action.user_decision is not None
        or action.user_feedback is not None
        or action.resolved_by_id is not None
        or action.resolved_at is not None
    ):
        raise _invalid()
    expected_type = (
        "chapter_review_warning"
        if metadata["action_kind"] == ChapterActionKind.REVIEW_WARNING.value
        else "chapter_review_revision"
    )
    expected_options = (
        (["accept_warning", "request_revision"], None)
        if metadata["action_kind"] == ChapterActionKind.REVIEW_WARNING.value
        else (["request_revision"], "request_revision")
    )
    expected_prompt = (
        "Review the warning for the current chapter version."
        if metadata["action_kind"] == ChapterActionKind.REVIEW_WARNING.value
        else "Request a revision for the blocking chapter review."
    )
    if (
        action.request_type != expected_type
        or action.options != expected_options[0]
        or action.default_option != expected_options[1]
        or action.prompt != expected_prompt
    ):
        raise _invalid()
    return metadata  # type: ignore[return-value]


class ChapterReviewCoordinator:
    """Owns the deterministic review lifecycle for the stable V2 facade."""

    def __init__(self, service: object) -> None:
        self.service = service

    async def execute_review(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Updated:
        service = self.service
        service._validated_ids(  # type: ignore[attr-defined]
            project_id, chapter_id, workflow_run_id, actor_user_id
        )
        context = await service._claim_current_review(  # type: ignore[attr-defined]
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
        )
        agent = {
            ChapterReviewStage.EDITOR: service.editor_agent,
            ChapterReviewStage.CHIEF_EDITOR: service.chief_editor_agent,
            ChapterReviewStage.LORE: service.lore_agent,
        }[context.stage]
        claim_id = service._run_metadata(context.run)["reviewer_claim"]["claim_id"]
        if agent is None:
            await service._release_reviewer_claim(  # type: ignore[attr-defined]
                workflow_run_id,
                expected_operation_key=context.operation_key,
                expected_claim_id=claim_id,
            )
            raise _invalid() from None
        cancellation: asyncio.CancelledError | None = None
        failure_code: ChapterFailureCode | None = None
        report: ChapterReviewReport | None = None
        try:
            report = await self._provider(agent=agent, request=context.request)
        except asyncio.CancelledError as error:
            cancellation = service._safe_cancelled_error(error)  # type: ignore[attr-defined]
        except ProviderTimeoutError:
            failure_code = ChapterFailureCode.PROVIDER_TIMEOUT
        except ProviderInvalidOutputError:
            failure_code = ChapterFailureCode.INVALID_PROVIDER_OUTPUT
        except Exception:
            failure_code = ChapterFailureCode.PROVIDER_UNAVAILABLE
        if cancellation is not None:
            await service._release_reviewer_claim(  # type: ignore[attr-defined]
                workflow_run_id,
                expected_operation_key=context.operation_key,
                expected_claim_id=claim_id,
            )
            raise cancellation from None
        if failure_code is not None or report is None:
            await service._fail_reviewer(  # type: ignore[attr-defined]
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                actor_user_id=actor_user_id,
                expected_operation_key=context.operation_key,
                expected_claim_id=claim_id,
                failure_code=failure_code or ChapterFailureCode.PROVIDER_UNAVAILABLE,
            )
            raise ChapterProductionV2ProviderError() from None
        return await service._persist_current_review(  # type: ignore[attr-defined]
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
            expected_operation_key=context.operation_key,
            expected_claim_id=claim_id,
            expected_request_hash=context.request_hash,
            report=report,
        )

    async def _provider(self, *, agent: object, request: object) -> ChapterReviewReport:
        return await agent.review(request)  # type: ignore[attr-defined]

    async def resolve_action(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_request_id: UUID,
        *,
        actor_user_id: UUID,
        decision: str,
    ) -> ChapterProductionV2Updated:
        service = self.service
        service._validated_ids(  # type: ignore[attr-defined]
            project_id, chapter_id, workflow_run_id, action_request_id, actor_user_id
        )
        typed_decision = _typed_review_decision(decision)
        return await service._resolve_review_action_locked(  # type: ignore[attr-defined]
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            action_request_id=action_request_id,
            typed_decision=typed_decision,
            actor_user_id=actor_user_id,
        )

    async def acknowledge_no_write(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        *,
        actor_user_id: UUID,
        expected_operation_key: str,
        expected_claim_id: str,
    ) -> ChapterProductionState:
        service = self.service
        service._validated_ids(  # type: ignore[attr-defined]
            project_id, chapter_id, workflow_run_id, actor_user_id
        )
        if not _valid_sha256(expected_operation_key) or not _valid_nonzero_uuid(
            expected_claim_id
        ):
            raise _invalid() from None
        try:
            await service._require_project_owner(project_id, actor_user_id)  # type: ignore[attr-defined]
            chapter = await service._chapter(project_id, chapter_id, lock=True)  # type: ignore[attr-defined]
            run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)  # type: ignore[attr-defined]
            state, checkpoint = await service._locked_state(run)  # type: ignore[attr-defined]
            if state.status not in {
                ChapterProductionStatus.EDITOR_REVIEW,
                ChapterProductionStatus.CHIEF_FINAL_REVIEW,
                ChapterProductionStatus.LORE_FINAL_REVIEW,
            } or state.awaiting_user:
                raise ChapterProductionV2ReconciliationError()
            claim = service._run_metadata(run)["reviewer_claim"]
            stage = {
                ChapterProductionStatus.EDITOR_REVIEW: ChapterReviewStage.EDITOR,
                ChapterProductionStatus.CHIEF_FINAL_REVIEW: ChapterReviewStage.CHIEF_EDITOR,
                ChapterProductionStatus.LORE_FINAL_REVIEW: ChapterReviewStage.LORE,
            }[state.status]
            document, version = await service._locked_review_document(  # type: ignore[attr-defined]
                project_id=project_id,
                chapter_id=chapter_id,
                state=state,
                chapter=chapter,
            )
            if (
                type(claim) is not dict
                or claim.get("operation_key") != expected_operation_key
                or claim.get("claim_id") != expected_claim_id
                or claim.get("status") != "claimed"
                or claim.get("checkpoint_index") != checkpoint.checkpoint_index
                or claim.get("stage") != stage.value
                or await service._exact_review_report_count(  # type: ignore[attr-defined]
                    run=run, version=version, stage=stage
                )
                != 0
            ):
                raise ChapterProductionV2ReconciliationError()
            service._set_reviewer_claim(run, None)  # type: ignore[attr-defined]
            service._append_state(run, checkpoint, state)  # type: ignore[attr-defined]
            await service._commit()  # type: ignore[attr-defined]
            return state
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except ChapterProductionV2ReconciliationError:
            await service._rollback()  # type: ignore[attr-defined]
            raise
        except ChapterProductionV2ValidationError:
            await service._rollback()  # type: ignore[attr-defined]
            raise
        except Exception:
            await service._rollback()  # type: ignore[attr-defined]
            raise _invalid() from None
