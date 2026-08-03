from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    WorkflowEvent,
    WorkflowRun,
    WorkflowType,
)
from app.core.errors import ConflictError, NotFoundError
from app.core.errors import WorkflowStateError
from app.llm import (
    ChapterGenerationRequest,
    ChapterGenerationResponse,
    ChapterGenerationResult,
    ChapterGenerationProvenance,
    ProviderInvalidOutputError,
    ProviderUnavailableError,
)
from app.services.chapter_production_service import (
    ChapterProductionCommitIndeterminateError,
    ChapterProductionService,
)
from app.services.chapter_service import ChapterService
from app.services.document_service import DocumentService
from app.services.project_service import ProjectService
from app.workspace import ProjectWorkspace


async def create_project_and_chapter(async_session: AsyncSession, workspace_base: Path):
    project = await ProjectService(async_session, ProjectWorkspace(workspace_base)).create_project(
        slug=f"chapter-production-{workspace_base.name}",
        title="Archive of Ash",
    )
    chapter = await ChapterService(async_session).create_chapter(
        project_id=project.id, title="The Locked Door"
    )
    return project, chapter


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_production_persists_fake_artifacts_and_approval_gate(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "workspaces")

    result = await ChapterProductionService(async_session).start_production(project.id, chapter.id)

    run = await async_session.get(WorkflowRun, result.workflow_run_id)
    assert run is not None
    assert run.project_id == project.id
    assert run.chapter_id == chapter.id
    assert run.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value
    assert run.status == "awaiting_approval"
    assert run.current_node == "approval"
    assert run.next_node is None
    assert run.awaiting_user is True

    persisted_chapter = await async_session.get(Chapter, chapter.id)
    assert persisted_chapter is not None
    documents = list(
        await async_session.scalars(
            select(Document)
            .options(selectinload(Document.current_version))
            .where(Document.project_id == project.id)
            .order_by(Document.path)
        )
    )
    assert [(document.type, document.path, document.chapter_id) for document in documents] == [
        (DocumentType.CHAPTER_DRAFT.value, "chapters/chapter-0001-draft.md", chapter.id),
        (DocumentType.CHAPTER_OUTLINE_OPTIONS.value, "chapters/chapter-0001-outline.md", chapter.id),
    ]
    outline = next(document for document in documents if document.type == DocumentType.CHAPTER_OUTLINE_OPTIONS.value)
    draft = next(document for document in documents if document.type == DocumentType.CHAPTER_DRAFT.value)
    assert persisted_chapter.current_outline_document_id == outline.id
    assert persisted_chapter.current_draft_document_id == draft.id
    assert outline.current_version is not None
    assert draft.current_version is not None
    assert outline.current_version.source == DocumentSource.OUTLINE_AGENT.value
    assert outline.current_version.agent_role == "outline_agent"
    assert outline.current_version.workflow_run_id == run.id
    assert outline.current_version.change_summary == "Generated chapter outline."
    assert draft.current_version.source == DocumentSource.WRITER_AGENT.value
    assert draft.current_version.agent_role == "writer_agent"
    assert draft.current_version.workflow_run_id == run.id
    assert draft.current_version.change_summary == "Generated chapter draft."
    assert (Path(project.workspace_root) / outline.path).read_text() == (
        await DocumentService(async_session).read_current_content(outline.id)
    ).content
    assert (Path(project.workspace_root) / draft.path).read_text() == (
        await DocumentService(async_session).read_current_content(draft.id)
    ).content

    events = list(
        await async_session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == run.id)
            .order_by(WorkflowEvent.created_at, WorkflowEvent.id)
        )
    )
    assert [event.event_type for event in events] == [
        "production_started",
        "generation_provenance",
        "generation_output_stored",
        "awaiting_approval",
    ]
    assert events[1].payload == {
        "provider_kind": "fake",
        "model_identifier": "deterministic-fake-v1",
        "prompt_template_version": "chapter-production-v1",
    }
    assert events[0].message == "Started chapter production."
    assert events[2].message == "Stored generated chapter output."
    action = await async_session.scalar(
        select(ActionRequest).where(ActionRequest.workflow_run_id == run.id)
    )
    assert action is not None
    assert action.project_id == project.id
    assert action.chapter_id == chapter.id
    assert action.request_type == "chapter_production_approval"
    assert action.status == ActionRequestStatus.PENDING.value
    assert action.options == ["approved", "rejected"]
    assert action.default_option == "approved"
    assert action.prompt == "Approve the generated chapter outline and draft?"


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_production_uses_injected_provider_and_persists_its_artifacts(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "provider")

    class SpyProvider:
        def __init__(self) -> None:
            self.requests: list[ChapterGenerationRequest] = []

        async def generate(self, request: ChapterGenerationRequest) -> ChapterGenerationResponse:
            self.requests.append(request)
            return ChapterGenerationResponse(
                result=ChapterGenerationResult(
                    outline="# Provider outline\n",
                    draft="# Provider draft\n",
                    summary="Provider summary",
                ), input_tokens=123, output_tokens=456,
            )

    provider = SpyProvider()
    service = ChapterProductionService(
        async_session, generation_provider=provider,
        generation_provenance=ChapterGenerationProvenance(
            "test_provider", "test-model-2026", "chapter-template-v2"
        ),
    )
    result = await service.start_production(project.id, chapter.id)

    assert provider.requests == [
        ChapterGenerationRequest(
            project_title="Archive of Ash", chapter_number=1, title="The Locked Door"
        )
    ]
    documents = list(
        await async_session.scalars(
            select(Document)
            .options(selectinload(Document.current_version))
            .where(Document.project_id == project.id)
            .order_by(Document.path)
        )
    )
    outline = next(document for document in documents if document.type == DocumentType.CHAPTER_OUTLINE_OPTIONS.value)
    draft = next(document for document in documents if document.type == DocumentType.CHAPTER_DRAFT.value)
    assert (await DocumentService(async_session).read_current_content(outline.id)).content == "# Provider outline\n"
    assert (await DocumentService(async_session).read_current_content(draft.id)).content == "# Provider draft\n"
    events = list(
        await async_session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == result.workflow_run_id)
            .order_by(WorkflowEvent.created_at, WorkflowEvent.id)
        )
    )
    assert [event.event_type for event in events] == [
        "production_started",
        "generation_provenance",
        "generation_output_stored",
        "awaiting_approval",
    ]
    assert events[1].payload == {
        "provider_kind": "test_provider",
        "model_identifier": "test-model-2026",
        "prompt_template_version": "chapter-template-v2",
        "input_tokens": 123,
        "output_tokens": 456,
    }
    run_read = await service.get_production_run(project.id, chapter.id, result.workflow_run_id)
    assert run_read.events[1].payload == events[1].payload
    action = await async_session.scalar(
        select(ActionRequest).where(ActionRequest.id == result.action_id)
    )
    assert action is not None
    assert action.status == ActionRequestStatus.PENDING.value
    assert action.options == ["approved", "rejected"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_provider_failure_rolls_back_lock_and_leaves_no_production_artifacts(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "provider-failure")
    project_id = project.id
    chapter_id = chapter.id
    workspace = Path(project.workspace_root)

    class FailingProvider:
        async def generate(self, _: ChapterGenerationRequest) -> ChapterGenerationResponse:
            raise ProviderUnavailableError()

    with pytest.raises(ProviderUnavailableError) as error:
        await ChapterProductionService(
            async_session, generation_provider=FailingProvider(),
            generation_provenance=ChapterGenerationProvenance("test_provider", "test-model", "v1"),
        ).start_production(project_id, chapter_id)

    assert error.value.status_code == 503
    assert error.value.code == "provider_unavailable"
    assert error.value.message == "The generation provider is temporarily unavailable. Please try again later."
    assert await async_session.scalar(
        select(WorkflowRun.id).where(WorkflowRun.chapter_id == chapter_id)
    ) is None
    assert await async_session.scalar(
        select(WorkflowEvent.id).join(WorkflowRun).where(WorkflowRun.chapter_id == chapter_id)
    ) is None
    assert await async_session.scalar(
        select(ActionRequest.id).where(ActionRequest.chapter_id == chapter_id)
    ) is None
    assert await async_session.scalar(
        select(Document.id).where(Document.chapter_id == chapter_id)
    ) is None
    assert not list(workspace.rglob("*.md"))

    retry = await ChapterProductionService(async_session).start_production(project_id, chapter_id)
    assert retry.workflow_run_id


@pytest.mark.integration
@pytest.mark.anyio
async def test_injected_provider_requires_explicit_server_owned_provenance(
    async_session: AsyncSession,
) -> None:
    class Provider:
        async def generate(self, _: ChapterGenerationRequest) -> ChapterGenerationResponse:
            return ChapterGenerationResponse(ChapterGenerationResult("outline", "draft", "summary"))

    with pytest.raises(ValueError, match="explicit trusted server provenance"):
        ChapterProductionService(async_session, generation_provider=Provider())


@pytest.mark.integration
@pytest.mark.anyio
async def test_malformed_provider_response_rolls_back_before_persistence_and_permits_retry(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "invalid-provider")
    project_id = project.id
    chapter_id = chapter.id
    workspace = Path(project.workspace_root)

    class MalformedProvider:
        async def generate(self, _: ChapterGenerationRequest) -> ChapterGenerationResponse:
            return ChapterGenerationResponse(
                result=ChapterGenerationResult(outline=42, draft="draft", summary="summary"),  # type: ignore[arg-type]
            )

    with pytest.raises(ProviderInvalidOutputError) as error:
        await ChapterProductionService(
            async_session, generation_provider=MalformedProvider(),
            generation_provenance=ChapterGenerationProvenance("test_provider", "test-model", "v1"),
        ).start_production(project_id, chapter_id)

    assert error.value.code == "provider_invalid_output"
    assert error.value.message == "The generation provider returned invalid output."
    assert await async_session.scalar(
        select(WorkflowRun.id).where(WorkflowRun.chapter_id == chapter_id)
    ) is None
    assert await async_session.scalar(
        select(WorkflowEvent.id).join(WorkflowRun).where(WorkflowRun.chapter_id == chapter_id)
    ) is None
    assert await async_session.scalar(
        select(ActionRequest.id).where(ActionRequest.chapter_id == chapter_id)
    ) is None
    assert await async_session.scalar(
        select(Document.id).where(Document.chapter_id == chapter_id)
    ) is None
    assert not list(workspace.rglob("*.md"))

    retry = await ChapterProductionService(async_session).start_production(project_id, chapter_id)
    assert retry.workflow_run_id


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "opaque_value",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        "a" * 64,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJvcGFxdWUtc2VjcmV0In0.signature",
    ],
)
async def test_provider_supplied_provenance_is_never_persisted_or_projected(
    async_session: AsyncSession, tmp_path: Path, opaque_value: str
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "opaque-provenance")

    class UnsafeProvider:
        async def generate(self, _: ChapterGenerationRequest) -> ChapterGenerationResponse:
            response = ChapterGenerationResponse(ChapterGenerationResult("outline", "draft", "summary"))
            # Simulates any legacy/extra provider response shape; validation ignores it.
            object.__setattr__(response, "provenance", opaque_value)
            object.__setattr__(response, "provider_kind", opaque_value)
            object.__setattr__(response, "model_identifier", opaque_value)
            object.__setattr__(response, "prompt_template_version", opaque_value)
            return response

    service = ChapterProductionService(
        async_session, UnsafeProvider(),
        generation_provenance=ChapterGenerationProvenance("test_provider", "server-model", "server-v1"),
    )
    started = await service.start_production(project.id, chapter.id)
    event = await async_session.scalar(select(WorkflowEvent).where(
        WorkflowEvent.workflow_run_id == started.workflow_run_id,
        WorkflowEvent.event_type == "generation_provenance",
    ))
    assert event is not None
    assert event.payload == {
        "provider_kind": "test_provider", "model_identifier": "server-model",
        "prompt_template_version": "server-v1",
    }
    assert opaque_value not in str(event.payload)
    assert opaque_value not in str((await service.get_production_run(
        project.id, chapter.id, started.workflow_run_id
    )).events)


