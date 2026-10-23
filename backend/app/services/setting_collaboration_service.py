"""Persist setting Agent suggestions; only explicit version-checked adoption writes."""
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

from pydantic import ValidationError as ModelValidationError
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from app.agents.setting_proposal_contracts import SettingChangeProposal, SettingProposalSourceTask
from app.api.schemas_setting_collaboration import SettingCollaborationHistoryResponse, SettingCollaborationSendRequest, SettingCollaborationSummary, SettingCollectionCollaborationSendRequest, SettingProposalAcceptanceRequest
from app.core.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.llm.errors import ProviderInvalidOutputError
from app.models import Document, DocumentVersion, SettingCollection, SettingCollaboration, SettingCollaborationAttempt, SettingCollaborationMessage
from app.services.document_service import DocumentService
from app.services.setting_collection_service import SettingCollectionService


def _document_category(document: Document) -> str:
    category = (document.metadata_ or {}).get("category")
    if category in ("world", "setting"):
        return category
    return "world" if document.type in {"world_overview", "power_system", "factions", "geography", "history"} else "setting"


class SettingAgentAttemptPendingError(ConflictError):
    code = "setting_agent_attempt_pending"
    default_message = "Inspect or explicitly finish the previous setting Agent request before sending a new identity."


class SettingAgentAttemptUnconfirmedError(ConflictError):
    code = "setting_agent_attempt_unconfirmed"
    default_message = "The saved setting Agent exchange cannot be confirmed. No model call was repeated."


class _DispatchNotClaimed(Exception):
    pass


_ACTIVE_ATTEMPTS = ("prepared", "dispatched", "result_saved")


