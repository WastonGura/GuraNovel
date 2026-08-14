from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents import DeterministicChapterWriterProvider, WriterAgent
from app.models import (
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    Project,
    User,
    WorkflowRun,
    WorkflowType,
)
from app.services.chapter_production_v2_service import (
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Service,
    ChapterProductionV2ValidationError,
)
from app.services.chapter_phase_session_source import ChapterPhaseSessionSource
from app.services.document_service import DocumentService
from app.services.provider_attempt_contracts import initial_operation_key
from app.workflows.chapter_production import ChapterProductionStatus


CONTRACT_VERSION = "chapter-production-v2"
INACTIVE_STATUSES = frozenset(
    {
        ChapterProductionStatus.COMPLETED.value,
        ChapterProductionStatus.CANCELLED.value,
    }
)
pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _repository_module() -> ModuleType:
    return importlib.import_module("app.services.chapter_production_repository")


def _repository(session: AsyncSession) -> object:
    return _repository_module().ChapterProductionRepository(
        session,
        contract_version=CONTRACT_VERSION,
        inactive_run_statuses=INACTIVE_STATUSES,
    )


async def _approved_chapter(
    session: AsyncSession, workspace: Path, *, chapter_number: int = 1
) -> tuple[Project, Chapter, Document, DocumentVersion, User]:
    workspace.mkdir(parents=True, exist_ok=True)
    owner = User(username=f"repository-owner-{uuid4().hex}", display_name="Owner")
    session.add(owner)
    await session.flush()
    project = Project(
        slug=f"repository-project-{uuid4().hex}",
        title="Repository contract",
        workspace_root=str(workspace),
        owner_id=owner.id,
    )
    session.add(project)
    await session.flush()
    chapter = Chapter(
        project_id=project.id,
        chapter_number=chapter_number,
        title="Scoped chapter",
        status="OUTLINE_APPROVED",
    )
    session.add(chapter)
    await session.commit()
    document = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Approved outline",
        path=f"chapters/{chapter.id}-outline.md",
        content="# Plan\n\nEnter the repository boundary.\n",
        source=DocumentSource.OUTLINE_AGENT,
        agent_role="outline_agent",
    )
    chapter.current_outline_document_id = document.id
    await session.commit()
    assert document.current_version is not None
    return project, chapter, document, document.current_version, owner


def _run(
    *,
    project_id: UUID,
    chapter_id: UUID,
    operation_key: str,
    status: str = ChapterProductionStatus.DRAFTING.value,
) -> WorkflowRun:
    return WorkflowRun(
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status=status,
        current_node=ChapterProductionStatus.DRAFTING.value,
        awaiting_user=False,
        metadata_={
            "contract_version": CONTRACT_VERSION,
            "operation_key": operation_key,
        },
    )


async def test_locked_chapter_refreshes_stale_identity_and_holds_both_locks(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, _, _, _ = await _approved_chapter(async_session, tmp_path)
    stale = await async_session.get(Chapter, chapter.id)
    assert stale is chapter
    await async_session.commit()

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as writer:
            await writer.execute(
                update(Chapter).where(Chapter.id == chapter.id).values(status="DRAFTING")
            )
            await writer.commit()

        locked = await _repository(async_session).chapter(project.id, chapter.id, lock=True)
        assert locked is stale
        assert locked.status == "DRAFTING"
        assert await async_session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks "
                "WHERE pid = pg_backend_pid() AND locktype = 'advisory' AND granted)"
            )
        ) is True

        async with sessions() as contender:
            await contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError):
                await contender.execute(
                    update(Chapter).where(Chapter.id == chapter.id).values(status="BLOCKED")
                )
            await contender.rollback()
    finally:
        await async_session.rollback()
        await engine.dispose()


