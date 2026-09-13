"""Project-scoped restore points over the existing immutable document store."""

from copy import deepcopy
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from app.core.errors import ConflictError, NotFoundError
from app.models import Chapter, Document, DocumentSource, DocumentVersion, StudioFeedback, StudioRestorePoint
from app.services.document_service import DocumentService, DocumentVersionConflictError
from app.services.studio_feedback import StudioFeedbackService
from app.services.studio_scope import ensure_studio_writable, studio_chapter


class StudioRestorePoints:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _chapter(self, project_id: UUID, chapter_id: UUID) -> Chapter:
        return await studio_chapter(self.session, project_id, chapter_id)

    async def _writable(self, chapter: Chapter) -> None:
        await ensure_studio_writable(self.session, chapter)

    async def _draft(self, chapter: Chapter) -> Document:
        document = await self.session.scalar(select(Document).where(
            Document.id == chapter.current_draft_document_id,
            Document.project_id == chapter.project_id, Document.chapter_id == chapter.id,
            Document.type == "chapter_draft",
        ).with_for_update().execution_options(populate_existing=True))
        if document is None or document.current_version_id is None:
            raise ConflictError("The chapter has no saved draft.")
        return document

    async def list(self, project_id: UUID, chapter_id: UUID) -> list[StudioRestorePoint]:
        await self._chapter(project_id, chapter_id)
        return list(await self.session.scalars(select(StudioRestorePoint).where(
            StudioRestorePoint.chapter_id == chapter_id
        ).options(defer(StudioRestorePoint.feedback_snapshot)).order_by(StudioRestorePoint.created_at.desc(), StudioRestorePoint.id)))

    async def feedback(self, project_id: UUID, chapter_id: UUID, point_id: UUID):
        await self._chapter(project_id, chapter_id)
        point = await self.session.get(StudioRestorePoint, point_id)
        if point is None or point.chapter_id != chapter_id:
            raise NotFoundError("Restore point not found.")
        snapshot = point.feedback_snapshot
        return dict(point_id=point.id, document_id=point.document_id, source_version_id=point.version_id,
                    available=snapshot is not None, comments=deepcopy(snapshot["comments"]) if snapshot else [],
                    requirements=snapshot["requirements"] if snapshot else "")

    async def _snapshot(self, project_id: UUID, chapter_id: UUID) -> dict:
        feedback = await StudioFeedbackService(self.session).read(project_id, chapter_id, "draft")
        return dict(comments=deepcopy(feedback["comments"]), requirements=feedback["requirements"])

    async def create(self, project_id: UUID, chapter_id: UUID, request_id: UUID,
                     expected_current_version_id: UUID) -> StudioRestorePoint:
        chapter = await self._chapter(project_id, chapter_id)
        previous = await self.session.get(StudioRestorePoint, request_id)
        if previous:
            if previous.chapter_id != chapter_id or previous.version_id != expected_current_version_id:
                raise ConflictError("The restore-point request has different inputs.")
            return previous
        await self._writable(chapter)
        document = await self._draft(chapter)
        if document.current_version_id != expected_current_version_id:
            raise DocumentVersionConflictError()
        point = StudioRestorePoint(id=request_id, chapter_id=chapter_id,
                                   document_id=document.id, version_id=expected_current_version_id,
                                   summary="手动存档", feedback_snapshot=await self._snapshot(project_id, chapter_id))
        self.session.add(point)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError("The restore-point request has different inputs.") from None
        return point

    async def restore(self, project_id: UUID, chapter_id: UUID, point_id: UUID,
                      request_id: UUID, expected_current_version_id: UUID) -> DocumentVersion:
        chapter = await self._chapter(project_id, chapter_id)
        point = await self.session.get(StudioRestorePoint, point_id)
        if point is None or point.chapter_id != chapter_id:
            raise NotFoundError("Restore point not found.")
        document = await self._draft(chapter)
        key = sha256(f"studio-restore:{chapter_id}:{request_id}".encode()).hexdigest()
        previous = await self.session.scalar(select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.metadata_["contract_version"].astext == "studio.restore.v1",
            DocumentVersion.metadata_["operation_key"].astext == key,
        ))
        if previous:
            if previous.parent_version_id != expected_current_version_id or previous.metadata_.get("attempt_id") != str(point_id):
                raise ConflictError("The restore request has different inputs.")
            return previous
        await self._writable(chapter)
        if document.current_version_id != expected_current_version_id:
            raise DocumentVersionConflictError()
        source = await self.session.get(Document, point.document_id)
        if source is None or source.chapter_id != chapter_id or source.project_id != project_id:
            raise NotFoundError("Restore point source not found.")
        service = DocumentService(self.session)
        content = await service.read_version_content(point.document_id, point.version_id)
        # The old prose version alone cannot recover mutable comments/requirements.
        # Keep both in a point within the same transaction as the restoration.
        self.session.add(StudioRestorePoint(
            id=uuid4(), chapter_id=chapter_id, document_id=document.id,
            version_id=expected_current_version_id, summary="恢复前自动备份",
            feedback_snapshot=await self._snapshot(project_id, chapter_id),
        ))
        version, *writes = await service.stage_write_document(
            document_id=document.id, content=content, source=DocumentSource.USER,
            expected_current_version_id=expected_current_version_id,
            change_summary="从存档继续写作",
            version_metadata={"contract_version": "studio.restore.v1", "operation_key": key,
                              "attempt_id": str(point_id)},
        )
        if point.feedback_snapshot is not None:
            feedback = await self.session.get(StudioFeedback, (chapter_id, "draft"), populate_existing=True)
            if feedback is None:
                feedback = StudioFeedback(chapter_id=chapter_id, region="draft", revision=0)
                self.session.add(feedback)
            feedback.document_id, feedback.source_version_id = document.id, version.id
            feedback.comments = deepcopy(point.feedback_snapshot["comments"])
            feedback.requirements = point.feedback_snapshot["requirements"]
            feedback.revision += 1
            feedback.request_id, feedback.request_fingerprint = request_id, key
        await service.commit_staged_document(document, writes)
        return version