@pytest.mark.integration
@pytest.mark.anyio
async def test_get_production_run_projects_event_payloads_and_excludes_secrets(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "event-projection")
    service = ChapterProductionService(async_session)
    started = await service.start_production(project.id, chapter.id)
    event = await async_session.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.workflow_run_id == started.workflow_run_id,
            WorkflowEvent.event_type == "generation_provenance",
        )
    )
    assert event is not None
    event.payload = {**event.payload, "Authorization": "Bearer secret", "raw_output": "chapter text"}
    async_session.add(
        WorkflowEvent(
            workflow_run_id=started.workflow_run_id,
            event_type="unknown_provider_event",
            payload={"api_key": "super-secret"},
        )
    )
    await async_session.commit()

    run = await service.get_production_run(project.id, chapter.id, started.workflow_run_id)
    assert run.events[1].payload == {
        "provider_kind": "fake",
        "model_identifier": "deterministic-fake-v1",
        "prompt_template_version": "chapter-production-v1",
    }
    assert run.events[-1].payload == {}


@pytest.mark.integration
@pytest.mark.anyio
async def test_provider_failure_remains_primary_when_rollback_fails(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter = await create_project_and_chapter(
        async_session, tmp_path / "provider-rollback-failure"
    )
    original_rollback = async_session.rollback

    class FailingProvider:
        async def generate(self, _: ChapterGenerationRequest) -> ChapterGenerationResponse:
            raise ProviderUnavailableError()

    async def fail_rollback() -> None:
        raise RuntimeError("database rollback failed")

    monkeypatch.setattr(async_session, "rollback", fail_rollback)
    with pytest.raises(ProviderUnavailableError) as error:
        await ChapterProductionService(
            async_session, generation_provider=FailingProvider(),
            generation_provenance=ChapterGenerationProvenance("test_provider", "test-model", "v1"),
        ).start_production(project.id, chapter.id)

    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert "database rollback failed" not in repr(error.value)
    monkeypatch.setattr(async_session, "rollback", original_rollback)
    await async_session.rollback()


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_production_scopes_chapters_and_rejects_an_existing_awaiting_run(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "first")
    other_project, other_chapter = await create_project_and_chapter(
        async_session, tmp_path / "second"
    )
    service = ChapterProductionService(async_session)

    with pytest.raises(NotFoundError):
        await service.start_production(project.id, other_chapter.id)
    with pytest.raises(NotFoundError):
        await service.start_production(uuid4(), chapter.id)

    await service.start_production(project.id, chapter.id)
    with pytest.raises(ConflictError) as error:
        await service.start_production(project.id, chapter.id)

    assert error.value.message == "Chapter production is already awaiting approval."
    assert await async_session.scalar(
        select(WorkflowRun.id).where(
            WorkflowRun.project_id == other_project.id,
            WorkflowRun.chapter_id == other_chapter.id,
        )
    ) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_concurrent_start_production_creates_one_durable_run_and_approval_gate(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "concurrent")
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def start_production() -> object:
        async with session_factory() as session:
            return await ChapterProductionService(session).start_production(project.id, chapter.id)

    try:
        outcomes = await asyncio.gather(*(start_production() for _ in range(4)), return_exceptions=True)

        successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 3
        assert all(isinstance(error, ConflictError) for error in failures)

        async with session_factory() as check_session:
            runs = list(
                await check_session.scalars(
                    select(WorkflowRun).where(
                        WorkflowRun.project_id == project.id,
                        WorkflowRun.chapter_id == chapter.id,
                        WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
                    )
                )
            )
            actions = list(
                await check_session.scalars(
                    select(ActionRequest).where(
                        ActionRequest.project_id == project.id,
                        ActionRequest.chapter_id == chapter.id,
                        ActionRequest.request_type == "chapter_production_approval",
                        ActionRequest.status == ActionRequestStatus.PENDING.value,
                    )
                )
            )

        assert len(runs) == 1
        assert len(actions) == 1
        assert actions[0].workflow_run_id == runs[0].id
        workspace = Path(project.workspace_root)
        assert (workspace / "chapters/chapter-0001-outline.md").exists()
        assert (workspace / "chapters/chapter-0001-draft.md").exists()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_resolve_action_approves_the_scoped_pending_gate_and_rejects_replay(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "approve")
    service = ChapterProductionService(async_session)
    started = await service.start_production(project.id, chapter.id)

    resolved = await service.resolve_action(
        project.id, chapter.id, started.workflow_run_id, started.action_id, "approved"
    )

    assert resolved.decision == "approved"
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    action = await async_session.get(ActionRequest, started.action_id)
    persisted_chapter = await async_session.get(Chapter, chapter.id)
    assert run is not None
    assert action is not None
    assert persisted_chapter is not None
    assert run.status == "completed"
    assert run.awaiting_user is False
    assert run.current_node == "approval"
    assert run.next_node is None
    assert action.status == ActionRequestStatus.APPROVED.value
    assert action.user_decision == "approved"
    assert persisted_chapter.status == "OUTLINE_APPROVED"
    assert list(
        await async_session.scalars(
            select(WorkflowEvent.event_type)
            .where(WorkflowEvent.workflow_run_id == started.workflow_run_id)
            .order_by(WorkflowEvent.created_at)
        )
    ) == [
        "production_started",
        "generation_provenance",
        "generation_output_stored",
        "awaiting_approval",
        "approval_approved",
    ]

    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            project.id, chapter.id, started.workflow_run_id, started.action_id, "approved"
        )


