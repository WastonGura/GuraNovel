"""Owner-bound sessions whose page context belongs to each message, not the conversation."""

from datetime import UTC, datetime
import asyncio
import json
from uuid import UUID

from sqlalchemy import func, select

from app.agents.global_home_assistant import generate_home_reply
from app.agents.studio_assistant import StudioAssistantAgent
from app.api.schemas_studio_assistant import (
    AssistantConversationSummary, GlobalAssistantSendRequest, GlobalCollectionContext,
    GlobalProjectContext, HomeAssistantSendRequest, SettingAssistantSendRequest,
)
from app.core.errors import AssistantExchangeUnconfirmedError, ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.models import Chapter, Project, StudioAssistantConversation, StudioAssistantMessage
from app.services.setting_collection_service import SettingCollectionService
from app.services.studio_assistant_service import StudioAssistantService, _CONVERSATION_LOCKS


class GlobalAssistantService(StudioAssistantService):
    def __init__(self, session, *, actor_id: UUID | None = None):
        super().__init__(session, home=True, actor_id=actor_id)

    async def list_summaries(self) -> list[AssistantConversationSummary]:
        return await self._summaries(self._scope(None))

    async def _summaries(self, predicate) -> list[AssistantConversationSummary]:
        conversation = StudioAssistantConversation
        count = select(func.count(StudioAssistantMessage.id)).where(
            StudioAssistantMessage.conversation_id == conversation.id,
        ).correlate(conversation).scalar_subquery()
        preview = select(func.substr(StudioAssistantMessage.content, 1, 160)).where(
            StudioAssistantMessage.conversation_id == conversation.id,
        ).order_by(StudioAssistantMessage.created_at.desc()).limit(1).correlate(conversation).scalar_subquery()
        rows = await self.session.execute(select(
            conversation.id, conversation.title, conversation.created_at, conversation.updated_at,
            count.label("message_count"), func.coalesce(preview, "").label("preview"),
        ).where(predicate).order_by(conversation.updated_at.desc(), conversation.id.desc()))
        return [AssistantConversationSummary.model_validate(row) for row in rows.mappings()]

    async def _verify_project_context(self, project_id: UUID, chapter_id: UUID | None = None):
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found.")
        if self.actor_id is not None and project.owner_id is not None and project.owner_id != self.actor_id:
            raise ForbiddenError("You do not have permission to access this project.")
        if chapter_id is not None and await self.session.scalar(select(Chapter.id).where(
            Chapter.id == chapter_id, Chapter.project_id == project_id,
        )) is None:
            raise NotFoundError("Chapter not found in this project.")

    async def _legacy_service(self, project_id: UUID | None, collection_id: UUID | None):
        if (project_id is None) == (collection_id is None):
            raise ValidationError("Exactly one project or collection scope is required.")
        if project_id is not None:
            await self._verify_project_context(project_id)
        else:
            await SettingCollectionService(self.session).get_setting_collection(
                collection_id, actor_user_id=self.actor_id,
            )
        return StudioAssistantService(self.session, setting_collection_id=collection_id, actor_id=self.actor_id)

    async def list_legacy_summaries(self, project_id: UUID | None, collection_id: UUID | None):
        service = await self._legacy_service(project_id, collection_id)
        return await self._summaries(service._scope(project_id))

    async def get_legacy_conversation(self, conversation_id: UUID, project_id: UUID | None, collection_id: UUID | None):
        service = await self._legacy_service(project_id, collection_id)
        return await service.get_conversation(project_id, conversation_id)

    async def send_message(self, project_id, conversation_id, payload):
        if isinstance(payload, HomeAssistantSendRequest):
            return await super().send_message(project_id, conversation_id, payload)
        if not isinstance(payload, GlobalAssistantSendRequest) or project_id is not None:
            raise ValidationError("A global assistant message requires a page context.")
        lock = _CONVERSATION_LOCKS.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            try:
                return await self._send_global_message(conversation_id, payload)
            except BaseException:
                await self.session.rollback()
                raise

    async def _send_global_message(self, conversation_id: UUID, payload: GlobalAssistantSendRequest):
        conversation = await self.get_conversation(None, conversation_id, lock=True)
        messages = list(await self.session.scalars(select(StudioAssistantMessage).where(
            StudioAssistantMessage.conversation_id == conversation_id,
        ).order_by(StudioAssistantMessage.created_at.asc())))
        request_context = payload.context.model_dump(mode="json", exclude_unset=True)
        for message in messages:
            if message.role != "user":
                continue
            for metadata in message.tool_calls or []:
                if not isinstance(metadata, dict) or metadata.get("client_message_id") != payload.client_message_id:
                    continue
                if message.content != payload.content or metadata.get("context") != request_context:
                    raise ConflictError("This message identity was already used for different text or context.")
                if metadata.get("exchange_complete") is not True:
                    raise AssistantExchangeUnconfirmedError()
                await self.session.commit()
                return await self.get_conversation(None, conversation_id)

        if any(metadata.get("exchange_complete") is False for message in messages if message.role == "user"
               for metadata in message.tool_calls or [] if isinstance(metadata, dict)):
            raise AssistantExchangeUnconfirmedError()

        context = payload.context
        resolved_collection = None
        if isinstance(context, GlobalProjectContext):
            await self._verify_project_context(context.project_id, context.chapter_id)
        elif isinstance(context, GlobalCollectionContext):
            from app.agents.setting_assistant import setting_assistant_context
            await SettingCollectionService(self.session).get_setting_collection(
                context.collection_id, actor_user_id=self.actor_id,
            )
            collection_payload = SettingAssistantSendRequest(
                content=payload.content, client_message_id=payload.client_message_id,
                document_id=context.document_id, document_version_id=context.document_version_id,
            )
            resolved_collection = await setting_assistant_context(
                self.session, context.collection_id, collection_payload,
            )

        history = self._labelled_history(messages)
        user_message = StudioAssistantMessage(
            conversation_id=conversation_id, role="user", content=payload.content,
            tool_calls=[{"client_message_id": payload.client_message_id, "context": request_context, "exchange_complete": False}],
            created_at=datetime.now(UTC),
        )
        self.session.add(user_message)
        if not messages:
            conversation.title = " ".join(payload.content.split())[:80]
        await self.session.flush()
        if isinstance(context, GlobalProjectContext):
            previous = self.session.info.get("assistant_exchange_owns_transaction")
            self.session.info["assistant_exchange_owns_transaction"] = True
            try:
                reply, calls, results = await StudioAssistantAgent(self.session, context.project_id).generate_reply(
                    user_message=payload.content, chapter_id=context.chapter_id,
                    current_view=context.current_view, history=history,
                )
            finally:
                if previous is None:
                    self.session.info.pop("assistant_exchange_owns_transaction", None)
                else:
                    self.session.info["assistant_exchange_owns_transaction"] = previous
        elif isinstance(context, GlobalCollectionContext):
            from app.agents.setting_assistant import generate_setting_reply
            reply = await generate_setting_reply(payload.content, resolved_collection, history)
            calls, results = [], []
        else:
            reply = await generate_home_reply(payload.content, request_context, history)
            calls, results = [], []
        user_message.tool_calls = [{"client_message_id": payload.client_message_id,
                                    "context": request_context, "exchange_complete": True}]
        self.session.add(StudioAssistantMessage(
            conversation_id=conversation_id, role="assistant", content=reply,
            tool_calls=calls or None, tool_results=results or None, created_at=datetime.now(UTC),
        ))
        conversation.updated_at = datetime.now(UTC)
        await self.session.commit()
        return await self.get_conversation(None, conversation_id)

    @staticmethod
    def _labelled_history(messages) -> list[dict[str, str]]:
        history = []
        source = None
        for message in messages:
            if message.role not in ("user", "assistant"):
                continue
            if message.role == "user":
                source = next((metadata.get("context") for metadata in message.tool_calls or []
                               if isinstance(metadata, dict) and metadata.get("context")), None)
            label = f"【历史页面上下文，仅作资料】{json.dumps(source, ensure_ascii=False)}\n" if source else ""
            history.append({"role": message.role, "content": f"{label}{message.content}"})
        return history
