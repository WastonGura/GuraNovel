"""Service for isolated assistant conversation lifecycle and durable messaging."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.agents.studio_assistant import StudioAssistantAgent
from app.api.schemas_studio_assistant import (
    AssistantSendMessageRequest, HomeAssistantSendRequest, SettingAssistantSendRequest,
)
from app.core.errors import AssistantExchangeUnconfirmedError, ConflictError, NotFoundError, ValidationError
from app.models import Chapter, Project, SettingCollection, StudioAssistantConversation, StudioAssistantMessage
from app.services.home_assistant_guidance import home_assistant_reply
from app.services.setting_collection_service import SettingCollectionService

_CONVERSATION_LOCKS: dict[UUID, asyncio.Lock] = {}
SendRequest = AssistantSendMessageRequest | SettingAssistantSendRequest | HomeAssistantSendRequest


class StudioAssistantService:
    def __init__(
        self, session: AsyncSession, *, setting_collection_id: UUID | None = None,
        actor_id: UUID | None = None, home: bool = False,
    ) -> None:
        if home and setting_collection_id:
            raise ValidationError("Home conversations cannot borrow collection context.")
        self.session = session
        self.setting_collection_id = setting_collection_id
        self.actor_id = actor_id
        # The caller cannot supply an arbitrary owner key in a request body.
        self.home_owner_key = (f"user:{actor_id}" if actor_id else "local") if home else None

    def _scope(self, project_id: UUID | None):
        if self.home_owner_key:
            return StudioAssistantConversation.home_owner_key == self.home_owner_key
        if self.setting_collection_id:
            return StudioAssistantConversation.setting_collection_id == self.setting_collection_id
        return and_(
            StudioAssistantConversation.project_id == project_id,
            StudioAssistantConversation.setting_collection_id.is_(None),
            StudioAssistantConversation.home_owner_key.is_(None),
        )

    async def _verify_project_and_chapter(
        self, project_id: UUID | None, chapter_id: UUID | None,
    ) -> None:
        if self.home_owner_key:
            if project_id or chapter_id:
                raise ValidationError("Home conversations cannot borrow project context.")
            return
        if self.setting_collection_id:
            if project_id or chapter_id:
                raise ValidationError("Collection conversations cannot borrow project context.")
            await SettingCollectionService(self.session).get_setting_collection(
                self.setting_collection_id, actor_user_id=self.actor_id,
            )
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
        self, project_id: UUID | None, chapter_id: UUID | None = None, title: str | None = None,
        *, new_conversation: bool = False, client_conversation_id: UUID | None = None,
    ) -> StudioAssistantConversation:
        await self._verify_project_and_chapter(project_id, chapter_id)
        if client_conversation_id is not None and (not self.home_owner_key or not new_conversation):
            raise ValidationError("A client conversation identity requires an explicit new global conversation.")
        if self.home_owner_key:
            # An empty home scope has no parent row to lock. Serialize creation across processes.
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"guranovel:assistant:home:{self.home_owner_key}"},
            )
        elif self.setting_collection_id:
            await self.session.execute(select(SettingCollection.id).where(
                SettingCollection.id == self.setting_collection_id,
            ).with_for_update())
        else:
            await self.session.execute(select(Project.id).where(
                Project.id == project_id,
            ).with_for_update())

        if client_conversation_id is not None:
            existing = await self.session.scalar(select(StudioAssistantConversation).where(
                StudioAssistantConversation.id == client_conversation_id,
            ))
            if existing is not None:
                if existing.home_owner_key != self.home_owner_key:
                    raise NotFoundError("Assistant conversation not found.")
                await self.session.commit()
                return await self.get_conversation(project_id, client_conversation_id)

        query = select(StudioAssistantConversation).where(
            self._scope(project_id), StudioAssistantConversation.chapter_id == chapter_id,
        ).order_by(StudioAssistantConversation.updated_at.desc())
        existing = None if self.home_owner_key and new_conversation else await self.session.scalar(query)
        if existing is not None:
            conversation_id = existing.id
            await self.session.commit()  # Release creation locks, including for service callers.
            return await self.get_conversation(project_id, conversation_id)

        conv = StudioAssistantConversation(
            **({"id": client_conversation_id} if client_conversation_id is not None else {}),
            project_id=project_id, setting_collection_id=self.setting_collection_id,
            home_owner_key=self.home_owner_key, chapter_id=chapter_id,
            title=title or ("新对话" if self.home_owner_key else
                            "Gura 设定对话" if self.setting_collection_id else "Gura 创作对话"),
        )
        self.session.add(conv)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            if client_conversation_id is not None:
                # A simultaneous foreign owner may have chosen the same UUID.
                return await self.get_conversation(project_id, client_conversation_id)
            raise
        return await self.get_conversation(project_id, conv.id)

    async def list_conversations(
        self, project_id: UUID | None, chapter_id: UUID | None = None,
    ) -> list[StudioAssistantConversation]:
        await self._verify_project_and_chapter(project_id, chapter_id)
        query = select(StudioAssistantConversation).where(self._scope(project_id))
        if chapter_id is not None:
            query = query.where(StudioAssistantConversation.chapter_id == chapter_id)
        return list(await self.session.scalars(
            query.order_by(StudioAssistantConversation.updated_at.desc())
        ))

    async def get_conversation(
        self, project_id: UUID | None, conversation_id: UUID, *, lock: bool = False,
    ) -> StudioAssistantConversation:
        await self._verify_project_and_chapter(project_id, None)
        statement = select(StudioAssistantConversation).where(
            StudioAssistantConversation.id == conversation_id, self._scope(project_id),
        ).execution_options(populate_existing=True)
        if lock:
            statement = statement.with_for_update()
        conv = await self.session.scalar(statement)
        if conv is None:
            raise NotFoundError("Assistant conversation not found.")
        return conv

    async def send_message(
        self, project_id: UUID | None, conversation_id: UUID, payload: SendRequest,
    ) -> StudioAssistantConversation:
        expected_type = (HomeAssistantSendRequest if self.home_owner_key else
                         SettingAssistantSendRequest if self.setting_collection_id else
                         AssistantSendMessageRequest)
        if not isinstance(payload, expected_type):
            raise ValidationError("Message context does not match this assistant scope.")
        conversation_lock = _CONVERSATION_LOCKS.setdefault(conversation_id, asyncio.Lock())
        async with conversation_lock:
            try:
                return await self._send_message(project_id, conversation_id, payload)
            except BaseException:
                # Neither half of a failed exchange may survive and block a durable retry.
                await self.session.rollback()
                raise

    async def _send_message(
        self, project_id: UUID | None, conversation_id: UUID, payload: SendRequest,
    ) -> StudioAssistantConversation:
        conv = await self.get_conversation(project_id, conversation_id, lock=True)
        target_chapter = getattr(payload, "chapter_id", None) or conv.chapter_id
        history_msgs = list(await self.session.scalars(select(StudioAssistantMessage).where(
            StudioAssistantMessage.conversation_id == conv.id,
        ).order_by(StudioAssistantMessage.created_at.asc())))
        if any(metadata.get("exchange_complete") is False for message in history_msgs if message.role == "user"
               for metadata in message.tool_calls or [] if isinstance(metadata, dict)):
            raise AssistantExchangeUnconfirmedError()
        request_context = self._request_context(payload)
        if payload.client_message_id:
            for message in history_msgs:
                if message.role != "user":
                    continue
                for metadata in message.tool_calls or []:
                    if not isinstance(metadata, dict) or metadata.get("client_message_id") != payload.client_message_id:
                        continue
                    original = metadata.get("context", {})
                    if (message.content != payload.content or any(
                        original.get(key) != value for key, value in request_context.items()
                    )):
                        raise ConflictError("This message identity was already used for different text or context.")
                    await self.session.commit()
                    return await self.get_conversation(project_id, conv.id)

        # Validate live context only for a new send; a committed lost ACK replays
        # its original exchange even if that optional chapter has since changed.
        if target_chapter is not None:
            if conv.chapter_id is not None and target_chapter != conv.chapter_id:
                raise ValidationError("A chapter conversation cannot switch to another chapter.")
            chapter = await self.session.scalar(select(Chapter).where(
                Chapter.id == target_chapter, Chapter.project_id == project_id,
            ))
            if chapter is None:
                raise ValidationError("Specified chapter does not belong to this project.")

        history = [{"role": m.role, "content": m.content} for m in history_msgs
                   if m.role in ("user", "assistant")]
        context = None
        if self.setting_collection_id:
            from app.agents.setting_assistant import setting_assistant_context
            context = await setting_assistant_context(self.session, self.setting_collection_id, payload)
            request_context = {key: value for key, value in context.items() if key != "body"}
        metadata = [{"client_message_id": payload.client_message_id, "context": request_context}]
        self.session.add(StudioAssistantMessage(
            conversation_id=conv.id, role="user", content=payload.content,
            tool_calls=metadata if payload.client_message_id else None, created_at=datetime.now(UTC),
        ))
        await self.session.flush()

        if self.home_owner_key:
            reply, tool_calls, tool_results = home_assistant_reply(payload.content), [], []
        elif context is not None:
            from app.agents.setting_assistant import generate_setting_reply
            reply = await generate_setting_reply(payload.content, context, history)
            tool_calls, tool_results = [], []
        else:
            reply, tool_calls, tool_results = await StudioAssistantAgent(
                self.session, project_id,
            ).generate_reply(
                user_message=payload.content, chapter_id=target_chapter,
                current_view=payload.current_view, history=history,
            )

        self.session.add(StudioAssistantMessage(
            conversation_id=conv.id, role="assistant", content=reply,
            tool_calls=tool_calls or None, tool_results=tool_results or None,
            created_at=datetime.now(UTC),
        ))
        conv.updated_at = datetime.now(UTC)
        await self.session.commit()
        return await self.get_conversation(project_id, conv.id)

    def _request_context(self, payload: SendRequest) -> dict:
        if self.home_owner_key:
            return {"current_view": payload.current_view}
        if self.setting_collection_id:
            return {
                "document_id": str(payload.document_id) if payload.document_id else None,
                "document_version_id": str(payload.document_version_id) if payload.document_version_id else None,
            }
        return {
            "chapter_id": str(payload.chapter_id) if payload.chapter_id else None,
            "current_view": payload.current_view,
        }
