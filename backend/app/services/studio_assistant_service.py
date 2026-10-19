"""Service for Studio Assistant conversation lifecycle and messaging."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.studio_assistant import StudioAssistantAgent
from app.api.schemas_studio_assistant import AssistantSendMessageRequest
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import Chapter, Project, SettingCollection, StudioAssistantConversation, StudioAssistantMessage
from app.api.schemas_studio_assistant import SettingAssistantSendRequest
from app.services.setting_collection_service import SettingCollectionService

_CONVERSATION_LOCKS: dict[UUID, asyncio.Lock] = {}


class StudioAssistantService:
    def __init__(self, session: AsyncSession, *, setting_collection_id: UUID | None = None, actor_id: UUID | None = None) -> None:
        self.session = session
        self.setting_collection_id = setting_collection_id
        self.actor_id = actor_id

    def _scope(self, project_id: UUID | None):
        if self.setting_collection_id:
            return StudioAssistantConversation.setting_collection_id == self.setting_collection_id
        return StudioAssistantConversation.project_id == project_id

    async def _verify_project_and_chapter(
        self, project_id: UUID | None, chapter_id: UUID | None
    ) -> None:
        if self.setting_collection_id:
            if project_id or chapter_id:
                raise ValidationError("Collection conversations cannot borrow project context.")
            await SettingCollectionService(self.session).get_setting_collection(self.setting_collection_id, actor_user_id=self.actor_id)
            return
        if project_id is None:
            raise ValidationError("An assistant context is required.")
        project = await self.session.scalar(select(Project).where(Project.id == project_id))
        if project is None:
            raise NotFoundError("Project not found.")
        if chapter_id is not None:
            chapter = await self.session.scalar(
                select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
            )
            if chapter is None:
                raise NotFoundError("Chapter not found in this project.")

    async def get_or_create_conversation(
        self,
        project_id: UUID | None,
        chapter_id: UUID | None = None,
        title: str | None = None,
    ) -> StudioAssistantConversation:
        await self._verify_project_and_chapter(project_id, chapter_id)

        if self.setting_collection_id:
            await self.session.execute(select(SettingCollection.id).where(SettingCollection.id == self.setting_collection_id).with_for_update())

        query = select(StudioAssistantConversation).where(
            self._scope(project_id)
        )
        if chapter_id is not None:
            query = query.where(StudioAssistantConversation.chapter_id == chapter_id)

        query = query.order_by(StudioAssistantConversation.updated_at.desc())
        existing = await self.session.scalar(query)
        if existing is not None:
            return await self.get_conversation(project_id, existing.id)

        conv = StudioAssistantConversation(
            project_id=project_id,
            setting_collection_id=self.setting_collection_id,
            chapter_id=chapter_id,
            title=title or ("Gura 设定对话" if self.setting_collection_id else "Gura 创作对话"),
        )
        self.session.add(conv)
        await self.session.commit()

        # Re-fetch with loaded messages relationship
        return await self.get_conversation(project_id, conv.id)

    async def list_conversations(
        self,
        project_id: UUID | None,
        chapter_id: UUID | None = None,
    ) -> list[StudioAssistantConversation]:
        await self._verify_project_and_chapter(project_id, chapter_id)

        query = select(StudioAssistantConversation).where(
            self._scope(project_id)
        )
        if chapter_id is not None:
            query = query.where(StudioAssistantConversation.chapter_id == chapter_id)

        query = query.order_by(StudioAssistantConversation.updated_at.desc())
        return list(await self.session.scalars(query))

    async def get_conversation(
        self,
        project_id: UUID | None,
        conversation_id: UUID,
        *,
        lock: bool = False,
    ) -> StudioAssistantConversation:
        if self.setting_collection_id:
            await self._verify_project_and_chapter(project_id, None)
        statement = (
            select(StudioAssistantConversation)
            .where(
                StudioAssistantConversation.id == conversation_id,
                self._scope(project_id),
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        conv = await self.session.scalar(statement)
        if conv is None:
            raise NotFoundError("Assistant conversation not found.")
        return conv

    async def send_message(
        self,
        project_id: UUID | None,
        conversation_id: UUID,
        payload: AssistantSendMessageRequest | SettingAssistantSendRequest,
    ) -> StudioAssistantConversation:
        conversation_lock = _CONVERSATION_LOCKS.setdefault(conversation_id, asyncio.Lock())
        async with conversation_lock:
            conv = await self.get_conversation(project_id, conversation_id, lock=True)
            target_chapter = getattr(payload, 'chapter_id', None) or conv.chapter_id
            if target_chapter is not None:
                chapter = await self.session.scalar(
                    select(Chapter).where(Chapter.id == target_chapter, Chapter.project_id == project_id)
                )
                if chapter is None:
                    raise ValidationError("Specified chapter does not belong to this project.")

            # 1. Fetch prior messages for conversation context & deduplication check
            history_msgs = list(
                await self.session.scalars(
                    select(StudioAssistantMessage)
                    .where(StudioAssistantMessage.conversation_id == conv.id)
                    .order_by(StudioAssistantMessage.created_at.asc())
                )
            )
            if payload.client_message_id:
                for m in history_msgs:
                    if m.role == "user" and m.tool_calls:
                        for tc in m.tool_calls:
                            if isinstance(tc, dict) and tc.get("client_message_id") == payload.client_message_id:
                                if self.setting_collection_id:
                                    original = tc.get("context", {})
                                    if (m.content != payload.content
                                            or original.get("document_id") != (str(payload.document_id) if payload.document_id else None)
                                            or original.get("document_version_id") != (str(payload.document_version_id) if payload.document_version_id else None)):
                                        raise ConflictError("This message identity was already used for different text or context.")
                                return await self.get_conversation(project_id, conv.id)

            history = [{"role": m.role, "content": m.content} for m in history_msgs]

            # 2. Save user message with optional client_message_id metadata
            user_tool_calls = [{"client_message_id": payload.client_message_id}] if payload.client_message_id else None
            context = None
            if self.setting_collection_id:
                from app.agents.setting_assistant import setting_assistant_context
                context = await setting_assistant_context(self.session, self.setting_collection_id, payload)
                user_tool_calls[0]["context"] = {key: value for key, value in context.items() if key != "body"}
            user_msg = StudioAssistantMessage(
                conversation_id=conv.id,
                role="user",
                content=payload.content,
                tool_calls=user_tool_calls,
                **({"created_at": datetime.now(UTC)} if self.setting_collection_id else {}),
            )
            self.session.add(user_msg)
            await self.session.flush()

            # 3. Generate assistant response with bounded tools & history
            if context is not None:
                from app.agents.setting_assistant import generate_setting_reply
                reply = await generate_setting_reply(payload.content, context, history)
                tool_calls, tool_results = [], []
            else:
                agent = StudioAssistantAgent(self.session, project_id)
                reply, tool_calls, tool_results = await agent.generate_reply(
                    user_message=payload.content, chapter_id=target_chapter,
                    current_view=payload.current_view, history=history,
                )

            # 4. Save assistant message
            assistant_msg = StudioAssistantMessage(
                conversation_id=conv.id,
                role="assistant",
                content=reply,
                tool_calls=tool_calls if tool_calls else None,
                tool_results=tool_results if tool_results else None,
                **({"created_at": datetime.now(UTC)} if self.setting_collection_id else {}),
            )
            self.session.add(assistant_msg)
            await self.session.commit()

            # 5. Return refreshed conversation
            return await self.get_conversation(project_id, conv.id)