async def test_locked_outline_refreshes_its_stale_current_version(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, outline, first_version, _ = await _approved_chapter(
        async_session, tmp_path
    )
    assert await async_session.get(Document, outline.id) is outline
    await async_session.commit()

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    second_version_id = uuid4()
    try:
        async with sessions() as writer:
            writer.add(
                DocumentVersion(
                    id=second_version_id,
                    document_id=outline.id,
                    version_number=2,
                    parent_version_id=first_version.id,
                    source=DocumentSource.OUTLINE_AGENT.value,
                    agent_role="outline_agent",
                    content_hash="b" * 64,
                    byte_size=8,
                    word_count=1,
                    file_path=outline.path,
                    snapshot_path=f"snapshots/{second_version_id}.md",
                    metadata_={},
                )
            )
            await writer.flush()
            await writer.execute(
                update(Document)
                .where(Document.id == outline.id)
                .values(current_version_id=second_version_id)
            )
            await writer.commit()

        locked_outline, locked_version = await _repository(async_session).outline_for_chapter(
            project.id, chapter.id, lock=True
        )
        assert locked_outline is outline
        assert locked_outline.current_version_id == second_version_id
        assert locked_version.id == second_version_id
    finally:
        await async_session.rollback()
        await engine.dispose()


async def test_outline_lookup_refreshes_a_stale_chapter_pointer_before_following_it(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, first_outline, _, _ = await _approved_chapter(async_session, tmp_path)
    stale_chapter = await async_session.get(Chapter, chapter.id)
    assert stale_chapter is chapter
    await async_session.commit()

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as writer:
            replacement = await DocumentService(writer).create_document(
                project_id=project.id,
                chapter_id=chapter.id,
                document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
                title="Fresh outline pointer",
                path=f"chapters/{chapter.id}-fresh-outline.md",
                content="# Fresh\n\nFollow only the refreshed chapter pointer.\n",
                source=DocumentSource.OUTLINE_AGENT,
                agent_role="outline_agent",
            )
            await writer.execute(
                update(Chapter)
                .where(Chapter.id == chapter.id)
                .values(current_outline_document_id=replacement.id)
            )
            await writer.commit()

        outline, version = await _repository(async_session).outline_for_chapter(
            project.id, chapter.id, lock=False
        )
        assert outline.id == replacement.id and outline.id != first_outline.id
        assert version.id == replacement.current_version_id
        assert stale_chapter.current_outline_document_id == replacement.id
    finally:
        await async_session.rollback()
        await engine.dispose()


async def test_scoped_run_failures_are_fixed_and_service_normalizes_them(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _, owner = await _approved_chapter(async_session, tmp_path / "one")
    other_project, other_chapter, _, _, _ = await _approved_chapter(
        async_session, tmp_path / "two", chapter_number=1
    )
    run = _run(project_id=project.id, chapter_id=chapter.id, operation_key="a" * 64)
    async_session.add(run)
    await async_session.commit()

    module = _repository_module()
    with pytest.raises(module._ChapterProductionRepositoryValidationError) as captured:
        await _repository(async_session).run(
            other_project.id, other_chapter.id, run.id, lock=True
        )
    assert str(captured.value) == "Chapter production repository lookup failed."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None

    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
    )
    with pytest.raises(ChapterProductionV2ValidationError) as public_error:
        await service._run(other_project.id, other_chapter.id, run.id, lock=True)
    assert str(public_error.value) == "Chapter production input is invalid."
    assert owner.id == project.owner_id


async def test_owner_lookup_is_exact_and_fixed_for_missing_or_foreign_actors(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, _, _, _, owner = await _approved_chapter(async_session, tmp_path)
    foreign = User(username=f"foreign-owner-{uuid4().hex}", display_name="Foreign")
    async_session.add(foreign)
    await async_session.commit()
    repository = _repository(async_session)

    assert await repository.require_project_owner(project.id, owner.id, lock=True) is None
    module = _repository_module()
    for actor_user_id in (foreign.id, uuid4()):
        with pytest.raises(module._ChapterProductionRepositoryValidationError) as captured:
            await repository.require_project_owner(project.id, actor_user_id, lock=True)
        assert str(captured.value) == "Chapter production repository lookup failed."


@pytest.mark.parametrize(
    "corruption",
    ("wrong_status", "null_pointer", "wrong_type", "missing", "wrong_parent_version"),
)
async def test_approved_outline_fails_closed_for_every_parent_binding(
    async_session: AsyncSession,
    tmp_path: Path,
    corruption: str,
) -> None:
    project, chapter, outline, _, _ = await _approved_chapter(async_session, tmp_path / "one")
    if corruption == "wrong_status":
        chapter.status = "OUTLINE_DISCUSSION"
    elif corruption == "null_pointer":
        chapter.current_outline_document_id = None
    elif corruption == "wrong_type":
        outline.type = DocumentType.CHAPTER_DRAFT.value
    elif corruption == "missing":
        chapter.current_outline_document_id = uuid4()
    else:
        _, _, foreign_outline, foreign_version, _ = await _approved_chapter(
            async_session, tmp_path / "two", chapter_number=1
        )
        assert foreign_outline.id != outline.id
        outline.current_version_id = foreign_version.id
    await async_session.commit()

    module = _repository_module()
    with pytest.raises(module._ChapterProductionRepositoryValidationError):
        await _repository(async_session).approved_outline(project.id, chapter.id, lock=False)

async def test_lock_free_authoritative_reads_refresh_status_pointer_and_run(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, first_outline, _, _ = await _approved_chapter(async_session, tmp_path)
    operation_key = "e" * 64
    first = _run(project_id=project.id, chapter_id=chapter.id, operation_key=operation_key)
    async_session.add(first)
    await async_session.commit()
    stale_chapter = await async_session.get(Chapter, chapter.id)
    stale_run = await async_session.get(WorkflowRun, first.id)
    await async_session.commit()

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as writer:
            replacement = await DocumentService(writer).create_document(
                project_id=project.id,
                chapter_id=chapter.id,
                document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
                title="Replacement outline",
                path=f"chapters/{chapter.id}-replacement-outline.md",
                content="# Replacement\n\nUse the new pointer.\n",
                source=DocumentSource.OUTLINE_AGENT,
                agent_role="outline_agent",
            )
            await writer.execute(
                update(Chapter)
                .where(Chapter.id == chapter.id)
                .values(
                    status="OUTLINE_APPROVED",
                    current_outline_document_id=replacement.id,
                )
            )
            await writer.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == first.id)
                .values(
                    status=ChapterProductionStatus.AUTHOR_REVISION.value,
                    metadata_={
                        "contract_version": CONTRACT_VERSION,
                        "operation_key": "f" * 64,
                    },
                )
            )
            await writer.commit()

        repository = _repository(async_session)
        refreshed_chapter, outline, version = await repository.approved_outline(
            project.id, chapter.id, lock=False
        )
        refreshed_run = await repository.run(project.id, chapter.id, first.id, lock=False)
        assert refreshed_chapter is stale_chapter
        assert refreshed_chapter.current_outline_document_id == replacement.id
        assert outline.id == replacement.id and outline.id != first_outline.id
        assert version.id == replacement.current_version_id
        assert refreshed_run is stale_run
        assert refreshed_run.status == ChapterProductionStatus.AUTHOR_REVISION.value
        assert refreshed_run.metadata_["operation_key"] == "f" * 64
    finally:
        await async_session.rollback()
        await engine.dispose()


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("missing", None),
        ("exact", "match"),
        ("duplicate", "error"),
        ("active_other", "error"),
        ("exact_active_other", "error"),
        ("many_active_other", "error"),
        ("terminal_other", None),
        ("legacy", None),
        ("malformed", "error"),
        ("malformed_v2", "error"),
        ("malformed_key", "error"),
        ("cross_project", "error"),
        ("cross_chapter", "error"),
        ("different_chapter_other_key", None),
    ),
)
async def test_operation_run_exact_scope_and_cardinality_matrix(
    async_session: AsyncSession,
    tmp_path: Path,
    case: str,
    expected: str | None,
) -> None:
    project, chapter, _, _, _ = await _approved_chapter(async_session, tmp_path / "one")
    other_project, _, _, _, _ = await _approved_chapter(
        async_session, tmp_path / "two", chapter_number=1
    )
    sibling = Chapter(
        project_id=project.id,
        chapter_number=2,
        title="Sibling scope",
        status="OUTLINE_APPROVED",
    )
    async_session.add(sibling)
    await async_session.flush()
    operation_key = "c" * 64
    rows: list[WorkflowRun] = []
    if case in {"exact", "duplicate", "exact_active_other"}:
        rows.append(_run(project_id=project.id, chapter_id=chapter.id, operation_key=operation_key))
    if case == "duplicate":
        rows.append(_run(project_id=project.id, chapter_id=chapter.id, operation_key=operation_key))
    if case in {"active_other", "exact_active_other", "many_active_other"}:
        rows.append(_run(project_id=project.id, chapter_id=chapter.id, operation_key="d" * 64))
    if case == "many_active_other":
        rows.append(_run(project_id=project.id, chapter_id=chapter.id, operation_key="e" * 64))
    if case == "terminal_other":
        rows.append(
            _run(
                project_id=project.id,
                chapter_id=chapter.id,
                operation_key="d" * 64,
                status=ChapterProductionStatus.COMPLETED.value,
            )
        )
    if case == "legacy":
        legacy = _run(project_id=project.id, chapter_id=chapter.id, operation_key="d" * 64)
        legacy.metadata_ = {"contract_version": "legacy", "operation_key": "d" * 64}
        rows.append(legacy)
    if case == "malformed":
        malformed = _run(project_id=project.id, chapter_id=chapter.id, operation_key="d" * 64)
        malformed.metadata_ = []  # type: ignore[assignment]
        rows.append(malformed)
    if case == "malformed_v2":
        malformed_v2 = _run(
            project_id=project.id, chapter_id=chapter.id, operation_key="d" * 64
        )
        malformed_v2.metadata_ = {"contract_version": CONTRACT_VERSION}
        rows.append(malformed_v2)
    if case == "malformed_key":
        malformed_key = _run(
            project_id=project.id, chapter_id=chapter.id, operation_key="d" * 64
        )
        malformed_key.metadata_["operation_key"] = "D" * 64
        rows.append(malformed_key)
    if case == "cross_project":
        rows.append(
            _run(
                project_id=other_project.id,
                chapter_id=chapter.id,
                operation_key="d" * 64,
                status=ChapterProductionStatus.COMPLETED.value,
            )
        )
    if case in {"cross_chapter", "different_chapter_other_key"}:
        rows.append(
            _run(
                project_id=project.id,
                chapter_id=sibling.id,
                operation_key=(operation_key if case == "cross_chapter" else "d" * 64),
                status=ChapterProductionStatus.COMPLETED.value,
            )
        )
    async_session.add_all(rows)
    await async_session.commit()

    repository = _repository(async_session)
    module = _repository_module()
    if expected == "error":
        with pytest.raises(module._ChapterProductionRepositoryValidationError):
            await repository.operation_run(project.id, chapter.id, operation_key)
    else:
        result = await repository.operation_run(project.id, chapter.id, operation_key)
        if expected == "match":
            assert result is rows[0]
        else:
            assert result is None


