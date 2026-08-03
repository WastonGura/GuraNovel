"""Persist the chapter-production approval gate."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError, ConflictError, NotFoundError, WorkflowStateError
from app.core.logging import log_event
from app.llm import (
    ChapterGenerationProvenance,
    ChapterGenerationProvider,
    ChapterGenerationRequest,
    FakeChapterGenerationProvider,
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    validate_chapter_generation_response,
)
from app.llm.contracts import MAX_PROVENANCE_TOKEN_COUNT
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.services.document_service import DocumentService


def _normalize_generation_provider_error(error: Exception) -> Exception:
    try:
        error_type = type(error)
        if issubclass(error_type, ProviderConfigurationError):
            return ProviderConfigurationError()
        if issubclass(error_type, ProviderInvalidOutputError):
            return ProviderInvalidOutputError()
        if issubclass(error_type, ProviderRateLimitedError):
            return ProviderRateLimitedError()
        if issubclass(error_type, ProviderTimeoutError):
            return ProviderTimeoutError()
        if issubclass(error_type, ProviderUnavailableError):
            return ProviderUnavailableError()
        if issubclass(error_type, (TypeError, ValueError)):
            return ProviderInvalidOutputError()
    except Exception:
        pass
    return ProviderUnavailableError()


class ChapterProductionCommitIndeterminateError(AppError):
    """Raised when workflow persistence fails after documents have committed."""

    status_code = 500
    code = "chapter_production_commit_indeterminate"
    default_message = (
        "Chapter production artifacts were saved, but the workflow outcome could not be confirmed. "
        "Reconciliation is required before retrying."
    )


@dataclass(frozen=True)
class ChapterProductionStarted:
    workflow_run_id: UUID
    action_id: UUID
    outline_document_id: UUID
    draft_document_id: UUID


@dataclass(frozen=True)
class ChapterProductionResolved:
    workflow_run_id: UUID
    action_id: UUID
    decision: str


@dataclass(frozen=True)
class ChapterProductionActionRead:
    id: UUID
    type: str
    status: str
    options: tuple[str, ...]
    default_option: str | None
    user_decision: str | None


@dataclass(frozen=True)
class ChapterProductionEventRead:
    event_type: str
    node_name: str | None
    message: str | None
    payload: dict


@dataclass(frozen=True)
class ChapterProductionRunRead:
    id: UUID
    type: str
    status: str
    current_node: str | None
    next_node: str | None
    awaiting_user: bool
    actions: tuple[ChapterProductionActionRead, ...]
    events: tuple[ChapterProductionEventRead, ...]
    outline_document_id: UUID | None
    draft_document_id: UUID | None


class ChapterProductionService:
    """Create and resolve the chapter-production approval gate.

    Providers are untrusted transports.  A caller that injects one must also pass
    ``generation_provenance`` selected by server composition/configuration; this
    service never reads provenance identity from ``provider.generate()`` output.

    ``DocumentService`` commits each artifact and its filesystem write internally.
    Consequently this service deliberately does not claim atomic workflow-plus-filesystem
    behavior; after an artifact commit, a later persistence failure is indeterminate and
    never triggers document or workspace deletion.
    """

    _WORKFLOW_TYPE = WorkflowType.CHAPTER_PRODUCTION.value
    _AWAITING_APPROVAL = "awaiting_approval"
    _COMPLETED = "completed"
    _REJECTED = "rejected"
    _APPROVAL_NODE = "approval"
    _ACTION_TYPE = "chapter_production_approval"
    _FAKE_PROVENANCE = ChapterGenerationProvenance(
        provider_kind="fake",
        model_identifier="deterministic-fake-v1",
        prompt_template_version="chapter-production-v1",
    )

    def __init__(
        self,
        session: AsyncSession,
        generation_provider: ChapterGenerationProvider | None = None,
        *,
        generation_provenance: ChapterGenerationProvenance | None = None,
    ) -> None:
        self.session = session
        self.documents = DocumentService(session)
        if generation_provider is None:
            self.generation_provider = FakeChapterGenerationProvider()
            self.generation_provenance = self._FAKE_PROVENANCE
        else:
            if generation_provenance is None:
                raise ValueError(
                    "injected generation providers require explicit trusted server provenance"
                )
            self.generation_provider = generation_provider
            self.generation_provenance = generation_provenance

    async def start_production(
        self, project_id: UUID, chapter_id: UUID
    ) -> ChapterProductionStarted:
        chapter = await self._scoped_chapter(project_id, chapter_id)
        await self._lock_chapter(chapter_id)
        active_run = await self.session.scalar(
            select(WorkflowRun.id).where(
                WorkflowRun.project_id == project_id,
                WorkflowRun.chapter_id == chapter_id,
                WorkflowRun.workflow_type == self._WORKFLOW_TYPE,
                or_(
                    WorkflowRun.awaiting_user.is_(True),
                    WorkflowRun.status.not_in((self._COMPLETED, self._REJECTED)),
                ),
            )
        )
        if active_run is not None:
            raise ConflictError("Chapter production is already awaiting approval.")

        provider_failure: Exception | None = None
        provider_cancelled = False
        try:
            response = await self.generation_provider.generate(
                ChapterGenerationRequest(
                    project_title=chapter.project.title,
                    chapter_number=chapter.chapter_number,
                    title=chapter.title,
                )
            )
            response = validate_chapter_generation_response(response)
        except asyncio.CancelledError:
            provider_cancelled = True
        except Exception as error:
            provider_failure = _normalize_generation_provider_error(error)
        if provider_cancelled or provider_failure is not None:
            try:
                await self.session.rollback()
            except Exception:
                pass
            if provider_cancelled:
                raise asyncio.CancelledError() from None
            raise provider_failure from None
        generated = response.result
        run = WorkflowRun(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_type=self._WORKFLOW_TYPE,
            status=self._AWAITING_APPROVAL,
            current_node=self._APPROVAL_NODE,
            next_node=None,
            awaiting_user=True,
        )
        self.session.add(run)
        await self.session.flush()
        self.session.add(
            WorkflowEvent(
                workflow_run_id=run.id,
                event_type="production_started",
                node_name="start",
                message="Started chapter production.",
            )
        )
        self.session.add(
            WorkflowEvent(
                workflow_run_id=run.id,
                event_type="generation_provenance",
                node_name="generate",
                message="Recorded chapter generation provenance.",
                payload=self.generation_provenance.to_payload(
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                ),
                created_at=datetime.now(UTC),
            )
        )

        artifacts_committed = False
        try:
            outline = await self.documents.create_document(
                project_id=project_id,
                chapter_id=chapter_id,
                document_type=DocumentType.CHAPTER_OUTLINE_OPTIONS,
                title=f"Chapter {chapter.chapter_number} outline",
                path=self._outline_path(chapter.chapter_number),
                content=generated.outline,
                source=DocumentSource.OUTLINE_AGENT,
                agent_role="outline_agent",
                workflow_run_id=run.id,
                change_summary="Generated chapter outline.",
            )
            artifacts_committed = True
            self.session.add(
                WorkflowEvent(
                    workflow_run_id=run.id,
                    event_type="generation_output_stored",
                    node_name="generate",
                    message="Stored generated chapter output.",
                    payload={"outline_document_id": str(outline.id)},
                )
            )
            draft = await self.documents.create_document(
                project_id=project_id,
                chapter_id=chapter_id,
                document_type=DocumentType.CHAPTER_DRAFT,
                title=f"Chapter {chapter.chapter_number} draft",
                path=self._draft_path(chapter.chapter_number),
                content=generated.draft,
                source=DocumentSource.WRITER_AGENT,
                agent_role="writer_agent",
                workflow_run_id=run.id,
                change_summary="Generated chapter draft.",
            )
            chapter.current_outline_document_id = outline.id
            chapter.current_draft_document_id = draft.id
            action = ActionRequest(
                workflow_run_id=run.id,
                project_id=project_id,
                chapter_id=chapter_id,
                request_type=self._ACTION_TYPE,
                status=ActionRequestStatus.PENDING.value,
                prompt="Approve the generated chapter outline and draft?",
                options=["approved", "rejected"],
                default_option="approved",
            )
            self.session.add_all(
                [
                    action,
                    WorkflowEvent(
                        workflow_run_id=run.id,
                        event_type="awaiting_approval",
                        node_name=self._APPROVAL_NODE,
                        message="Awaiting approval of generated chapter artifacts.",
                    ),
                ]
            )
            await self.session.flush()
            await self.session.commit()
        except BaseException as error:
            try:
                await self.session.rollback()
            except BaseException:
                pass
            if artifacts_committed:
                raise ChapterProductionCommitIndeterminateError() from error
            raise
        log_event(
            "chapter_production_started",
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=run.id,
            action_id=action.id,
        )
        return ChapterProductionStarted(run.id, action.id, outline.id, draft.id)

    async def resolve_action(
        self,
        project_id: UUID,
        chapter_id: UUID,
        workflow_run_id: UUID,
        action_id: UUID,
        decision: str,
    ) -> ChapterProductionResolved:
        chapter = await self._scoped_chapter(project_id, chapter_id)
        run = await self.session.scalar(
            select(WorkflowRun)
            .where(
                WorkflowRun.id == workflow_run_id,
                WorkflowRun.project_id == project_id,
                WorkflowRun.chapter_id == chapter_id,
                WorkflowRun.workflow_type == self._WORKFLOW_TYPE,
            )
            .with_for_update()
        )
        if run is None:
            raise NotFoundError("Chapter production workflow not found.")
        action = await self.session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.id == action_id,
                ActionRequest.workflow_run_id == workflow_run_id,
                ActionRequest.project_id == project_id,
                ActionRequest.chapter_id == chapter_id,
                ActionRequest.request_type == self._ACTION_TYPE,
            )
            .with_for_update()
        )
        if action is None:
            raise NotFoundError("Chapter production approval not found.")
        if decision not in {"approved", "rejected"}:
            raise WorkflowStateError("Chapter production decisions must be approved or rejected.")
        if action.status != ActionRequestStatus.PENDING.value or not run.awaiting_user:
            raise WorkflowStateError("Chapter production approval has already been resolved.")

        approved = decision == "approved"
        action.status = (
            ActionRequestStatus.APPROVED.value if approved else ActionRequestStatus.REJECTED.value
        )
        action.user_decision = decision
        action.resolved_at = datetime.now(UTC)
        run.awaiting_user = False
        run.status = self._COMPLETED if approved else self._REJECTED
        run.current_node = self._APPROVAL_NODE
        run.next_node = None
        run.completed_at = datetime.now(UTC)
        if approved:
            chapter.status = "OUTLINE_APPROVED"
        self.session.add(
            WorkflowEvent(
                workflow_run_id=run.id,
                event_type=f"approval_{decision}",
                node_name=self._APPROVAL_NODE,
                message=f"Chapter production was {decision}.",
                payload={"decision": decision, "action_id": str(action.id)},
            )
        )
        try:
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        log_event(
            "chapter_production_action_resolved",
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=run.id,
            action_id=action.id,
            decision=decision,
        )
        return ChapterProductionResolved(run.id, action.id, decision)

    async def get_production_run(
        self, project_id: UUID, chapter_id: UUID, workflow_run_id: UUID
    ) -> ChapterProductionRunRead:
        """Return detached public workflow state without reading artifact content."""
        run = await self.session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == workflow_run_id,
                WorkflowRun.project_id == project_id,
                WorkflowRun.chapter_id == chapter_id,
                WorkflowRun.workflow_type == self._WORKFLOW_TYPE,
            )
        )
        if run is None:
            raise NotFoundError("Chapter production workflow not found.")

        actions = list(
            await self.session.scalars(
                select(ActionRequest)
                .where(
                    ActionRequest.workflow_run_id == workflow_run_id,
                    ActionRequest.project_id == project_id,
                    ActionRequest.chapter_id == chapter_id,
                    ActionRequest.request_type == self._ACTION_TYPE,
                )
                .order_by(ActionRequest.created_at, ActionRequest.id)
            )
        )
        events = list(
            await self.session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.workflow_run_id == workflow_run_id)
                .order_by(WorkflowEvent.created_at, WorkflowEvent.id)
            )
        )
        documents = list(
            await self.session.execute(
                select(Document.id, Document.type)
                .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                .where(
                    Document.project_id == project_id,
                    Document.chapter_id == chapter_id,
                    DocumentVersion.workflow_run_id == workflow_run_id,
                    Document.type.in_(
                        (
                            DocumentType.CHAPTER_OUTLINE_OPTIONS.value,
                            DocumentType.CHAPTER_DRAFT.value,
                        )
                    ),
                )
            )
        )
        document_ids = {document_type: document_id for document_id, document_type in documents}
        return ChapterProductionRunRead(
            id=run.id,
            type=run.workflow_type,
            status=run.status,
            current_node=run.current_node,
            next_node=run.next_node,
            awaiting_user=run.awaiting_user,
            actions=tuple(
                ChapterProductionActionRead(
                    id=action.id,
                    type=action.request_type,
                    status=action.status,
                    options=tuple(action.options),
                    default_option=action.default_option,
                    user_decision=action.user_decision,
                )
                for action in actions
            ),
            events=tuple(
                ChapterProductionEventRead(
                    event_type=event.event_type,
                    node_name=event.node_name,
                    message=event.message,
                    payload=self._public_event_payload(event.event_type, event.payload),
                )
                for event in events
            ),
            outline_document_id=document_ids.get(DocumentType.CHAPTER_OUTLINE_OPTIONS.value),
            draft_document_id=document_ids.get(DocumentType.CHAPTER_DRAFT.value),
        )

    @staticmethod
    def _public_event_payload(event_type: str, payload: object) -> dict[str, str | int]:
        """Project event payloads onto the small, public chapter-production schema."""
        if not isinstance(payload, dict):
            return {}
        if event_type == "generation_provenance":
            try:
                provenance = ChapterGenerationProvenance(
                    provider_kind=payload["provider_kind"],
                    model_identifier=payload["model_identifier"],
                    prompt_template_version=payload["prompt_template_version"],
                )
            except (KeyError, TypeError, ValueError):
                return {}
            input_tokens = payload.get("input_tokens")
            output_tokens = payload.get("output_tokens")
            for value in (input_tokens, output_tokens):
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= MAX_PROVENANCE_TOKEN_COUNT
                ):
                    return {}
            return provenance.to_payload(input_tokens=input_tokens, output_tokens=output_tokens)
        # Historical fake_output_stored events remain public only when their
        # outline_document_id satisfies the current canonical UUID contract.
        if event_type in {"generation_output_stored", "fake_output_stored"}:
            document_id = ChapterProductionService._canonical_uuid(payload.get("outline_document_id"))
            return {"outline_document_id": document_id} if document_id is not None else {}
        if event_type in {"approval_approved", "approval_rejected"}:
            decision = event_type.removeprefix("approval_")
            action_id = ChapterProductionService._canonical_uuid(payload.get("action_id"))
            if payload.get("decision") != decision or action_id is None:
                return {}
            return {"decision": decision, "action_id": action_id}
        if event_type in {"production_started", "awaiting_approval"}:
            return {}
        return {}

    @staticmethod
    def _canonical_uuid(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = UUID(value)
        except (TypeError, ValueError, AttributeError):
            return None
        return value if str(parsed) == value else None

    async def _scoped_chapter(self, project_id: UUID, chapter_id: UUID) -> Chapter:
        chapter = await self.session.scalar(
            select(Chapter)
            .options(selectinload(Chapter.project))
            .where(Chapter.id == chapter_id, Chapter.project_id == project_id)
        )
        if chapter is None:
            raise NotFoundError("Chapter not found.")
        return chapter

    async def _lock_chapter(self, chapter_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"chapter-production:{chapter_id}"},
        )

    @staticmethod
    def _outline_path(chapter_number: int) -> str:
        return f"chapters/chapter-{chapter_number:04d}-outline.md"

    @staticmethod
    def _draft_path(chapter_number: int) -> str:
        return f"chapters/chapter-{chapter_number:04d}-draft.md"
