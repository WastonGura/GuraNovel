"""Deterministic Editor, Chief Editor, and Lore review coordination."""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.agents import ChapterReviewReport
from app.llm import ProviderInvalidOutputError, ProviderTimeoutError
from app.services.chapter_production_v2_contracts import (
    REVIEWER_CLAIM_STATUS_CLAIMED,
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ReviewProviderError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
    safe_cancelled_error,
    valid_nonzero_uuid,
    valid_sha256,
)
from app.services.chapter_review_claim import (
    claim_current_review,
    fail_reviewer,
    release_reviewer_claim,
    set_reviewer_claim,
)
from app.services.chapter_review_persistence import (
    persist_current_review,
    resolve_review_action_locked,
)
from app.services.chapter_review_protocols import ChapterReviewService
from app.workflows.chapter_production import (
    ChapterActionDecision,
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterReviewStage,
)


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


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


class ChapterReviewCoordinator:
    """Owns the deterministic review lifecycle for the stable V2 facade."""

    def __init__(self, service: ChapterReviewService) -> None:
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
        service._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        context = await claim_current_review(
            service,
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
            await release_reviewer_claim(
                service,
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
            cancellation = safe_cancelled_error(error)
        except ProviderTimeoutError:
            failure_code = ChapterFailureCode.PROVIDER_TIMEOUT
        except ProviderInvalidOutputError:
            failure_code = ChapterFailureCode.INVALID_PROVIDER_OUTPUT
        except Exception:
            failure_code = ChapterFailureCode.PROVIDER_UNAVAILABLE
        if cancellation is not None:
            await release_reviewer_claim(
                service,
                workflow_run_id,
                expected_operation_key=context.operation_key,
                expected_claim_id=claim_id,
            )
            raise cancellation from None
        if failure_code is not None or report is None:
            await fail_reviewer(
                service,
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                actor_user_id=actor_user_id,
                expected_operation_key=context.operation_key,
                expected_claim_id=claim_id,
                failure_code=failure_code or ChapterFailureCode.PROVIDER_UNAVAILABLE,
            )
            raise ChapterProductionV2ReviewProviderError() from None
        return await persist_current_review(
            service,
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
        service._validated_ids(
            project_id, chapter_id, workflow_run_id, action_request_id, actor_user_id
        )
        typed_decision = _typed_review_decision(decision)
        return await resolve_review_action_locked(
            service,
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
        service._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        if not valid_sha256(expected_operation_key) or not valid_nonzero_uuid(
            expected_claim_id
        ):
            raise _invalid() from None
        try:
            await service._require_project_owner(project_id, actor_user_id)
            chapter = await service._chapter(project_id, chapter_id, lock=True)
            run = await service._run(project_id, chapter_id, workflow_run_id, lock=True)
            state, checkpoint = await service._locked_state(run)
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
            document, version = await service._locked_review_document(
                project_id=project_id,
                chapter_id=chapter_id,
                state=state,
                chapter=chapter,
            )
            if (
                type(claim) is not dict
                or claim.get("operation_key") != expected_operation_key
                or claim.get("claim_id") != expected_claim_id
                or claim.get("status") != REVIEWER_CLAIM_STATUS_CLAIMED
                or claim.get("checkpoint_index") != checkpoint.checkpoint_index
                or claim.get("stage") != stage.value
                or await service._exact_review_report_count(
                    run=run, version=version, stage=stage
                )
                != 0
            ):
                raise ChapterProductionV2ReconciliationError()
            set_reviewer_claim(run, None)
            service._append_state(run, checkpoint, state)
            await service._commit()
            return state
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