async def test_operation_run_forces_refresh_before_matching(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    project, chapter, _, _, _ = await _approved_chapter(async_session, tmp_path)
    operation_key = "c" * 64
    run = _run(project_id=project.id, chapter_id=chapter.id, operation_key=operation_key)
    async_session.add(run)
    await async_session.commit()
    stale = await async_session.get(WorkflowRun, run.id)
    await async_session.commit()

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as writer:
            await writer.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == run.id)
                .values(
                    metadata_={
                        "contract_version": CONTRACT_VERSION,
                        "operation_key": "d" * 64,
                    }
                )
            )
            await writer.commit()

        module = _repository_module()
        with pytest.raises(module._ChapterProductionRepositoryValidationError):
            await _repository(async_session).operation_run(
                project.id, chapter.id, operation_key
            )
        assert stale is not None and stale.metadata_["operation_key"] == "d" * 64
    finally:
        await async_session.rollback()
        await engine.dispose()


async def test_facade_rejects_exact_operation_with_malformed_remaining_metadata_before_writes(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, outline, outline_version, owner = await _approved_chapter(
        async_session, tmp_path
    )

    class CountingProvider(DeterministicChapterWriterProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def draft_initial(self, request: object, profile: object) -> object:
            self.calls += 1
            return await super().draft_initial(request, profile)  # type: ignore[arg-type]

    provider = CountingProvider()
    bind = async_session.bind
    assert bind is not None
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(provider),
        phase_session_source=ChapterPhaseSessionSource(bind),
    )
    operation_key = initial_operation_key(
        project_id=project.id,
        chapter_id=chapter.id,
        outline_document_id=outline.id,
        outline_version_id=outline_version.id,
        outline_content_hash=outline_version.content_hash,
        segmenter_version="markdown-v1",
    )
    run = _run(project_id=project.id, chapter_id=chapter.id, operation_key=operation_key)
    run.metadata_ = {
        "contract_version": CONTRACT_VERSION,
        "operation_key": operation_key,
        "unexpected": "must-fail-before-provider-or-write",
    }
    async_session.add(run)
    await async_session.commit()
    before = (
        await async_session.scalar(select(func.count()).select_from(WorkflowRun)),
        await async_session.scalar(select(func.count()).select_from(Document)),
        await async_session.scalar(select(func.count()).select_from(DocumentVersion)),
    )

    with pytest.raises(ChapterProductionV2ValidationError) as captured:
        await service.start_from_approved_outline(
            project.id, chapter.id, actor_user_id=owner.id
        )
    assert str(captured.value) == "Chapter production input is invalid."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert provider.calls == 0
    after = (
        await async_session.scalar(select(func.count()).select_from(WorkflowRun)),
        await async_session.scalar(select(func.count()).select_from(Document)),
        await async_session.scalar(select(func.count()).select_from(DocumentVersion)),
    )
    assert after == before


@pytest.mark.parametrize("corruption", ("missing", "wrong_document"))
async def test_locked_current_version_has_a_distinct_reconciliation_boundary(
    async_session: AsyncSession, tmp_path: Path, corruption: str
) -> None:
    project, chapter, outline, _, _ = await _approved_chapter(async_session, tmp_path)
    if corruption == "missing":
        outline.current_version_id = None
    else:
        _, _, foreign_outline, foreign_version, _ = await _approved_chapter(
            async_session, tmp_path / "foreign", chapter_number=1
        )
        assert foreign_outline.id != outline.id
        outline.current_version_id = foreign_version.id
    await async_session.commit()

    module = _repository_module()
    with pytest.raises(module._ChapterProductionRepositoryReconciliationError) as internal:
        await _repository(async_session).locked_current_document_version(
            project_id=project.id,
            chapter_id=chapter.id,
            document_id=outline.id,
            expected_document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        )
    assert str(internal.value) == "Chapter production repository requires reconciliation."
    assert internal.value.__cause__ is None

    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
    )
    with pytest.raises(ChapterProductionV2ReconciliationError) as public:
        await service._locked_current_document_version(outline)
    assert str(public.value) == "Chapter production requires explicit reconciliation."
    assert chapter.project_id == project.id


