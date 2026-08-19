"""Restart-safe CHAPTER_FINAL database/filesystem saga for Chapter Production V2.

The saga owns the non-Panel finalization boundary exactly once: it consumes a
validated READY source under the facade's locks, stages the deterministic final
document and version in a database transaction, materializes files only after
the commit is known, and only then appends the completed checkpoint/event.
Retries and restarts re-derive canonical final/current/snapshot paths from
chapter, run, and version facts; persisted mutable paths are never trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.documents.chapter_segments import MAX_CHAPTER_CONTENT_BYTES
from app.models import (
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    WorkflowEvent,
    WorkflowRun,
)
from app.services.chapter_production_runtime import next_event_sequence
from app.services.chapter_production_v2_contracts import (
    CONTRACT_VERSION,
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2Finalized,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
    valid_sha256 as _valid_sha256,
)
from app.services.document_service import DocumentCommitIndeterminateError
from app.workspace.hashing import sha256_content
from app.workspace.markdown_store import MarkdownStore
from app.workspace.paths import version_snapshot_path
from app.workflows.chapter_production import (
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterProductionValidationError,
)

_CONTRACT_VERSION = CONTRACT_VERSION
_OPERATION_KIND = "non-panel-final-promotion"


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _reconciliation() -> ChapterProductionV2ReconciliationError:
    return ChapterProductionV2ReconciliationError()


def _canonical_boundary_uuid(value: object) -> UUID:
    """Return a canonical stdlib UUID for saga boundary IDs, or fail closed."""
    if isinstance(value, (str, bytes)):
        raise _invalid()
    try:
        parsed = UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        raise _invalid() from None
    if str(parsed) != str(value) or parsed.int == 0:
        raise _invalid()
    return parsed


@dataclass(frozen=True, slots=True)
class _ReadySourceEvidence:
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    actor_user_id: UUID
    draft_document_id: UUID
    draft_version_id: UUID
    draft_hash: str
    content: str


@dataclass(frozen=True, slots=True)
class _StagedFinal:
    document_id: UUID
    version_id: UUID
    content: str
    path: str
    snapshot_path: str
    byte_size: int
    content_hash: str
    workspace_root: str
    writes: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _WriteProject:
    workspace_root: str


@dataclass(frozen=True, slots=True)
class _WriteDocument:
    project: _WriteProject


def _final_operation_key(
    run: WorkflowRun, state: ChapterProductionState
) -> str:
    if state.document_version_id is None:
        raise _invalid()
    return sha256_content(
        ":".join(
            (
                _CONTRACT_VERSION,
                str(run.id),
                state.document_version_id,
                state.review_policy_version,
                _OPERATION_KIND,
            )
        )
    )


def _final_document_path(*, chapter: Chapter, run: WorkflowRun) -> str:
    return f"chapters/chapter-{chapter.chapter_number:04d}-{run.id}-final.md"


def _valid_final_document_paths(
    *,
    chapter: Chapter,
    run: WorkflowRun,
    document: Document,
    version: DocumentVersion,
) -> bool:
    return (
        document.path == _final_document_path(chapter=chapter, run=run)
        and type(version.version_number) is int
        and version.version_number == 1
        and version.file_path == document.path
        and version.snapshot_path
        == version_snapshot_path(str(document.id), version.version_number).as_posix()
    )


async def _finalized_result_locked(
    service: object,
    *,
    chapter: Chapter,
    run: WorkflowRun,
    state: ChapterProductionState,
) -> ChapterProductionV2Finalized:
    if chapter.final_document_id is None or state.content_hash is None:
        raise _reconciliation()
    documents = list(
        await service.session.scalars(
            select(Document)
            .options(selectinload(Document.project))
            .execution_options(populate_existing=True)
            .where(
                Document.project_id == run.project_id,
                Document.chapter_id == chapter.id,
                Document.type == DocumentType.CHAPTER_FINAL.value,
            )
            .with_for_update()
        )
    )
    if len(documents) != 1 or documents[0].id != chapter.final_document_id:
        raise _reconciliation()
    document = documents[0]
    if document.project is None:
        raise _reconciliation()
    workspace_root = str(document.project.workspace_root)
    version = await service._locked_current_document_version(document)
    if (
        version is None
        or document.current_version_id != version.id
        or version.document_id != document.id
        or not _valid_final_document_paths(
            chapter=chapter,
            run=run,
            document=document,
            version=version,
        )
        or version.content_hash != state.content_hash
        or version.workflow_run_id != run.id
        or version.source != DocumentSource.SYSTEM.value
        or version.parent_version_id is not None
        or version.metadata_
        != {
            "contract_version": _CONTRACT_VERSION,
            "operation_key": _final_operation_key(run, state),
        }
    ):
        raise _reconciliation()
    _verify_final_artifacts(
        document=document,
        version=version,
        workspace_root=workspace_root,
    )
    return ChapterProductionV2Finalized(run.id, document.id, version.id)

def _verified_snapshot_content_plain(
    *, document: Document, version: DocumentVersion, workspace_root: str
) -> str:
    if (
        version.document_id != document.id
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
        content = MarkdownStore(Path(workspace_root)).read_bounded(
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


def _verify_final_artifacts(
    *, document: Document, version: DocumentVersion, workspace_root: str
) -> None:
    read_failed = False
    try:
        snapshot_content = _verified_snapshot_content_plain(
            document=document,
            version=version,
            workspace_root=workspace_root,
        )
    except Exception:
        read_failed = True
        snapshot_content = ""
    try:
        current_content = MarkdownStore(Path(workspace_root)).read_bounded(
            document.path,
            max_bytes=MAX_CHAPTER_CONTENT_BYTES,
        )
    except Exception:
        read_failed = True
        current_content = ""
    if read_failed:
        raise _reconciliation()
    if (
        current_content != snapshot_content
        or len(current_content.encode("utf-8")) != version.byte_size
        or sha256_content(current_content) != version.content_hash
    ):
        raise _reconciliation()


class ChapterFinalizationSaga:
    """Owns final-document staging, materialization, completion, and replay."""

    def __init__(self, service: object) -> None:
        self.service = service

    async def finalize(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
    ) -> ChapterProductionV2Finalized:
        project_id = _canonical_boundary_uuid(project_id)
        chapter_id = _canonical_boundary_uuid(chapter_id)
        workflow_run_id = _canonical_boundary_uuid(workflow_run_id)
        actor_user_id = _canonical_boundary_uuid(actor_user_id)
        service = self.service
        service._validated_ids(project_id, chapter_id, workflow_run_id, actor_user_id)
        try:
            evidence = await self._consume_ready_locked(
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=workflow_run_id,
                actor_user_id=actor_user_id,
            )
            if isinstance(evidence, ChapterProductionV2Finalized):
                return evidence
            staged = await self._stage_final_document_locked(evidence)
            if isinstance(staged, ChapterProductionV2Finalized):
                return staged
            self._materialize_final_files(staged)
            return await self._complete_finalization_locked(evidence)
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

    async def _consume_ready_locked(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        actor_user_id: UUID,
    ) -> _ReadySourceEvidence | ChapterProductionV2Finalized:
        service = self.service
        await service._require_project_owner(project_id, actor_user_id)
        chapter = await service._chapter(project_id, chapter_id, lock=True)
        run = await service._run(
            project_id, chapter_id, workflow_run_id, lock=True
        )
        state, checkpoint = await service._locked_state(run)
        if state.status is ChapterProductionStatus.COMPLETED:
            result = await _finalized_result_locked(service,
                chapter=chapter, run=run, state=state
            )
            await service.session.commit()
            return result
        document, version = await service._locked_review_document(
            project_id=project_id,
            chapter_id=chapter_id,
            state=state,
            chapter=chapter,
        )
        if state.status is ChapterProductionStatus.REVISION_READY:
            policy, editor, chief, lore = await service._live_review_bindings_locked(
                run=run, state=state, document=document, version=version
            )
            try:
                archive_state = state.begin_archive_update(
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
            service._append_state(run, checkpoint, archive_state)
            chapter.status = ChapterProductionStatus.ARCHIVE_UPDATE.value
            await service._commit()
        elif state.status is not ChapterProductionStatus.ARCHIVE_UPDATE:
            raise _invalid()
        else:
            await service.session.commit()

        draft_document_id = _canonical_boundary_uuid(document.id)
        draft_version_id = _canonical_boundary_uuid(version.id)
        draft_hash = version.content_hash
        content = service._verified_snapshot_content(document, version)
        if (
            _canonical_boundary_uuid(version.id) != draft_version_id
            or sha256_content(content) != draft_hash
        ):
            raise _invalid()
        await service.session.commit()
        return _ReadySourceEvidence(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            actor_user_id=actor_user_id,
            draft_document_id=draft_document_id,
            draft_version_id=draft_version_id,
            draft_hash=draft_hash,
            content=content,
        )

    async def _stage_final_document_locked(
        self, evidence: _ReadySourceEvidence
    ) -> _StagedFinal | ChapterProductionV2Finalized:
        service = self.service
        await service._require_project_owner(
            evidence.project_id, evidence.actor_user_id
        )
        chapter = await service._chapter(
            evidence.project_id, evidence.chapter_id, lock=True
        )
        run = await service._run(
            evidence.project_id,
            evidence.chapter_id,
            evidence.workflow_run_id,
            lock=True,
        )
        state, _ = await service._locked_state(run)
        if state.status is ChapterProductionStatus.COMPLETED:
            result = await _finalized_result_locked(service,
                chapter=chapter, run=run, state=state
            )
            await service.session.commit()
            return result
        if state.status is not ChapterProductionStatus.ARCHIVE_UPDATE:
            raise _invalid()
        document, version = await service._locked_review_document(
            project_id=evidence.project_id,
            chapter_id=evidence.chapter_id,
            state=state,
            chapter=chapter,
        )
        locked_document_id = _canonical_boundary_uuid(document.id)
        locked_version_id = _canonical_boundary_uuid(version.id)
        if (
            state.document_id is None
            or state.document_version_id is None
            or locked_document_id != UUID(state.document_id)
            or locked_version_id != UUID(state.document_version_id)
            or version.content_hash != state.content_hash
            or version.content_hash != evidence.draft_hash
        ):
            raise _reconciliation()
        final_documents = list(
            await service.session.scalars(
                select(Document)
                .options(selectinload(Document.project))
                .execution_options(populate_existing=True)
                .where(
                    Document.project_id == evidence.project_id,
                    Document.chapter_id == evidence.chapter_id,
                    Document.type == DocumentType.CHAPTER_FINAL.value,
                )
                .with_for_update()
            )
        )
        if not final_documents:
            return await self._stage_create_final_document(
                evidence=evidence,
                chapter=chapter,
                run=run,
                state=state,
            )
        if len(final_documents) == 1:
            return await self._recover_existing_final_document(
                evidence=evidence,
                chapter=chapter,
                run=run,
                state=state,
                final_document=final_documents[0],
            )
        raise _reconciliation()

    async def _stage_create_final_document(
        self,
        *,
        evidence: _ReadySourceEvidence,
        chapter: Chapter,
        run: WorkflowRun,
        state: ChapterProductionState,
    ) -> _StagedFinal:
        service = self.service
        final_document, current_write, snapshot_write = (
            await service.documents.stage_create_document(
                project_id=evidence.project_id,
                chapter_id=evidence.chapter_id,
                document_type=DocumentType.CHAPTER_FINAL,
                title=f"Chapter {chapter.chapter_number} final",
                path=_final_document_path(chapter=chapter, run=run),
                content=evidence.content,
                source=DocumentSource.SYSTEM,
                workflow_run_id=run.id,
                change_summary="Promoted the reviewed Chapter Production V2 version.",
                version_metadata={
                    "contract_version": _CONTRACT_VERSION,
                    "operation_key": _final_operation_key(run, state),
                },
            )
        )
        final_version = final_document.current_version
        if final_version is None or final_version.snapshot_path is None:
            raise _invalid()
        if final_document.project is None:
            raise _invalid()
        staged = _StagedFinal(
            document_id=final_document.id,
            version_id=final_version.id,
            content=evidence.content,
            path=final_document.path,
            snapshot_path=final_version.snapshot_path,
            byte_size=final_version.byte_size,
            content_hash=final_version.content_hash,
            workspace_root=final_document.project.workspace_root,
            writes=(current_write, snapshot_write),
        )
        chapter.final_document_id = final_document.id
        await service._commit()
        return staged

    async def _recover_existing_final_document(
        self,
        *,
        evidence: _ReadySourceEvidence,
        chapter: Chapter,
        run: WorkflowRun,
        state: ChapterProductionState,
        final_document: Document,
    ) -> _StagedFinal:
        service = self.service
        final_version = await service._locked_current_document_version(
            final_document
        )
        final_operation_key = _final_operation_key(run, state)
        if (
            final_version is None
            or chapter.final_document_id != final_document.id
            or not _valid_final_document_paths(
                chapter=chapter,
                run=run,
                document=final_document,
                version=final_version,
            )
            or final_version.content_hash != evidence.draft_hash
            or final_version.workflow_run_id != run.id
            or final_version.source != DocumentSource.SYSTEM.value
            or final_version.parent_version_id is not None
            or final_version.metadata_
            != {
                "contract_version": _CONTRACT_VERSION,
                "operation_key": final_operation_key,
            }
            or final_version.snapshot_path is None
            or final_document.project is None
        ):
            raise _reconciliation()
        staged = _StagedFinal(
            document_id=final_document.id,
            version_id=final_version.id,
            content=evidence.content,
            path=final_document.path,
            snapshot_path=final_version.snapshot_path,
            byte_size=final_version.byte_size,
            content_hash=final_version.content_hash,
            workspace_root=final_document.project.workspace_root,
            writes=(
                (final_document.path, evidence.content),
                (final_version.snapshot_path, evidence.content),
            ),
        )
        await service.session.commit()
        return staged

    def _materialize_final_files(self, staged: _StagedFinal) -> None:
        try:
            write_document = _WriteDocument(
                _WriteProject(staged.workspace_root)
            )
            self.service.documents.write_staged_files(
                write_document, staged.writes
            )
        except DocumentCommitIndeterminateError:
            raise ChapterProductionV2CommitIndeterminateError() from None

    async def _complete_finalization_locked(
        self, evidence: _ReadySourceEvidence
    ) -> ChapterProductionV2Finalized:
        service = self.service
        await service._require_project_owner(
            evidence.project_id, evidence.actor_user_id
        )
        chapter = await service._chapter(
            evidence.project_id, evidence.chapter_id, lock=True
        )
        run = await service._run(
            evidence.project_id,
            evidence.chapter_id,
            evidence.workflow_run_id,
            lock=True,
        )
        state, checkpoint = await service._locked_state(run)
        if state.status is ChapterProductionStatus.COMPLETED:
            result = await _finalized_result_locked(service,
                chapter=chapter, run=run, state=state
            )
            await service.session.commit()
            return result
        if state.status is not ChapterProductionStatus.ARCHIVE_UPDATE:
            raise _reconciliation()
        document, version = await service._locked_review_document(
            project_id=evidence.project_id,
            chapter_id=evidence.chapter_id,
            state=state,
            chapter=chapter,
        )
        policy, editor, chief, lore = await service._live_review_bindings_locked(
            run=run, state=state, document=document, version=version
        )
        result = await _finalized_result_locked(service,
            chapter=chapter, run=run, state=state
        )
        try:
            completed = state.complete(
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
        service._append_state(run, checkpoint, completed)
        chapter.status = ChapterProductionStatus.COMPLETED.value
        service.session.add(
            WorkflowEvent(
                workflow_run_id=run.id,
                event_sequence=await next_event_sequence(service.session, run.id),
                event_type="chapter_finalized",
                node_name=completed.current_node,
                payload={
                    "chapter_id": str(chapter.id),
                    "document_version_id": state.document_version_id,
                    "final_document_id": str(result.final_document_id),
                    "final_version_id": str(result.final_version_id),
                    "status": completed.status.value,
                },
            )
        )
        await service._commit()
        return result



__all__ = ["ChapterFinalizationSaga"]
