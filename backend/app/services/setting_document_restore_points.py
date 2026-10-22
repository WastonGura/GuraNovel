"""Explicit manual archives over immutable setting versions, separate from autosave."""

from copy import deepcopy
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas_documents import WriteDocumentRequest
from app.api.schemas_studio import StudioComment
from app.core.errors import ConflictError, NotFoundError
from app.models import Document, DocumentSource, DocumentVersion, SettingDocumentRestorePoint
from app.services.document_service import DocumentService


class SettingDocumentRestorePoints:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.documents = DocumentService(session)

    async def _document(
        self, document_id: UUID, actor_user_id: UUID | None, *, lock: bool = False
    ) -> Document:
        document = (
            await self.documents._locked_document(document_id)
            if lock
            else await self.documents._document(document_id)
        )
        if document.setting_collection_id is None:
            raise NotFoundError("Setting document not found.")
        self.documents._ensure_actor_permission(document, actor_user_id)
        return document

    @staticmethod
    def _comments(document: Document) -> list[dict]:
        version = document.current_version
        metadata = document.metadata_ or {}
        source_id = metadata.get("comments_source_version_id")
        if "comments" in metadata and source_id in (None, str(document.current_version_id)):
            comments = metadata.get("comments", [])
        else:
            comments = (version.metadata_ or {}).get("setting_comments", []) if version else []
        try:
            if not isinstance(comments, list) or len(comments) > 1024:
                raise ValueError("Invalid saved comments")
            return [
                StudioComment.model_validate(comment).model_dump(mode="json")
                for comment in comments
            ]
        except (SchemaValidationError, ValueError):
            raise ConflictError(
                "Saved setting comments could not be verified; save the current draft before archiving."
            ) from None

    def _response(self, point: SettingDocumentRestorePoint, document: Document) -> dict:
        if point.version.document_id != document.id:
            raise ConflictError("The setting archive does not match its document.")
        current = document.current_version
        try:
            same_comments = self._comments(document) == point.comments_snapshot
        except ConflictError:
            # Past manual archives remain readable even if the current feedback is invalid.
            same_comments = False
        return dict(
            id=point.id,
            document_id=point.document_id,
            version_id=point.version_id,
            point_number=point.point_number,
            summary=point.summary,
            message=point.message,
            created_at=point.created_at,
            version=point.version,
            matches_current=bool(
                current and current.content_hash == point.version.content_hash and same_comments
            ),
        )

    async def list(self, document_id: UUID, *, actor_user_id: UUID | None = None) -> list[dict]:
        document = await self._document(document_id, actor_user_id)
        points = await self.session.scalars(
            select(SettingDocumentRestorePoint)
            .options(
                selectinload(SettingDocumentRestorePoint.version),
            )
            .where(SettingDocumentRestorePoint.document_id == document_id)
            .order_by(SettingDocumentRestorePoint.point_number.desc())
        )
        return [self._response(point, document) for point in points]

    async def create(
        self,
        document_id: UUID,
        *,
        request_id: UUID,
        expected_current_version_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> dict:
        document = await self._document(document_id, actor_user_id, lock=True)
        self.documents._ensure_document_is_mutable(document)
        previous = await self.session.scalar(
            select(SettingDocumentRestorePoint)
            .options(
                selectinload(SettingDocumentRestorePoint.version),
            )
            .where(SettingDocumentRestorePoint.id == request_id)
        )
        if previous is not None:
            if (
                previous.document_id != document_id
                or previous.version_id != expected_current_version_id
            ):
                raise ConflictError("This setting archive request already has different inputs.")
            return self._response(previous, document)
        self.documents._ensure_expected_current_version(document, expected_current_version_id)
        comments = self._comments(document)
        # Validate saved anchors against their exact immutable prose, not a client body.
        content = await self.documents.read_version_content(
            document_id, expected_current_version_id
        )
        try:
            WriteDocumentRequest(
                content=content,
                expected_current_version_id=expected_current_version_id,
                setting_comments=comments,
            )
        except SchemaValidationError:
            raise ConflictError(
                "Saved setting comments do not match the current version; save the draft before archiving."
            ) from None
        last_number = await self.session.scalar(
            select(func.max(SettingDocumentRestorePoint.point_number)).where(
                SettingDocumentRestorePoint.document_id == document_id
            )
        )
        point = SettingDocumentRestorePoint(
            id=request_id,
            document_id=document_id,
            version_id=expected_current_version_id,
            version=document.current_version,
            point_number=(last_number or 0) + 1,
            summary=document.title,
            comments_snapshot=deepcopy(comments),
            actor_user_id=actor_user_id,
        )
        self.session.add(point)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError(
                "This setting archive request already has different inputs."
            ) from None
        return self._response(point, document)

    async def update_message(
        self,
        document_id: UUID,
        point_id: UUID,
        *,
        message: str | None,
        expected_message: str | None,
        actor_user_id: UUID | None = None,
    ) -> dict:
        document = await self._document(document_id, actor_user_id, lock=True)
        self.documents._ensure_document_is_mutable(document)
        point = await self.session.scalar(
            select(SettingDocumentRestorePoint)
            .options(selectinload(SettingDocumentRestorePoint.version))
            .where(
                SettingDocumentRestorePoint.id == point_id,
                SettingDocumentRestorePoint.document_id == document_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if point is None:
            raise NotFoundError("Setting restore point not found in this document.")
        if point.message == message:
            return self._response(point, document)
        if point.message != expected_message:
            raise ConflictError(
                "The archive message changed; preserve this draft and compare its current message."
            )
        point.message = message
        await self.session.commit()
        return self._response(point, document)

    async def restore(
        self,
        document_id: UUID,
        point_id: UUID,
        *,
        expected_current_version_id: UUID,
        actor_user_id: UUID | None = None,
        source: DocumentSource = DocumentSource.USER,
        agent_role: str | None = None,
        workflow_run_id: UUID | None = None,
        change_summary: str | None = None,
    ) -> DocumentVersion:
        document = await self._document(document_id, actor_user_id, lock=True)
        self.documents._ensure_document_is_mutable(document)
        point = await self.session.scalar(
            select(SettingDocumentRestorePoint).where(
                SettingDocumentRestorePoint.id == point_id,
                SettingDocumentRestorePoint.document_id == document_id,
            )
        )
        if point is None:
            raise NotFoundError("Setting restore point not found in this document.")
        return await self.documents.restore_document(
            document_id=document_id,
            version_id=point.version_id,
            expected_current_version_id=expected_current_version_id,
            source=source,
            actor_user_id=actor_user_id,
            agent_role=agent_role,
            workflow_run_id=workflow_run_id,
            change_summary=change_summary or f"恢复手动存档 {point.point_number}",
            setting_comments_snapshot=deepcopy(point.comments_snapshot),
            version_metadata={
                "contract_version": "setting.restore-point.v1",
                "operation_key": sha256(
                    f"setting-point:{document_id}:{point.id}:{expected_current_version_id}".encode()
                ).hexdigest(),
                "attempt_id": str(point.id),
            },
        )