async def test_current_version_lookup_rejects_a_foreign_document_under_local_scope(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, chapter, _, _, _ = await _approved_chapter(async_session, tmp_path / "local")
    _, _, foreign_outline, _, _ = await _approved_chapter(
        async_session, tmp_path / "foreign", chapter_number=1
    )
    module = _repository_module()

    with pytest.raises(module._ChapterProductionRepositoryReconciliationError) as captured:
        await _repository(async_session).locked_current_document_version(
            project_id=project.id,
            chapter_id=chapter.id,
            document_id=foreign_outline.id,
            expected_document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        )
    assert str(captured.value) == "Chapter production repository requires reconciliation."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert str(foreign_outline.id) not in repr(captured.value)


@pytest.mark.parametrize(
    "invalid_scope",
    ("project", "chapter", "document", "type"),
)
async def test_current_version_invalid_explicit_scope_is_always_reconciliation(
    async_session: AsyncSession, invalid_scope: str
) -> None:
    values: dict[str, object] = {
        "project_id": uuid4(),
        "chapter_id": uuid4(),
        "document_id": uuid4(),
        "expected_document_type": DocumentType.CHAPTER_FINAL,
    }
    values[
        "expected_document_type" if invalid_scope == "type" else f"{invalid_scope}_id"
    ] = None
    module = _repository_module()

    with pytest.raises(module._ChapterProductionRepositoryReconciliationError) as captured:
        await _repository(async_session).locked_current_document_version(
            **values  # type: ignore[arg-type]
        )
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


async def test_current_version_facade_maps_invalid_derived_scope_to_reconciliation(
    async_session: AsyncSession
) -> None:
    invalid_document = Document(
        id=uuid4(),
        project_id=uuid4(),
        chapter_id=None,
        type=DocumentType.CHAPTER_FINAL.value,
        path="chapters/invalid-scope.md",
    )
    service = ChapterProductionV2Service(
        async_session,
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
    )

    with pytest.raises(ChapterProductionV2ReconciliationError) as captured:
        await service._locked_current_document_version(invalid_document)
    assert str(captured.value) == "Chapter production requires explicit reconciliation."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize("external_change", ("switch", "wrong_document", "delete"))
async def test_locked_current_version_refreshes_the_document_before_following_its_pointer(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    external_change: str,
) -> None:
    _, _, outline, first_version, _ = await _approved_chapter(async_session, tmp_path / "one")
    foreign_version: DocumentVersion | None = None
    if external_change == "wrong_document":
        _, _, _, foreign_version, _ = await _approved_chapter(
            async_session, tmp_path / "two", chapter_number=1
        )
    stale_document = await async_session.get(Document, outline.id)
    assert stale_document is outline
    await async_session.commit()

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    replacement_id = uuid4()
    try:
        async with sessions() as writer:
            if external_change == "switch":
                writer.add(
                    DocumentVersion(
                        id=replacement_id,
                        document_id=outline.id,
                        version_number=2,
                        parent_version_id=first_version.id,
                        source=DocumentSource.OUTLINE_AGENT.value,
                        agent_role="outline_agent",
                        content_hash="c" * 64,
                        byte_size=8,
                        word_count=1,
                        file_path=outline.path,
                        snapshot_path=f"snapshots/{replacement_id}.md",
                        metadata_={},
                    )
                )
                await writer.flush()
                await writer.execute(
                    update(Document)
                    .where(Document.id == outline.id)
                    .values(current_version_id=replacement_id)
                )
            elif external_change == "wrong_document":
                assert foreign_version is not None
                replacement_id = foreign_version.id
                await writer.execute(
                    update(Document)
                    .where(Document.id == outline.id)
                    .values(current_version_id=replacement_id)
                )
            else:
                await writer.execute(delete(Document).where(Document.id == outline.id))
            await writer.commit()

        repository = _repository(async_session)
        if external_change == "switch":
            version = await repository.locked_current_document_version(
                project_id=stale_document.project_id,
                chapter_id=stale_document.chapter_id,
                document_id=stale_document.id,
                expected_document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
            )
            assert version.id == replacement_id
            assert stale_document.current_version_id == replacement_id
        else:
            module = _repository_module()
            with pytest.raises(module._ChapterProductionRepositoryReconciliationError):
                await repository.locked_current_document_version(
                    project_id=stale_document.project_id,
                    chapter_id=stale_document.chapter_id,
                    document_id=stale_document.id,
                    expected_document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
                )
    finally:
        await async_session.rollback()
        await engine.dispose()


async def test_repository_failure_leaves_rollback_ownership_to_the_caller(
    async_session: AsyncSession,
) -> None:
    pending = User(username=f"rollback-owner-{uuid4().hex}", display_name="Pending")
    async_session.add(pending)
    module = _repository_module()
    with pytest.raises(module._ChapterProductionRepositoryValidationError):
        await _repository(async_session).chapter(uuid4(), uuid4(), lock=False)
    assert async_session.in_transaction()

    username = pending.username
    await async_session.rollback()
    assert await async_session.scalar(select(User).where(User.username == username)) is None
