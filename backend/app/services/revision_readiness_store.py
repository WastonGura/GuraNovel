"""Scope-bound persistence for Chapter Production V2 READY checkpoints and events."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Document,
    DocumentType,
    DocumentVersion,
    ReviewMode,
    ReviewReport,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
)
from app.services.chapter_review_validation import validated_persisted_review_report
from app.workflows.chapter_production import (
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterProductionValidationError,
    ChapterReviewBinding,
    ChapterReviewPolicyBinding,
    ChapterReviewStage,
)


_READY_EVENT_TYPE = "revision_ready"
_READY_STATUS = ChapterProductionStatus.REVISION_READY.value
_READY_NODE = ChapterProductionStatus.REVISION_READY.value
_READY_EVENT_KEYS = frozenset(
    {
        "chapter_id",
        "checkpoint_id",
        "checkpoint_index",
        "document_id",
        "document_version_id",
        "content_hash",
        "review_policy_version",
        "status",
    }
)
_POLICY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _reconciliation() -> ChapterProductionV2ReconciliationError:
    return ChapterProductionV2ReconciliationError()


def ready_semantic_key(
    *,
    workflow_run_id: UUID,
    document_version_id: UUID,
    review_policy_version: str,
) -> tuple[str, str, str]:
    """Derive the exact content-free READY key from canonical durable facts."""

    try:
        workflow_run_id = UUID(str(workflow_run_id))
        document_version_id = UUID(str(document_version_id))
    except (AttributeError, TypeError, ValueError):
        raise _invalid() from None
    if (
        workflow_run_id.int == 0
        or document_version_id.int == 0
        or type(review_policy_version) is not str
        or _POLICY_RE.fullmatch(review_policy_version) is None
    ):
        raise _invalid() from None
    return (str(workflow_run_id), str(document_version_id), review_policy_version)


def ready_event_payload(
    *,
    chapter_id: UUID | str,
    checkpoint_id: UUID,
    checkpoint_index: int,
    document_id: UUID | str,
    document_version_id: UUID | str,
    content_hash: str,
    review_policy_version: str,
) -> dict[str, str | int]:
    """Return the exact mechanical READY event payload."""

    return {
        "chapter_id": str(chapter_id),
        "checkpoint_id": str(checkpoint_id),
        "checkpoint_index": checkpoint_index,
        "document_id": str(document_id),
        "document_version_id": str(document_version_id),
        "content_hash": content_hash,
        "review_policy_version": review_policy_version,
        "status": _READY_STATUS,
    }


@dataclass(frozen=True, slots=True, repr=False)
class RevisionReadyPair:
    """One validated READY checkpoint/event pair."""

    state: ChapterProductionState
    checkpoint: WorkflowCheckpoint
    event: WorkflowEvent

    def __repr__(self) -> str:
        return "RevisionReadyPair()"


@dataclass(frozen=True, slots=True)
class _ReviewStateReferences:
    review_policy_version: str
    chief_editor_required: bool
    editor_report_id: str | None
    chief_editor_report_id: str | None
    lore_report_id: str | None


def _review_slots(state: ChapterProductionState | _ReviewStateReferences) -> tuple:
    slots: list[tuple[UUID, str, str]] = []
    if state.editor_report_id is not None:
        slots.append(
            (UUID(state.editor_report_id), ReviewMode.CHAPTER_EDITOR.value, "editor_agent")
        )
    if state.chief_editor_report_id is not None:
        slots.append(
            (
                UUID(state.chief_editor_report_id),
                ReviewMode.CHAPTER_CHIEF_FINAL.value,
                "chief_editor_agent",
            )
        )
    if state.lore_report_id is not None:
        slots.append(
            (UUID(state.lore_report_id), ReviewMode.CHAPTER_FINAL_LORE.value, "lore_agent")
        )
    return tuple(slots)


@runtime_checkable
class RevisionReadinessService(Protocol):
    """The exact facade helpers the READY store is allowed to call."""

    session: AsyncSession
    documents: object

    def _run_metadata(self, run: WorkflowRun) -> dict[str, str]: ...
    def _project_state(
        self, run: WorkflowRun, state: ChapterProductionState
    ) -> None: ...


class RevisionReadinessStore:
    """Locked READY mutations and revalidation inside a caller-owned transaction."""

    def __init__(self, service: RevisionReadinessService) -> None:
        if not isinstance(service, RevisionReadinessService):
            raise _invalid() from None
        self._service = service

    async def _locked_review_rows(
        self, run: WorkflowRun, document: Document, version: DocumentVersion
    ) -> list[ReviewReport]:
        return list(
            await self._service.session.scalars(
                select(ReviewReport)
                .execution_options(populate_existing=True)
                .where(
                    ReviewReport.project_id == run.project_id,
                    ReviewReport.chapter_id == run.chapter_id,
                    ReviewReport.workflow_run_id == run.id,
                    ReviewReport.target_document_id == document.id,
                    ReviewReport.target_version_id == version.id,
                    ReviewReport.review_mode.in_(
                        (
                            ReviewMode.CHAPTER_EDITOR.value,
                            ReviewMode.CHAPTER_CHIEF_FINAL.value,
                            ReviewMode.CHAPTER_FINAL_LORE.value,
                        )
                    ),
                )
                .with_for_update()
            )
        )

    async def live_review_bindings_locked(
        self,
        *,
        run: WorkflowRun,
        state: ChapterProductionState | _ReviewStateReferences,
        document: Document,
        version: DocumentVersion,
    ) -> tuple[
        ChapterReviewPolicyBinding,
        ChapterReviewBinding,
        ChapterReviewBinding | None,
        ChapterReviewBinding,
    ]:
        metadata = self._service._run_metadata(run)
        policy = ChapterReviewPolicyBinding(
            workflow_run_id=str(run.id),
            chapter_id=str(run.chapter_id),
            review_policy_version=metadata["review_policy_version"],
            chief_editor_required=metadata["chief_editor_required"],
        )
        expected = _review_slots(state)
        required_count = 3 if state.chief_editor_required else 2
        if len(expected) != required_count:
            raise _invalid() from None
        rows = await self._locked_review_rows(run, document, version)
        if len(rows) != required_count or {row.id for row in rows} != {
            item[0] for item in expected
        }:
            raise _reconciliation() from None
        by_id = {row.id: row for row in rows}
        bindings: dict[ChapterReviewStage, ChapterReviewBinding] = {}
        stage_by_mode = {
            ReviewMode.CHAPTER_EDITOR.value: ChapterReviewStage.EDITOR,
            ReviewMode.CHAPTER_CHIEF_FINAL.value: ChapterReviewStage.CHIEF_EDITOR,
            ReviewMode.CHAPTER_FINAL_LORE.value: ChapterReviewStage.LORE,
        }
        for report_id, mode, role in expected:
            row = by_id[report_id]
            stage = stage_by_mode[mode]
            if (
                row.review_mode != mode
                or row.reviewer_agent_role != role
                or row.passed is not True
                or row.target_document_id != document.id
                or row.target_version_id != version.id
            ):
                raise _reconciliation() from None
            await validated_persisted_review_report(
                self._service,
                row=row,
                run=run,
                document=document,
                version=version,
                stage=stage,
            )
            bindings[stage] = ChapterReviewBinding(
                report_id=str(row.id),
                stage=stage,
                workflow_run_id=str(run.id),
                chapter_id=str(run.chapter_id),
                document_id=str(document.id),
                document_version_id=str(version.id),
                review_mode=row.review_mode,
                reviewer_agent_role=row.reviewer_agent_role,
                passed=row.passed,
            )
        return (
            policy,
            bindings[ChapterReviewStage.EDITOR],
            bindings.get(ChapterReviewStage.CHIEF_EDITOR),
            bindings[ChapterReviewStage.LORE],
        )

    async def enter(
        self,
        *,
        run: WorkflowRun,
        checkpoint: WorkflowCheckpoint,
        state: ChapterProductionState,
        document: Document,
        version: DocumentVersion,
    ) -> ChapterProductionState:
        policy, editor, chief, lore = await self.live_review_bindings_locked(
            run=run, state=state, document=document, version=version
        )
        try:
            ready = state.finalize_revision_ready(
                policy=policy,
                document_id=str(document.id),
                current_document_version_id=str(version.id),
                version_content_hash=version.content_hash,
                editor_report=editor,
                chief_editor_report=chief,
                lore_report=lore,
            )
        except ChapterProductionValidationError:
            raise _invalid() from None
        pairs = await self.validated_pairs(run)
        semantic_key = ready_semantic_key(
            workflow_run_id=run.id,
            document_version_id=version.id,
            review_policy_version=policy.review_policy_version,
        )
        matches = [pair for pair in pairs if pair.state.semantic_ready_key == semantic_key]
        if not matches:
            ready_checkpoint = WorkflowCheckpoint(
                workflow_run_id=run.id,
                checkpoint_index=checkpoint.checkpoint_index + 1,
                node_name=ready.current_node,
                state_json=ready.to_checkpoint(),
            )
            self._service.session.add(ready_checkpoint)
            await self._service.session.flush()
            self._service.session.add(
                WorkflowEvent(
                    workflow_run_id=run.id,
                    event_type=_READY_EVENT_TYPE,
                    node_name=ready.current_node,
                    payload=ready_event_payload(
                        chapter_id=run.chapter_id,
                        checkpoint_id=ready_checkpoint.id,
                        checkpoint_index=ready_checkpoint.checkpoint_index,
                        document_id=document.id,
                        document_version_id=version.id,
                        content_hash=version.content_hash,
                        review_policy_version=policy.review_policy_version,
                    ),
                )
            )
            self._service._project_state(run, ready)
            return ready
        if len(matches) == 1:
            pair = matches[0]
            expected_payload = ready_event_payload(
                chapter_id=run.chapter_id,
                checkpoint_id=pair.checkpoint.id,
                checkpoint_index=pair.checkpoint.checkpoint_index,
                document_id=document.id,
                document_version_id=version.id,
                content_hash=version.content_hash,
                review_policy_version=policy.review_policy_version,
            )
            if (
                pair.checkpoint.node_name != ready.current_node
                or pair.checkpoint.state_json != ready.to_checkpoint()
                or pair.event.node_name != ready.current_node
                or pair.event.payload != expected_payload
            ):
                raise _reconciliation() from None
            self._service._project_state(run, ready)
            return ready
        raise _reconciliation() from None

    async def validated_pairs(self, run: WorkflowRun) -> tuple[RevisionReadyPair, ...]:
        checkpoints = list(
            await self._service.session.scalars(
                select(WorkflowCheckpoint)
                .execution_options(populate_existing=True)
                .where(WorkflowCheckpoint.workflow_run_id == run.id)
                .order_by(WorkflowCheckpoint.checkpoint_index)
                .with_for_update()
            )
        )
        markers = [
            item
            for item in checkpoints
            if item.node_name == _READY_NODE
            or (
                type(item.state_json) is dict
                and item.state_json.get("status") == _READY_STATUS
            )
        ]
        restored: list[tuple[ChapterProductionState, WorkflowCheckpoint]] = []
        for marker in markers:
            if (
                marker.node_name != _READY_NODE
                or type(marker.state_json) is not dict
                or marker.state_json.get("status") != _READY_STATUS
                or sum(
                    item.checkpoint_index == marker.checkpoint_index - 1
                    for item in checkpoints
                )
                != 1
            ):
                raise _reconciliation() from None
            restored.append((await self.restore_marker(run, marker), marker))

        events = list(
            await self._service.session.scalars(
                select(WorkflowEvent)
                .execution_options(populate_existing=True)
                .where(
                    WorkflowEvent.workflow_run_id == run.id,
                    WorkflowEvent.event_type == _READY_EVENT_TYPE,
                )
                .with_for_update()
            )
        )
        if len(events) != len(restored) or any(
            type(item.payload) is not dict or set(item.payload) != _READY_EVENT_KEYS
            for item in events
        ):
            raise _reconciliation() from None
        pairs: list[RevisionReadyPair] = []
        used_events: set[UUID] = set()
        for state, marker in restored:
            matching = [
                item
                for item in events
                if item.payload.get("checkpoint_id") == str(marker.id)
            ]
            expected_payload = ready_event_payload(
                chapter_id=run.chapter_id,
                checkpoint_id=marker.id,
                checkpoint_index=marker.checkpoint_index,
                document_id=state.document_id,
                document_version_id=state.document_version_id,
                content_hash=state.content_hash,
                review_policy_version=state.review_policy_version,
            )
            if (
                len(matching) != 1
                or matching[0].id in used_events
                or matching[0].node_name != _READY_NODE
                or matching[0].payload != expected_payload
            ):
                raise _reconciliation() from None
            used_events.add(matching[0].id)
            pairs.append(RevisionReadyPair(state, marker, matching[0]))
        return tuple(pairs)

    async def restore_marker(
        self, run: WorkflowRun, checkpoint: WorkflowCheckpoint
    ) -> ChapterProductionState:
        payload = checkpoint.state_json
        try:
            if type(payload) is not dict:
                raise ChapterProductionValidationError("READY payload is malformed.")
            references = _ReviewStateReferences(
                review_policy_version=payload["review_policy_version"],
                chief_editor_required=payload["chief_editor_required"],
                editor_report_id=payload["editor_report_id"],
                chief_editor_report_id=payload["chief_editor_report_id"],
                lore_report_id=payload["lore_report_id"],
            )
            document_id = UUID(payload["document_id"])
            version_id = UUID(payload["document_version_id"])
            document = await self._service.session.scalar(
                select(Document)
                .options(selectinload(Document.project))
                .execution_options(populate_existing=True)
                .where(
                    Document.id == document_id,
                    Document.project_id == run.project_id,
                    Document.chapter_id == run.chapter_id,
                    Document.type == DocumentType.CHAPTER_DRAFT.value,
                )
                .with_for_update()
            )
            version = await self._service.session.scalar(
                select(DocumentVersion)
                .execution_options(populate_existing=True)
                .where(
                    DocumentVersion.id == version_id,
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.content_hash == payload["content_hash"],
                )
                .with_for_update()
            )
            if document is None or version is None:
                raise ChapterProductionValidationError("READY version is stale.")
            policy, editor, chief, lore = await self.live_review_bindings_locked(
                run=run,
                state=references,
                document=document,
                version=version,
            )
            return ChapterProductionState.from_revision_ready_checkpoint(
                payload,
                policy=policy,
                workflow_run_id=str(run.id),
                chapter_id=str(run.chapter_id),
                run_workflow_type=run.workflow_type,
                run_status=_READY_STATUS,
                run_current_node=_READY_NODE,
                run_awaiting_user=False,
                checkpoint_workflow_run_id=str(checkpoint.workflow_run_id),
                checkpoint_node_name=checkpoint.node_name,
                document_id=str(document.id),
                current_document_version_id=str(version.id),
                version_content_hash=version.content_hash,
                editor_report=editor,
                chief_editor_report=chief,
                lore_report=lore,
            )
        except ChapterProductionV2ReconciliationError:
            raise
        except (ChapterProductionValidationError, KeyError, TypeError, ValueError):
            raise _reconciliation() from None

    async def validate_existing_pair(
        self,
        *,
        run: WorkflowRun,
        state: ChapterProductionState,
        policy: ChapterReviewPolicyBinding,
        document: Document,
        version: DocumentVersion,
        editor: ChapterReviewBinding,
        chief: ChapterReviewBinding | None,
        lore: ChapterReviewBinding,
    ) -> None:
        semantic_key = ready_semantic_key(
            workflow_run_id=run.id,
            document_version_id=version.id,
            review_policy_version=policy.review_policy_version,
        )
        matches = [
            pair
            for pair in await self.validated_pairs(run)
            if pair.state.semantic_ready_key == semantic_key
        ]
        if len(matches) != 1:
            raise _reconciliation() from None
        pair = matches[0]
        ready = ChapterProductionState.from_revision_ready_checkpoint(
            pair.checkpoint.state_json,
            policy=policy,
            workflow_run_id=str(run.id),
            chapter_id=str(run.chapter_id),
            run_workflow_type=run.workflow_type,
            run_status=_READY_STATUS,
            run_current_node=_READY_NODE,
            run_awaiting_user=False,
            checkpoint_workflow_run_id=str(pair.checkpoint.workflow_run_id),
            checkpoint_node_name=pair.checkpoint.node_name,
            document_id=str(document.id),
            current_document_version_id=str(version.id),
            version_content_hash=version.content_hash,
            editor_report=editor,
            chief_editor_report=chief,
            lore_report=lore,
        )
        expected_payload = ready_event_payload(
            chapter_id=run.chapter_id,
            checkpoint_id=pair.checkpoint.id,
            checkpoint_index=pair.checkpoint.checkpoint_index,
            document_id=document.id,
            document_version_id=version.id,
            content_hash=version.content_hash,
            review_policy_version=policy.review_policy_version,
        )
        if ready.semantic_ready_key != semantic_key or pair.event.payload != expected_payload:
            raise _reconciliation() from None


__all__ = (
    "RevisionReadyPair",
    "RevisionReadinessStore",
    "ready_event_payload",
    "ready_semantic_key",
)
