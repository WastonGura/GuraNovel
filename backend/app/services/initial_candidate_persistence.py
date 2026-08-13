"""Persist or adopt one exact initial Writer candidate without finalizing it."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.chapter_writer_contracts import CandidateChapterOutput, InitialDraftRequest
from app.documents.chapter_segments import (
    CURRENT_CHAPTER_SEGMENTER_VERSION,
    MAX_CHAPTER_CONTENT_BYTES,
    ChapterSegmentError,
    derive_chapter_segment_map,
    normalize_chapter_content,
)
from app.models import Document, DocumentSource, DocumentType, DocumentVersion
from app.services.chapter_phase_session_lease import ChapterPhaseSessionLease
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
)
from app.services.document_service import DocumentCommitIndeterminateError, DocumentService
from app.services.initial_generation_snapshot import InitialGenerationSnapshot
from app.services.initial_provider_handoff import (
    InitialProviderResult,
    _InitialEvidencePhase,
)
from app.services.provider_attempt_contracts import CONTRACT_VERSION
from app.workspace.hashing import sha256_content
from app.workspace.paths import version_snapshot_path
from app.workspace.word_count import count_words


_CHANGE_SUMMARY = "Generated Chapter Production V2 draft."


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _reconcile() -> ChapterProductionV2ReconciliationError:
    return ChapterProductionV2ReconciliationError()


def _valid_uuid(value: object) -> bool:
    return type(value) is UUID and value.int != 0


def _valid_hash(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


async def _rollback(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except BaseException:
        pass


async def _commit(session: AsyncSession) -> None:
    failed = False
    try:
        await session.commit()
    except BaseException:
        failed = True
    if failed:
        await _rollback(session)
        raise ChapterProductionV2CommitIndeterminateError() from None


def _compose(result: InitialProviderResult) -> str:
    failed = False
    content = ""
    try:
        values = tuple(segment.content for segment in result.candidate.segments)
        if not 1 <= len(values) <= 64:
            raise ValueError
        normalized = []
        for value in values:
            if type(value) is not str or value != value.strip():
                raise ValueError
            item = normalize_chapter_content(value)
            if not item:
                raise ValueError
            normalized.append(item)
        content = normalize_chapter_content("\n\n".join(normalized) + "\n")
        if len(content.encode("utf-8")) > MAX_CHAPTER_CONTENT_BYTES:
            raise ValueError
    except (AttributeError, ChapterSegmentError, UnicodeError, ValueError, TypeError):
        failed = True
    if failed:
        raise _invalid() from None
    return content


def _validate_result(result: object) -> tuple[InitialProviderResult, str]:
    if (
        type(result) is not InitialProviderResult
        or type(result.generation) is not InitialGenerationSnapshot
        or type(result.request) is not InitialDraftRequest
        or type(result.candidate) is not CandidateChapterOutput
    ):
        raise _invalid() from None
    scope = result.generation.scope
    request = result.request
    candidate = result.candidate
    request_lineage = (
        request.project_id, request.chapter_id, request.workflow_run_id,
        request.approved_outline.document_id, request.approved_outline.version_id,
    )
    candidate_lineage = (
        candidate.project_id, candidate.chapter_id, candidate.workflow_run_id,
        candidate.approved_outline_document_id, candidate.approved_outline_version_id,
    )
    expected_segments = tuple(
        (item.segment_id, item.index, item.title) for item in request.allowed_segments
    )
    actual_segments = tuple(
        (item.segment_id, item.index, item.title) for item in candidate.segments
    )
    if (
        request_lineage[:3]
        != (scope.project_id, scope.chapter_id, scope.workflow_run_id)
        or candidate_lineage != request_lineage
        or candidate.source_draft_document_id is not None
        or candidate.source_draft_version_id is not None
        or candidate.complete_chapter is not True
        or actual_segments != expected_segments
    ):
        raise _invalid() from None
    return result, _compose(result)


def _validate_prospective(result: InitialProviderResult, content: str) -> None:
    request = result.request
    failed = False
    try:
        segment_map = derive_chapter_segment_map(
            project_id=request.project_id,
            chapter_id=request.chapter_id,
            document_id=request.approved_outline.document_id,
            version_id=request.approved_outline.version_id,
            content=content,
            segmenter_version=CURRENT_CHAPTER_SEGMENTER_VERSION,
        )
        segment_map.canonical_bytes()
    except (ChapterSegmentError, UnicodeError, ValueError, TypeError, AttributeError):
        failed = True
    if failed:
        raise _invalid() from None


@dataclass(frozen=True, slots=True, repr=False)
class InitialCandidateIdentity:
    project_id: UUID
    chapter_id: UUID
    workflow_run_id: UUID
    document_id: UUID
    version_id: UUID
    content_hash: str
    operation_key: str
    attempt_id: UUID

    def __post_init__(self) -> None:
        if (
            not all(
                _valid_uuid(value)
                for value in (
                    self.project_id, self.chapter_id, self.workflow_run_id,
                    self.document_id, self.version_id, self.attempt_id,
                )
            )
            or not _valid_hash(self.content_hash)
            or not _valid_hash(self.operation_key)
        ):
            raise _invalid() from None

    def __repr__(self) -> str:
        return "InitialCandidateIdentity()"


class InitialCandidatePersistence:
    def __init__(
        self, phase_sessions: ChapterPhaseSessionLease, chief_editor_required: bool,
    ) -> None:
        if (
            type(phase_sessions) is not ChapterPhaseSessionLease
            or type(chief_editor_required) is not bool
        ):
            raise _invalid() from None
        self.phase_sessions = phase_sessions
        self.chief_editor_required = chief_editor_required

    async def persist(self, result: InitialProviderResult) -> InitialCandidateIdentity:
        result, content = _validate_result(result)
        _validate_prospective(result, content)
        async with self.phase_sessions.lease() as session:
            return await self._persist(session, result, content)

    async def _persist(
        self, session: AsyncSession, result: InitialProviderResult, content: str,
    ) -> InitialCandidateIdentity:
        try:
            phase = _InitialEvidencePhase(session, self.chief_editor_required)
            scope = result.generation.scope
            evidence = await phase.load(
                scope.project_id, scope.chapter_id, scope.actor_user_id
            )
            self._validate_live(phase, evidence, result)
            chapter = await phase.repository.chapter(
                scope.project_id, scope.chapter_id, lock=True
            )
            expected_title = f"Chapter {chapter.chapter_number} draft"
            expected_path = (
                f"chapters/chapter-{chapter.chapter_number:04d}-"
                f"{scope.workflow_run_id}-draft.md"
            )
            candidates = await self._candidates(session, result)
            if len(candidates) > 1:
                raise _reconcile() from None
            if candidates:
                document, version = candidates[0]
                writes = self._writes(document, version, content)
            else:
                document, writes, version = await self._stage(
                    phase, result, content, expected_title, expected_path
                )
            self._validate_candidate(
                document, version, result, content, expected_title, expected_path
            )
            identity = self._identity(document, version, result)
            await _commit(session)
        except ChapterProductionV2CommitIndeterminateError:
            raise
        except (ChapterProductionV2ValidationError, ChapterProductionV2ReconciliationError):
            await _rollback(session)
            raise
        except Exception:
            await _rollback(session)
            raise _reconcile() from None
        try:
            DocumentService(session).write_staged_files(document, writes)
        except DocumentCommitIndeterminateError:
            raise ChapterProductionV2CommitIndeterminateError() from None
        return identity

    @staticmethod
    def _validate_live(
        phase: _InitialEvidencePhase, evidence: object, result: InitialProviderResult,
    ) -> None:
        generation = result.generation
        if (
            evidence.attempt != generation.attempt  # type: ignore[attr-defined]
            or evidence.operation_key != generation.scope.operation_key  # type: ignore[attr-defined]
            or evidence.checkpoints[-1].checkpoint_index  # type: ignore[attr-defined]
            != generation.scope.checkpoint_index
            or phase.request(evidence).model_dump_json() != result.request.model_dump_json()
        ):
            raise _reconcile() from None

    @staticmethod
    async def _candidates(
        session: AsyncSession, result: InitialProviderResult,
    ) -> list[tuple[Document, DocumentVersion]]:
        scope = result.generation.scope
        metadata_key = {
            "contract_version": CONTRACT_VERSION,
            "operation_key": scope.operation_key,
        }
        metadata_attempt = {"attempt_id": str(scope.attempt_id)}
        versions = list(await session.scalars(
            select(DocumentVersion)
            .where(or_(
                DocumentVersion.workflow_run_id == scope.workflow_run_id,
                DocumentVersion.metadata_.contains(metadata_key),
                DocumentVersion.metadata_.contains(metadata_attempt),
            ))
            .with_for_update().execution_options(populate_existing=True)
        ))
        if not versions:
            return []
        documents = list(await session.scalars(
            select(Document).options(selectinload(Document.project))
            .where(Document.id.in_(tuple(version.document_id for version in versions)))
            .with_for_update().execution_options(populate_existing=True)
        ))
        by_id = {document.id: document for document in documents}
        if len(documents) != len({version.document_id for version in versions}):
            raise _reconcile() from None
        return [(by_id[version.document_id], version) for version in versions]

    @staticmethod
    async def _stage(
        phase: _InitialEvidencePhase, result: InitialProviderResult, content: str,
        expected_title: str, expected_path: str,
    ) -> tuple[Document, tuple[tuple[str, str], ...], DocumentVersion]:
        scope = result.generation.scope
        document, current_write, snapshot_write = await phase.documents.stage_create_document(
            project_id=scope.project_id,
            chapter_id=scope.chapter_id,
            document_type=DocumentType.CHAPTER_DRAFT,
            title=expected_title,
            path=expected_path,
            content=content,
            source=DocumentSource.WRITER_AGENT,
            agent_role="writer_agent",
            workflow_run_id=scope.workflow_run_id,
            change_summary=_CHANGE_SUMMARY,
            version_metadata={
                "contract_version": CONTRACT_VERSION,
                "operation_key": scope.operation_key,
                "attempt_id": str(scope.attempt_id),
            },
        )
        version = document.current_version
        if version is None:
            raise _reconcile() from None
        return document, (current_write, snapshot_write), version

    @staticmethod
    def _writes(
        document: Document, version: DocumentVersion, content: str,
    ) -> tuple[tuple[str, str], ...]:
        if version.snapshot_path is None:
            raise _reconcile() from None
        return ((document.path, content), (version.snapshot_path, content))

    @staticmethod
    def _validate_candidate(
        document: Document, version: DocumentVersion,
        result: InitialProviderResult, content: str,
        expected_title: str, expected_path: str,
    ) -> None:
        scope = result.generation.scope
        expected_metadata = {
            "contract_version": CONTRACT_VERSION,
            "operation_key": scope.operation_key,
            "attempt_id": str(scope.attempt_id),
        }
        if (
            document.project_id != scope.project_id
            or document.chapter_id != scope.chapter_id
            or document.type != DocumentType.CHAPTER_DRAFT.value
            or document.path != expected_path
            or document.title != expected_title
            or type(document.metadata_) is not dict or document.metadata_ != {}
            or document.current_version_id != version.id
            or version.document_id != document.id
            or version.version_number != 1
            or version.parent_version_id is not None
            or version.source != DocumentSource.WRITER_AGENT.value
            or version.actor_user_id is not None
            or version.agent_role != "writer_agent"
            or version.workflow_run_id != scope.workflow_run_id
            or version.content_hash != sha256_content(content)
            or version.byte_size != len(content.encode("utf-8"))
            or version.word_count != count_words(content)
            or version.file_path != expected_path
            or version.snapshot_path
            != version_snapshot_path(str(document.id), 1).as_posix()
            or version.change_summary != _CHANGE_SUMMARY
            or type(version.metadata_) is not dict
            or version.metadata_ != expected_metadata
        ):
            raise _reconcile() from None

    @staticmethod
    def _identity(
        document: Document, version: DocumentVersion, result: InitialProviderResult,
    ) -> InitialCandidateIdentity:
        scope = result.generation.scope
        return InitialCandidateIdentity(
            scope.project_id, scope.chapter_id, scope.workflow_run_id,
            UUID(str(document.id)), UUID(str(version.id)), version.content_hash,
            scope.operation_key, scope.attempt_id,
        )


__all__ = ["InitialCandidateIdentity", "InitialCandidatePersistence"]