@pytest.mark.integration
@pytest.mark.anyio
async def test_resolve_action_rejects_without_changing_chapter_status_or_crossing_scope(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "reject")
    other_project, other_chapter = await create_project_and_chapter(
        async_session, tmp_path / "isolated"
    )
    service = ChapterProductionService(async_session)
    started = await service.start_production(project.id, chapter.id)
    other_started = await service.start_production(other_project.id, other_chapter.id)

    with pytest.raises(NotFoundError):
        await service.resolve_action(
            project.id,
            chapter.id,
            other_started.workflow_run_id,
            other_started.action_id,
            "rejected",
        )
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            project.id, chapter.id, started.workflow_run_id, started.action_id, "revise"
        )

    await service.resolve_action(
        project.id, chapter.id, started.workflow_run_id, started.action_id, "rejected"
    )

    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    action = await async_session.get(ActionRequest, started.action_id)
    persisted_chapter = await async_session.get(Chapter, chapter.id)
    assert run is not None
    assert action is not None
    assert persisted_chapter is not None
    assert run.status == "rejected"
    assert run.awaiting_user is False
    assert run.current_node == "approval"
    assert run.next_node is None
    assert action.status == ActionRequestStatus.REJECTED.value
    assert action.user_decision == "rejected"
    assert persisted_chapter.status == "OUTLINE_DISCUSSION"


@pytest.mark.integration
@pytest.mark.anyio
async def test_post_document_workflow_commit_failure_preserves_artifacts_and_is_indeterminate(
    async_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, chapter = await create_project_and_chapter(async_session, tmp_path / "indeterminate")
    workspace = Path(project.workspace_root)
    original_commit = async_session.commit
    commits = 0

    async def fail_final_workflow_commit() -> None:
        nonlocal commits
        commits += 1
        if commits == 3:
            raise RuntimeError("final workflow commit failed")
        await original_commit()

    monkeypatch.setattr(async_session, "commit", fail_final_workflow_commit)

    with pytest.raises(ChapterProductionCommitIndeterminateError) as error:
        await ChapterProductionService(async_session).start_production(project.id, chapter.id)

    assert error.value.code == "chapter_production_commit_indeterminate"
    assert (workspace / "chapters/chapter-0001-outline.md").exists()
    assert (workspace / "chapters/chapter-0001-draft.md").exists()
    assert len(list((workspace / ".versions").rglob("*.md"))) == 2