class SettingCollaborationService:
    def __init__(self, session: AsyncSession, collection_id: UUID, document_id: UUID | None = None, actor_id: UUID | None = None, *, conversation_id: UUID | None = None):
        self.session, self.collection_id, self.document_id, self.actor_id = session, collection_id, document_id, actor_id
        self.conversation_id = conversation_id

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
        if self.conversation_id is not None:
            statement = statement.where(SettingCollaboration.id == self.conversation_id)
        elif self.document_id is None:
            statement = statement.where(SettingCollaboration.is_default.is_(True))
        if lock:
            statement = statement.with_for_update()
        conversation = await self.session.scalar(statement)
        if conversation is None:
            raise NotFoundError("No setting Agent conversation exists in this scope.")
        recent = select(SettingCollaborationAttempt.id).where(
            SettingCollaborationAttempt.conversation_id == conversation.id,
        ).order_by(SettingCollaborationAttempt.created_at.desc(), SettingCollaborationAttempt.id.desc()).limit(10)
        # Do not eagerly load the unbounded attempt archive or frozen input bodies.
        conversation.attempts = list(await self.session.scalars(select(SettingCollaborationAttempt).options(
            defer(SettingCollaborationAttempt.context), defer(SettingCollaborationAttempt.history),
        ).where(
            SettingCollaborationAttempt.conversation_id == conversation.id,
            or_(SettingCollaborationAttempt.status.in_(_ACTIVE_ATTEMPTS), SettingCollaborationAttempt.id.in_(recent)),
        ).order_by(SettingCollaborationAttempt.created_at.desc(), SettingCollaborationAttempt.id.desc()).execution_options(populate_existing=True)))
        return conversation

    async def get_or_create(self) -> SettingCollaboration:
        if self.conversation_id is not None:
            # Explicit history operations must never create or fall back to default.
            return await self.get()
        if self.document_id is None:
            await self._collection(lock=True)
            existing = await self.session.scalar(select(SettingCollaboration).where(
                SettingCollaboration.setting_collection_id == self.collection_id,
                SettingCollaboration.document_id.is_(None),
                SettingCollaboration.is_default.is_(True),
            ))
            if existing is not None:
                return await self.get()
            await self._collection(lock=True, writable=True)
            self.session.add(SettingCollaboration(setting_collection_id=self.collection_id, document_id=None, agent_role="lore_agent", is_default=True))
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

    async def create_conversation(self, client_request_id: UUID) -> SettingCollaboration:
        await self._collection(lock=True)
        existing = await self.session.scalar(select(SettingCollaboration.id).where(
            SettingCollaboration.setting_collection_id == self.collection_id,
            SettingCollaboration.document_id.is_(None),
            SettingCollaboration.client_request_id == client_request_id,
        ))
        if existing is not None:
            self.conversation_id = existing
            await self.session.commit()
            return await self.get()
        await self._collection(writable=True)
        conversation = SettingCollaboration(
            setting_collection_id=self.collection_id, document_id=None, agent_role="lore_agent",
            is_default=False, client_request_id=client_request_id,
        )
        self.session.add(conversation)
        await self.session.commit()
        self.conversation_id = conversation.id
        return await self.get()

    async def list_conversations(self, *, limit: int = 30, offset: int = 0) -> SettingCollaborationHistoryResponse:
        await self._collection()
        conversation, message, attempt = SettingCollaboration, SettingCollaborationMessage, SettingCollaborationAttempt
        message_scope = message.conversation_id == conversation.id
        has_messages = select(message.id).where(message_scope).correlate(conversation).exists()
        has_attempts = select(attempt.id).where(attempt.conversation_id == conversation.id).correlate(conversation).exists()
        latest_message = select(func.substr(message.content, 1, 160)).where(message_scope).order_by(message.created_at.desc(), message.id.desc()).limit(1).correlate(conversation).scalar_subquery()
        latest_message_at = select(message.created_at).where(message_scope).order_by(message.created_at.desc(), message.id.desc()).limit(1).correlate(conversation).scalar_subquery()
        attempt_scope = (attempt.conversation_id == conversation.id, attempt.status != "completed")
        latest_request = select(func.substr(attempt.request["content"].astext, 1, 160)).where(*attempt_scope).order_by(attempt.created_at.desc(), attempt.id.desc()).limit(1).correlate(conversation).scalar_subquery()
        latest_request_at = select(attempt.created_at).where(*attempt_scope).order_by(attempt.created_at.desc(), attempt.id.desc()).limit(1).correlate(conversation).scalar_subquery()
        preview = func.coalesce(case((or_(latest_message_at.is_(None), latest_request_at > latest_message_at), latest_request), else_=latest_message), "")
        count = select(func.count(message.id)).where(message_scope).correlate(conversation).scalar_subquery()
        rows = (await self.session.execute(select(
            conversation.id, conversation.title, conversation.created_at, conversation.updated_at, conversation.is_default,
            count.label("message_count"), preview.label("preview"),
        ).where(
            conversation.setting_collection_id == self.collection_id, conversation.document_id.is_(None),
            or_(has_messages, has_attempts),
        )
            .order_by(conversation.updated_at.desc(), conversation.id.desc()).offset(offset).limit(limit + 1))).mappings().all()
        return SettingCollaborationHistoryResponse(
            items=[SettingCollaborationSummary.model_validate(row) for row in rows[:limit]],
            next_offset=offset + limit if len(rows) > limit else None,
        )

    async def _request_title(self, conversation, content):
        # Existing identities, not a title sentinel, decide whether this is first.
        # A genuine first question may itself be exactly “新对话”.
        if not any(message.role == "user" for message in conversation.messages) and await self.session.scalar(
            select(SettingCollaborationAttempt.id).where(SettingCollaborationAttempt.conversation_id == conversation.id).limit(1)
        ) is None:
            conversation.title = " ".join(content.split())[:48]
        conversation.updated_at = datetime.now(UTC)

    async def send(self, payload: SettingCollaborationSendRequest | SettingCollectionCollaborationSendRequest) -> SettingCollaboration:
        conversation = await self.get(lock=True)
        fingerprint = sha256(payload.model_dump_json().encode()).hexdigest()
        attempt = await self.session.scalar(select(SettingCollaborationAttempt).where(
            SettingCollaborationAttempt.conversation_id == conversation.id,
            SettingCollaborationAttempt.client_message_id == payload.client_message_id,
        ).execution_options(populate_existing=True))
        if attempt is not None:
            self._same_request(attempt, fingerprint)
            return await self._resume_locked(conversation, attempt)
        for index, message in enumerate(conversation.messages):
            if message.client_message_id == payload.client_message_id:
                if message.request_fingerprint != fingerprint:
                    raise ConflictError("This message identity was already used for different content or context.")
                # Old records have no attempt reference. Only an intact adjacent pair
                # can be replayed; an orphan user message must never trigger a call.
                following = conversation.messages[index + 1:index + 2]
                if message.role != "user" or not following or following[0].role != "assistant" or following[0].attempt_id is not None:
                    raise SettingAgentAttemptUnconfirmedError()
                await self.session.commit()
                return conversation
        if any(item.status in _ACTIVE_ATTEMPTS for item in conversation.attempts):
            raise SettingAgentAttemptPendingError()
        context = await self._prepare_context(conversation, payload)
        await self._request_title(conversation, payload.content)
        attempt = SettingCollaborationAttempt(
            conversation_id=conversation.id, client_message_id=payload.client_message_id,
            request_fingerprint=fingerprint, request=payload.model_dump(mode="json"),
            context=context, history=[{"role": message.role, "content": message.content[:4000]} for message in conversation.messages[-12:]],
            status="prepared",
        )
        self.session.add(attempt)
        await self.session.commit()
        return await self.resume_attempt(attempt.id)

    async def _prepare_context(self, conversation, payload) -> dict:
        if self.document_id is None:
            if not isinstance(payload, SettingCollectionCollaborationSendRequest):
                raise ValidationError("Collection collaboration requires a collection-scoped message.")
            collection = await self._collection(writable=True)
            documents = await self._snapshot(bodies=True)
            return {"scope": "collection", "collection_id": str(self.collection_id), "title": collection.title,
                    "revision": collection.revision, "agent_role": conversation.agent_role, "documents": documents}
        if not isinstance(payload, SettingCollaborationSendRequest):
            raise ValidationError("Document collaboration requires its exact document version.")
        document = await self._document(writable=True)
        if document.current_version_id != payload.base_version_id:
            raise ConflictError("The document version changed; compare against its current text before requesting again.")
        body = await DocumentService(self.session).read_version_content(document.id, payload.base_version_id)
        if payload.draft_content != body:
            raise ConflictError("Save the current draft before requesting a version-bound suggestion.")
        return {"collection_id": str(self.collection_id), "document_id": str(document.id),
                   "base_version_id": str(payload.base_version_id), "title": document.title or "",
                   "category": _document_category(document),
                   "agent_role": conversation.agent_role, "body": body,
                   "selection": payload.selection.model_dump() if payload.selection else None}

    @staticmethod
    def _same_request(attempt, fingerprint):
        if attempt.request_fingerprint != fingerprint:
            raise ConflictError("This message identity was already used for different content or context.")

    async def _attempt(self, conversation, attempt_id):
        attempt = await self.session.scalar(select(SettingCollaborationAttempt).where(
            SettingCollaborationAttempt.conversation_id == conversation.id,
            SettingCollaborationAttempt.id == attempt_id,
        ).execution_options(populate_existing=True))
        if attempt is None:
            raise NotFoundError("Setting Agent request not found in this conversation.")
        return attempt

    async def inspect_attempt(self, attempt_id: UUID):
        return await self._attempt(await self.get(), attempt_id)

    async def finish_attempt(self, payload):
        # This also fences a send whose first HTTP request has not arrived yet.
        # get_or_create validates the exact collection/document and its ownership.
        await self.get_or_create()
        # An existing conversation leaves its collection/document creation lock
        # held. Release it before taking the conversation lock used by adoption.
        await self.session.commit()
        conversation = await self.get(lock=True)
        fingerprint = sha256(payload.model_dump_json().encode()).hexdigest()
        attempt = await self.session.scalar(select(SettingCollaborationAttempt).where(
            SettingCollaborationAttempt.conversation_id == conversation.id,
            SettingCollaborationAttempt.client_message_id == payload.client_message_id,
        ).execution_options(populate_existing=True))
        if attempt is None:
            # Explicitly ending an old journal is allowed, even if old messages
            # cannot prove a complete ACK. Keep them intact and fence this exact
            # request; never invent a completed attempt or discard the author's input.
            legacy = next((message for message in conversation.messages if message.client_message_id == payload.client_message_id), None)
            if legacy is not None and legacy.request_fingerprint != fingerprint:
                raise ConflictError("This message identity was already used for different content or context.")
            attempt = SettingCollaborationAttempt(
                conversation_id=conversation.id, client_message_id=payload.client_message_id,
                request_fingerprint=fingerprint, request=payload.model_dump(mode="json"),
                context={}, history=[], status="finished",
            )
            await self._request_title(conversation, payload.content)
            self.session.add(attempt)
        else:
            self._same_request(attempt, fingerprint)
            if attempt.status == "completed":
                self._complete_pair(conversation, attempt)
            else:
                attempt.status, attempt.updated_at = "finished", datetime.now(UTC)
                conversation.updated_at = datetime.now(UTC)
        await self.session.commit()
        return self._include_attempt(await self.get(), attempt)

    @staticmethod
    def _include_attempt(conversation, attempt):
        if all(item.id != attempt.id for item in conversation.attempts):
            conversation.attempts.append(attempt)
        return conversation

    @staticmethod
    def _complete_pair(conversation, attempt):
        pair = {message.id: message for message in conversation.messages if message.attempt_id == attempt.id}
        user, assistant = pair.get(attempt.user_message_id), pair.get(attempt.assistant_message_id)
        if (len(pair) != 2 or user is None or assistant is None or user.role != "user" or assistant.role != "assistant"
                or user.client_message_id != attempt.client_message_id or user.content != attempt.request["content"]
                or user.request_fingerprint != attempt.request_fingerprint):
            raise SettingAgentAttemptUnconfirmedError()

    async def resume_attempt(self, attempt_id: UUID):
        conversation = await self.get(lock=True)
        return await self._resume_locked(conversation, await self._attempt(conversation, attempt_id))

    async def _resume_locked(self, conversation, attempt):
        self._include_attempt(conversation, attempt)
        if attempt.status == "completed":
            self._complete_pair(conversation, attempt)
        if attempt.status in {"dispatched", "completed", "finished"}:
            await self.session.commit()
            return conversation
        if attempt.status == "result_saved":
            try:
                return await self._materialize(conversation, attempt)
            except AppError as error:
                attempt_id = attempt.id
                await self.session.rollback()
                current = await self.get(lock=True)
                saved = await self._attempt(current, attempt_id)
                if saved.status == "result_saved":
                    saved.error_code, saved.updated_at = error.code, datetime.now(UTC)
                await self.session.commit()
                raise
        await self._collection(writable=True)
        attempt_id, content, context, history = attempt.id, attempt.content, attempt.context, attempt.history
        await self.session.commit()
        owns_dispatch = False

        async def before_dispatch(provenance):
            nonlocal owns_dispatch
            current = await self.get(lock=True)
            saved = await self._attempt(current, attempt_id)
            if saved.status != "prepared":
                raise _DispatchNotClaimed()
            await self._collection(writable=True)
            saved.status, saved.error_code = "dispatched", None
            saved.provenance, saved.updated_at = provenance, datetime.now(UTC)
            current.updated_at = datetime.now(UTC)
            # Failure or cancellation here must prevent the external HTTP call.
            await self.session.commit()
            owns_dispatch = True

        try:
            from app.agents.setting_collaboration import generate_setting_collaboration, generate_setting_collection_collaboration
            generate = generate_setting_collection_collaboration if self.document_id is None else generate_setting_collaboration
            reply, provenance = await generate(content, context, history, before_dispatch=before_dispatch)
            if not owns_dispatch:
                raise SettingAgentAttemptUnconfirmedError()
        except Exception as error:
            await self.session.rollback()
            current = await self.get(lock=True)
            saved = await self._attempt(current, attempt_id)
            if saved.status == "prepared" or owns_dispatch and saved.status == "dispatched":
                saved.error_code = error.code if isinstance(error, AppError) else "internal_server_error"
                saved.updated_at = datetime.now(UTC)
                await self.session.commit()
                raise
            await self.session.commit()
            return current

        current = await self.get(lock=True)
        saved = await self._attempt(current, attempt_id)
        if saved.status not in {"dispatched", "finished"}:
            raise SettingAgentAttemptUnconfirmedError()
        # Independently checkpoint a valid provider result before any message write.
        saved.result, saved.provenance = reply.model_dump(mode="json"), provenance
        saved.error_code, saved.updated_at = None, datetime.now(UTC)
        try:
            self._result_fields(current, saved)
        except (ProviderInvalidOutputError, ModelValidationError):
            # Keep unusable output as audit evidence, never advertise that it can
            # be materialized or allow this dispatch identity to generate again.
            saved.error_code = "provider_invalid_output"
            await self.session.commit()
            raise ProviderInvalidOutputError() from None
        if saved.status != "finished":
            saved.status = "result_saved"
        await self.session.commit()
        return await self.resume_attempt(attempt_id)

    async def _materialize(self, conversation, attempt):
        await self._collection(writable=True)
        reply, provenance, proposal, body, title, snapshot = self._result_fields(conversation, attempt)
        return await self._record_reply(conversation, attempt, reply, provenance, proposal, body=body, title=title, snapshot=snapshot)

    def _result_fields(self, conversation, attempt):
        """Validate only frozen inputs/output here; current prose is checked on adoption."""
        from app.agents.setting_collaboration import SettingCollaborationReply, SettingCollectionCollaborationReply
        context, provenance = attempt.context, dict(attempt.provenance or {})
        collection_scope = self.document_id is None
        schema = SettingCollectionCollaborationReply if collection_scope else SettingCollaborationReply
        reply = schema.model_validate(attempt.result)
        suggestion, proposal, snapshot = reply.suggestion, None, None
        body, title = ("", "") if collection_scope else (context["body"], context["title"])
        if collection_scope:
            documents = context["documents"]
            target = None
            if suggestion and suggestion.target_document_id is not None:
                target = next((document for document in documents if document["id"] == suggestion.target_document_id), None)
                if target is None:
                    raise ProviderInvalidOutputError()
                body, title = target["body"], target["title"]
            snapshot = {"scope": "collection", "title": context["title"], "documents": [{key: value for key, value in document.items() if key != "body"} for document in documents]}
            provenance["input_documents"] = snapshot["documents"]
            target_id = UUID(target["id"]) if target else None
            base_version_id = UUID(target["version_id"]) if target else None
            category = target["category"] if target else suggestion.category if suggestion else "setting"
        else:
            target_id, base_version_id, category = self.document_id, UUID(context["base_version_id"]), context["category"]
            provenance["base_version_id"] = context["base_version_id"]
        if suggestion:
            proposal = SettingChangeProposal(
                id=attempt.id, setting_collection_id=self.collection_id, target_document_id=target_id,
                base_version_id=base_version_id, title=(title or "未命名条目")[:100] if target_id else suggestion.title,
                category=category, proposed_content=suggestion.proposed_content, reason=suggestion.reason,
                source_task=SettingProposalSourceTask(agent_role=conversation.agent_role),
                created_at=attempt.created_at,
            )
        return reply, provenance, proposal, body, title, snapshot

    async def _record_reply(self, conversation, attempt, reply, provenance, proposal, *, body: str, title: str, snapshot: dict | None = None):
        user_id, assistant_id = uuid4(), uuid4()
        self.session.add_all([
            SettingCollaborationMessage(id=user_id, conversation_id=conversation.id, role="user", content=attempt.content,
                                        attempt_id=attempt.id, client_message_id=attempt.client_message_id, request_fingerprint=attempt.request_fingerprint,
                                        context=attempt.context, created_at=datetime.now(UTC)),
            SettingCollaborationMessage(id=assistant_id, conversation_id=conversation.id, role="assistant", content=reply.reply, attempt_id=attempt.id,
                                        proposal_id=proposal.id if proposal else None,
                                        proposal=proposal.to_dict() if proposal else None,
                                        proposal_status="pending" if proposal else None,
                                        base_content=body if proposal else None, base_title=title if proposal else None,
                                        context=snapshot, provenance=provenance, created_at=datetime.now(UTC)),
        ])
        attempt.status, attempt.user_message_id, attempt.assistant_message_id = "completed", user_id, assistant_id
        attempt.error_code, attempt.updated_at = None, datetime.now(UTC)
        conversation.updated_at = datetime.now(UTC)
        await self.session.commit()
        return self._include_attempt(await self.get(), attempt)

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
