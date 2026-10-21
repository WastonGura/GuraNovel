"""Persist setting Agent suggestions; only explicit version-checked adoption writes."""
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.setting_proposal_contracts import SettingChangeProposal, SettingProposalSourceTask
from app.api.schemas_setting_collaboration import SettingCollaborationSendRequest, SettingCollectionCollaborationSendRequest, SettingProposalAcceptanceRequest
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.llm.errors import ProviderInvalidOutputError
from app.models import Document, DocumentVersion, SettingCollection, SettingCollaboration, SettingCollaborationMessage
from app.services.document_service import DocumentService
from app.services.setting_collection_service import SettingCollectionService


def _document_category(document: Document) -> str:
    category = (document.metadata_ or {}).get("category")
    if category in ("world", "setting"):
        return category
    return "world" if document.type in {"world_overview", "power_system", "factions", "geography", "history"} else "setting"


class SettingCollaborationService:
    def __init__(self, session: AsyncSession, collection_id: UUID, document_id: UUID | None = None, actor_id: UUID | None = None):
        self.session, self.collection_id, self.document_id, self.actor_id = session, collection_id, document_id, actor_id

    async def _collection(self, *, lock: bool = False, writable: bool = False, skip_locked: bool = False) -> SettingCollection:
        collection = await SettingCollectionService(self.session).get_setting_collection(self.collection_id, actor_user_id=self.actor_id)
        if lock or writable:
            statement = select(SettingCollection).where(SettingCollection.id == self.collection_id).execution_options(populate_existing=True)
            if lock:
                statement = statement.with_for_update(skip_locked=skip_locked)
            collection = await self.session.scalar(statement)
            if collection is None:
                if skip_locked:
                    raise ConflictError("The setting collection is being modified; retry this suggestion after the author's save completes.")
                raise NotFoundError("Setting collection not found.")
            await SettingCollectionService(self.session).get_setting_collection(self.collection_id, actor_user_id=self.actor_id)
        if writable and collection.status == "archived":
            raise ConflictError("Archived setting collections cannot be modified.")
        return collection

    async def _document(self, document_id: UUID | None = None, *, lock: bool = False, writable: bool = False) -> Document:
        await self._collection()
        target = document_id or self.document_id
        if target is None:
            raise NotFoundError("No document was selected by this proposal.")
        statement = select(Document).options(selectinload(Document.current_version)).where(
            Document.id == target, Document.setting_collection_id == self.collection_id, Document.deleted_at.is_(None),
        ).execution_options(populate_existing=True)
        if lock:
            statement = statement.with_for_update()
        document = await self.session.scalar(statement)
        if document is None:
            raise NotFoundError("Setting document not found in this collection.")
        if writable:
            # Preserve the existing document→collection order without waiting behind
            # a metadata PATCH whose ORM flush already locked the collection first.
            # A recoverable conflict releases this document so the author can finish saving.
            await self._collection(lock=lock, writable=True, skip_locked=lock)
        return document

    async def get(self, *, lock: bool = False) -> SettingCollaboration:
        if self.document_id is None:
            await self._collection()
        else:
            await self._document()
        statement = select(SettingCollaboration).where(
            SettingCollaboration.setting_collection_id == self.collection_id,
            SettingCollaboration.document_id == self.document_id,
        ).execution_options(populate_existing=True)
        if lock:
            statement = statement.with_for_update()
        conversation = await self.session.scalar(statement)
        if conversation is None:
            raise NotFoundError("No setting Agent conversation exists for this document.")
        return conversation

    async def get_or_create(self) -> SettingCollaboration:
        if self.document_id is None:
            await self._collection(lock=True)
            existing = await self.session.scalar(select(SettingCollaboration).where(
                SettingCollaboration.setting_collection_id == self.collection_id,
                SettingCollaboration.document_id.is_(None),
            ))
            if existing is not None:
                return await self.get()
            await self._collection(lock=True, writable=True)
            self.session.add(SettingCollaboration(setting_collection_id=self.collection_id, document_id=None, agent_role="lore_agent"))
            await self.session.commit()
            return await self.get()
        document = await self._document(lock=True)
        existing = await self.session.scalar(select(SettingCollaboration).where(SettingCollaboration.document_id == document.id))
        if existing is not None:
            return await self.get()
        await self._document(lock=True, writable=True)
        agent_role = "worldbuilding_agent" if _document_category(document) == "world" else "lore_agent"
        conversation = SettingCollaboration(setting_collection_id=self.collection_id, document_id=self.document_id, agent_role=agent_role)
        self.session.add(conversation)
        await self.session.commit()
        return await self.get()

    async def send(self, payload: SettingCollaborationSendRequest | SettingCollectionCollaborationSendRequest) -> SettingCollaboration:
        conversation = await self.get(lock=True)
        fingerprint = sha256(payload.model_dump_json().encode()).hexdigest()
        for message in conversation.messages:
            if message.client_message_id == payload.client_message_id:
                if message.request_fingerprint != fingerprint:
                    raise ConflictError("This message identity was already used for different content or context.")
                return conversation
        if self.document_id is None:
            if not isinstance(payload, SettingCollectionCollaborationSendRequest):
                raise ValidationError("Collection collaboration requires a collection-scoped message.")
            return await self._send_collection(conversation, payload)
        if not isinstance(payload, SettingCollaborationSendRequest):
            raise ValidationError("Document collaboration requires its exact document version.")
        document = await self._document(writable=True)
        if document.current_version_id != payload.base_version_id:
            raise ConflictError("The document version changed; compare against its current text before requesting again.")
        body = await DocumentService(self.session).read_version_content(document.id, payload.base_version_id)
        if payload.draft_content != body:
            raise ConflictError("Save the current draft before requesting a version-bound suggestion.")
        context = {"collection_id": str(self.collection_id), "document_id": str(document.id),
                   "base_version_id": str(payload.base_version_id), "title": document.title or "",
                   "category": _document_category(document),
                   "agent_role": conversation.agent_role, "body": body,
                   "selection": payload.selection.model_dump() if payload.selection else None}
        history = [{"role": message.role, "content": message.content} for message in conversation.messages]
        # No partial fake conversation is committed when the provider fails.
        from app.agents.setting_collaboration import generate_setting_collaboration
        reply, provenance = await generate_setting_collaboration(payload.content, context, history)
        suggestion = reply.suggestion
        proposal = SettingChangeProposal(
            id=uuid4(), setting_collection_id=self.collection_id, target_document_id=document.id,
            base_version_id=payload.base_version_id, title=(document.title or "未命名条目")[:100],
            category=str(context["category"])[:50], proposed_content=suggestion.proposed_content,
            reason=suggestion.reason, source_task=SettingProposalSourceTask(agent_role=conversation.agent_role),
        ) if suggestion else None
        return await self._record_reply(conversation, payload, reply, {**provenance, "base_version_id": str(payload.base_version_id)}, context, proposal,
                                        body=body, title=context["title"])

    async def _record_reply(self, conversation, payload, reply, provenance, context, proposal, *, body: str, title: str, snapshot: dict | None = None):
        self.session.add_all([
            SettingCollaborationMessage(conversation_id=conversation.id, role="user", content=payload.content,
                                        client_message_id=payload.client_message_id, request_fingerprint=sha256(payload.model_dump_json().encode()).hexdigest(),
                                        context=context, created_at=datetime.now(UTC)),
            SettingCollaborationMessage(conversation_id=conversation.id, role="assistant", content=reply.reply,
                                        proposal_id=proposal.id if proposal else None,
                                        proposal=proposal.to_dict() if proposal else None,
                                        proposal_status="pending" if proposal else None,
                                        base_content=body if proposal else None, base_title=title if proposal else None,
                                        context=snapshot, provenance=provenance, created_at=datetime.now(UTC)),
        ])
        conversation.updated_at = datetime.now(UTC)
        await self.session.commit()
        return await self.get()

    async def _snapshot(self, *, bodies: bool) -> list[dict]:
        documents = list(await self.session.scalars(select(Document).options(selectinload(Document.current_version)).where(
            Document.setting_collection_id == self.collection_id, Document.deleted_at.is_(None),
        ).order_by(Document.id).execution_options(populate_existing=True)))
        result, size = [], 0
        for document in documents:
            if document.current_version_id is None or document.current_version is None:
                raise ConflictError("A setting document has no saved version; reconcile it before collaboration.")
            item = {"id": str(document.id), "version_id": str(document.current_version_id), "title": document.title or "", "category": _document_category(document),
                    "content_hash": document.current_version.content_hash}
            if bodies:
                body = await DocumentService(self.session).read_version_content(document.id, document.current_version_id)
                from app.workspace.hashing import sha256_content
                if sha256_content(body.replace("\r\n", "\n").replace("\r", "\n")) != document.current_version.content_hash:
                    raise ConflictError("A setting snapshot no longer matches its saved hash.")
                item["body"] = body
                size += len(json.dumps(item, ensure_ascii=False))
                if size > 1800000:
                    raise ValidationError("The complete collection exceeds the Agent context limit; no partial collection was sent.")
            result.append(item)
        return result

    async def _send_collection(self, conversation, payload: SettingCollectionCollaborationSendRequest):
        collection = await self._collection(writable=True)
        documents = await self._snapshot(bodies=True)
        context = {"scope": "collection", "collection_id": str(self.collection_id), "title": collection.title,
                   "revision": collection.revision, "agent_role": conversation.agent_role, "documents": documents}
        from app.agents.setting_collaboration import generate_setting_collection_collaboration
        history = [{"role": message.role, "content": message.content} for message in conversation.messages]
        reply, provenance = await generate_setting_collection_collaboration(payload.content, context, history)
        suggestion, proposal, base_body, base_title = reply.suggestion, None, "", ""
        if suggestion:
            target = None
            if suggestion.target_document_id is not None:
                target = next((document for document in documents if document["id"] == suggestion.target_document_id), None)
                if target is None:
                    raise ProviderInvalidOutputError()
                base_body, base_title = target["body"], target["title"]
            proposal = SettingChangeProposal(
                id=uuid4(), setting_collection_id=self.collection_id,
                target_document_id=UUID(target["id"]) if target else None,
                base_version_id=UUID(target["version_id"]) if target else None,
                title=(base_title or "未命名条目")[:100] if target else suggestion.title,
                category=target["category"] if target else suggestion.category,
                proposed_content=suggestion.proposed_content, reason=suggestion.reason,
                source_task=SettingProposalSourceTask(agent_role=conversation.agent_role),
            )
        snapshot = {"scope": "collection", "title": collection.title, "documents": [{key: value for key, value in document.items() if key != "body"} for document in documents]}
        provenance = {**provenance, "input_documents": snapshot["documents"]}
        return await self._record_reply(conversation, payload, reply, provenance, context, proposal,
                                        body=base_body, title=base_title, snapshot=snapshot)

    async def _check_snapshot(self, message: SettingCollaborationMessage):
        if self.document_id is None:
            snapshot = message.context
            collection = await self._collection()
            if (not isinstance(snapshot, dict) or snapshot.get("scope") != "collection"
                    or snapshot.get("title") != collection.title or snapshot.get("documents") != await self._snapshot(bodies=False)):
                raise ConflictError("The collection changed; preserve this suggestion and compare against its current documents.")

    async def _proposal(self, conversation_id: UUID, proposal_id: UUID) -> SettingCollaborationMessage:
        message = await self.session.scalar(select(SettingCollaborationMessage).where(
            SettingCollaborationMessage.conversation_id == conversation_id,
            SettingCollaborationMessage.proposal_id == proposal_id,
        ).with_for_update().execution_options(populate_existing=True))
        if message is None:
            raise NotFoundError("Suggestion not found in this setting Agent conversation.")
        return message

    async def accept(self, proposal_id: UUID, payload: SettingProposalAcceptanceRequest) -> tuple[SettingCollaboration, Document, str]:
        conversation = await self.get(lock=True)
        message = await self._proposal(conversation.id, proposal_id)
        if message.proposal_status == "accepted":
            # A response may have been lost after commit; repeating adoption must not write again.
            document_id = await self.session.scalar(select(DocumentVersion.document_id).where(DocumentVersion.id == message.accepted_version_id))
            if document_id is None or self.document_id is not None and document_id != self.document_id:
                raise ConflictError("The accepted suggestion's document could not be reconciled.")
            document = await self._document(document_id, lock=True, writable=True)
            if document.current_version_id != message.accepted_version_id:
                raise ConflictError("This suggestion was already adopted, then the document changed; refresh its accepted history without overwriting newer text.")
            current = await DocumentService(self.session).read_current_content(document.id)
            return conversation, document, current.content
        if message.proposal_status != "pending":
            raise ConflictError("A discarded suggestion cannot be adopted.")
        proposal = SettingChangeProposal.from_dict(message.proposal)
        if proposal.id != proposal_id or proposal.setting_collection_id != self.collection_id:
            raise ConflictError("The suggestion does not match this collection decision.")
        if self.document_id is not None and proposal.target_document_id != self.document_id:
            raise ConflictError("This document conversation cannot target a different document.")
        if proposal.target_document_id is None:
            await self._collection(lock=True, writable=True)
            document = None
        else:
            document = await self._document(proposal.target_document_id, lock=True, writable=True)
        if (payload.expected_current_version_id != proposal.base_version_id
                or payload.expected_draft_content != message.base_content
                or payload.expected_title != message.base_title
                or document is not None and (document.current_version_id != proposal.base_version_id or (document.title or "") != message.base_title)):
            raise ConflictError("The author changed the document; preserve both versions and compare again.")
        if document is not None:
            current = await DocumentService(self.session).read_version_content(document.id, proposal.base_version_id)
            if current != message.base_content:
                raise ConflictError("The suggestion snapshot no longer matches the document.")
        await self._check_snapshot(message)
        document, writes = await SettingCollectionService(self.session).stage_setting_change_proposal(
            self.collection_id, proposal, actor_user_id=self.actor_id,
        )
        message.proposal_status = "accepted"
        message.proposal = {**message.proposal, "status": "accepted"}
        message.accepted_version_id = document.current_version_id
        conversation.updated_at = datetime.now(UTC)
        await DocumentService(self.session).commit_staged_document(document, writes)
        return await self.get(), await self._document(document.id), proposal.proposed_content

    async def discard(self, proposal_id: UUID) -> SettingCollaboration:
        conversation = await self.get(lock=True)
        if self.document_id is None:
            await self._collection(lock=True, writable=True)
        else:
            await self._document(lock=True, writable=True)
        message = await self._proposal(conversation.id, proposal_id)
        if message.proposal_status == "accepted":
            raise ConflictError("An adopted suggestion cannot be discarded.")
        message.proposal_status = "rejected"
        message.proposal = {**message.proposal, "status": "rejected"}
        conversation.updated_at = datetime.now(UTC)
        await self.session.commit()
        return await self.get()
