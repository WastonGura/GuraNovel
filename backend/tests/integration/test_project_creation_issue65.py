"""#65 vertical-slice acceptance tests (PostgreSQL required)."""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.chief_editor import ChiefEditor
from app.agents.composition import ProjectCreationComposition
from app.agents.concept_agent import ConceptAgent
from app.agents.contracts import ConceptAgentRequest
from app.models import (
    ActionRequest,
    Chapter,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    Project,
    ReviewReport,
    User,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
)
from app.core.errors import ConflictError, NotFoundError
from app.services import ProjectService
from app.services.document_service import DocumentService
from app.services.project_creation_service import ProjectCreationService
from app.workspace import ProjectWorkspace
from app.core.errors import WorkflowStateError
from app.services.document_service import DocumentCommitIndeterminateError
from app.llm.errors import ProviderInvalidOutputError
from app.llm.errors import ProviderUnavailableError
from app.workflows.project_creation import ProjectCreationState, ProjectCreationStatus
from app.workspace.hashing import sha256_content


class Concepts:
    async def generate_concepts(self, request, profile):
        return {
            "options": [
                {
                    "id": "glass-archive",
                    "title": "The Glass Archive",
                    "logline": "Recover a stolen memory.",
                    "premise": "An archivist saves her city from rewritten memories.",
                    "genres": ["fantasy", "mystery"],
                }
            ]
        }


class CleanReview:
    async def review_concepts(self, concepts, profile):
        return {"passed": True, "blocking_issues": [], "warnings": [], "notes": []}


class BlockingThenClean:
    def __init__(self):
        self.calls = 0

    async def review_concepts(self, concepts, profile):
        self.calls += 1
        if self.calls == 1:
            return {
                "passed": False,
                "summary": "Needs a revision.",
                "blocking_issues": [{"code": "missing-hook", "message": "Add a hook."}],
                "warnings": [],
                "notes": [],
                "suggested_actions": [{"code": "regenerate", "message": "Regenerate."}],
            }
        return {
            "passed": True,
            "summary": "Ready for author selection.",
            "blocking_issues": [],
            "warnings": [],
            "notes": [],
        }


class FailingConcepts:
    async def generate_concepts(self, request, profile):
        raise RuntimeError("upstream secret response")


class MultilineConcepts:
    async def generate_concepts(self, request, profile):
        return {
            "options": [
                {
                    "id": "multiline",
                    "title": "Bad\ntitle",
                    "logline": "Safe logline.",
                    "premise": "Safe premise.",
                    "genres": ["fiction"],
                }
            ]
        }


class CommaDelimitedGenreConcepts:
    async def generate_concepts(self, request, profile):
        return {
            "options": [
                {
                    "id": "ambiguous-genre",
                    "title": "Ambiguous Genre",
                    "logline": "A safely bounded logline.",
                    "premise": "A safely bounded premise.",
                    "genres": ["fantasy, mystery"],
                }
            ]
        }


class EventBlockedConcepts:
    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self.entered, self.release = entered, release

    async def generate_concepts(self, request, profile):
        self.entered.set()
        await self.release.wait()
        return await Concepts().generate_concepts(request, profile)


class MalformedChiefReview:
    async def review_concepts(self, concepts, profile):
        return {"passed": True, "blocking_issues": [{"code": "bad", "message": "bad"}]}


class FailingChiefReview:
    async def review_concepts(self, concepts, profile):
        raise RuntimeError("chief editor raw secret")


def composition() -> ProjectCreationComposition:
    return ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(CleanReview()))


@pytest.mark.integration
@pytest.mark.anyio
async def test_multiline_concept_output_fails_before_durable_writes(async_session, tmp_path):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(
        async_session,
        ProjectCreationComposition(ConceptAgent(MultilineConcepts()), ChiefEditor(CleanReview())),
    )
    with pytest.raises(ProviderInvalidOutputError):
        await service.start(
            created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
        )
    for model in (
        WorkflowRun,
        Document,
        DocumentVersion,
        ReviewReport,
        ActionRequest,
        WorkflowCheckpoint,
        WorkflowEvent,
    ):
        assert await async_session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_comma_delimited_provider_genre_fails_before_durable_writes(async_session, tmp_path):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(
        async_session,
        ProjectCreationComposition(
            ConceptAgent(CommaDelimitedGenreConcepts()), ChiefEditor(CleanReview())
        ),
    )
    with pytest.raises(ProviderInvalidOutputError):
        await service.start(
            created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
        )
    for model in (
        WorkflowRun,
        Document,
        DocumentVersion,
        ReviewReport,
        ActionRequest,
        WorkflowCheckpoint,
        WorkflowEvent,
    ):
        assert await async_session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_normal_multi_genre_artifact_round_trips_and_selection_works(async_session, tmp_path):
    from app.agents.persistence import parse_concept_options_markdown

    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    concept_document = await async_session.scalar(
        select(Document).where(
            Document.project_id == created.id, Document.path == "pitch/concept_options.md"
        )
    )
    assert concept_document is not None
    artifact = await DocumentService(async_session).read_current_content(concept_document.id)
    assert parse_concept_options_markdown(artifact.content).options[0].genres == ["fantasy", "mystery"]

    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    await service.resolve_action(
        created.id,
        started.workflow_run_id,
        gate.pending_action.id,
        decision="select",
        option_id="glass-archive",
    )
    selected = await async_session.scalar(
        select(Document).where(
            Document.project_id == created.id, Document.path == "pitch/selected_concept.md"
        )
    )
    assert selected is not None
    selected_content = await DocumentService(async_session).read_current_content(selected.id)
    assert "Genres: fantasy, mystery" in selected_content.content


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("transition", ["start", "regenerate", "select", "fuse"])
async def test_start_commit_acknowledgement_loss_is_safe(
    async_session, tmp_path, monkeypatch, transition
):
    created = await project(async_session, tmp_path / transition)
    review = BlockingThenClean() if transition == "regenerate" else CleanReview()
    service = ProjectCreationService(
        async_session,
        ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review)),
    )
    project_id = created.id
    run_id = action_id = None
    if transition != "start":
        started = await service.start(
            created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
        )
        gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
        run_id, action_id = started.workflow_run_id, gate.pending_action.id

    original_commit = async_session.commit
    async def committed_then_lost():
        await original_commit()
        raise RuntimeError("distinctive commit acknowledgement loss")

    monkeypatch.setattr(async_session, "commit", committed_then_lost)
    with pytest.raises(DocumentCommitIndeterminateError) as error:
        if transition == "start":
            await service.start(
                created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
            )
        else:
            kwargs = (
                {"decision": "regenerate"}
                if transition == "regenerate"
                else (
                    {"decision": "select", "option_id": "glass-archive"}
                    if transition == "select"
                    else {"decision": "fuse", "fused_concept": "author fusion"}
                )
            )
            await service.resolve_action(created.id, run_id, action_id, **kwargs)
    assert "distinctive commit acknowledgement loss" not in str(error.value)
    monkeypatch.setattr(async_session, "commit", original_commit)
    async_session.expire_all()
    if transition == "start":
        run = await async_session.scalar(
            select(WorkflowRun).where(WorkflowRun.project_id == project_id)
        )
        action = await async_session.scalar(
            select(ActionRequest).where(ActionRequest.workflow_run_id == run.id)
        )
        document = await async_session.scalar(
            select(Document).where(
                Document.project_id == project_id, Document.path == "pitch/concept_options.md"
            )
        )
        report = await async_session.scalar(
            select(ReviewReport).where(ReviewReport.workflow_run_id == run.id)
        )
        assert run.status == "concept_options" and run.awaiting_user
        assert action.status == "pending" and action.request_type == "project_creation_concept_selection"
        assert action.options == ["glass-archive"]
        assert action.metadata_ == {
            "review_severity": "clean",
            "review_report_id": str(report.id),
            "concept_document_id": str(document.id),
            "concept_version_id": str(document.current_version_id),
        }
        assert document.current_version_id == report.target_version_id
        assert report.passed and report.blocking_issues == [] and report.raw_report == {}
    elif transition == "regenerate":
        original = await async_session.get(ActionRequest, action_id)
        actions = list(
            await async_session.scalars(
                select(ActionRequest).where(ActionRequest.workflow_run_id == run_id)
            )
        )
        document = await async_session.scalar(
            select(Document).where(
                Document.project_id == project_id, Document.path == "pitch/concept_options.md"
            )
        )
        assert original.status == "revised" and original.user_decision == "regeneration_requested"
        assert str(document.current_version_id) != original.metadata_["concept_version_id"]
        current_gate = next(
            item
            for item in actions
            if item.status == "pending"
            and item.request_type == "project_creation_concept_selection"
            and item.metadata_["concept_version_id"] == str(document.current_version_id)
        )
        current_report = await async_session.get(
            ReviewReport, UUID(current_gate.metadata_["review_report_id"])
        )
        current_version = await async_session.get(DocumentVersion, document.current_version_id)
        assert current_report.passed and current_report.blocking_issues == []
        assert current_version.source == "concept_agent" and current_version.content_hash
    else:
        action = await async_session.get(ActionRequest, action_id)
        run = await async_session.get(WorkflowRun, run_id)
        document = await async_session.scalar(
            select(Document).where(
                Document.project_id == project_id, Document.path == "pitch/selected_concept.md"
            )
        )
        version = await async_session.get(DocumentVersion, document.current_version_id)
        assert action.status == "approved"
        assert run.status == "concept_selected" and run.completed_at is not None
        assert document.current_version_id == version.id
        assert version.workflow_run_id == run_id and version.source == "user" and version.content_hash


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("gate", ["selection", "regeneration"])
@pytest.mark.parametrize(
    "field",
    [
        "prompt",
        "options",
        "default_option",
        "chapter_id",
        "user_decision",
        "user_feedback",
        "resolved_by_id",
        "resolved_at",
        "expires_at",
    ],
)
async def test_issue65_pending_action_shape_corruption_fails_closed(
    async_session: AsyncSession, tmp_path: Path, gate: str, field: str
) -> None:
    created = await project(async_session, tmp_path / f"{gate}-{field}")
    review = BlockingThenClean() if gate == "regeneration" else CleanReview()
    service = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    user = User(username=f"issue65-{uuid4().hex}", display_name="Issue 65")
    chapter = Chapter(project_id=created.id, chapter_number=1)
    async_session.add_all((user, chapter))
    await async_session.commit()
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate_read = await service.get_project_creation_run(created.id, started.workflow_run_id)
    action = await async_session.get(ActionRequest, gate_read.pending_action.id)
    action_id = action.id
    corruptions = {
        "prompt": "unexpected prompt",
        "options": ["unexpected-option"],
        "default_option": "unexpected-option",
        "chapter_id": chapter.id,
        "user_decision": "unexpected decision",
        "user_feedback": "unexpected feedback",
        "resolved_by_id": user.id,
        "resolved_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC),
    }
    setattr(action, field, corruptions[field])
    await async_session.commit()

    with pytest.raises(WorkflowStateError):
        await service.get_project_creation_run(created.id, started.workflow_run_id)
    kwargs = (
        {"decision": "select", "option_id": "glass-archive"}
        if gate == "selection"
        else {"decision": "regenerate"}
    )
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(created.id, started.workflow_run_id, action_id, **kwargs)
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("gate", ["selection", "regeneration"])
@pytest.mark.parametrize(
    "field",
    [
        "reviewer_agent_role",
        "raw_report",
        "warnings",
        "notes",
        "suggested_actions",
        "passed",
        "blocking_issues",
    ],
)
async def test_issue65_report_shape_corruption_fails_closed(
    async_session: AsyncSession, tmp_path: Path, gate: str, field: str
) -> None:
    created = await project(async_session, tmp_path / f"report-{gate}-{field}")
    review = BlockingThenClean() if gate == "regeneration" else CleanReview()
    service = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate_read = await service.get_project_creation_run(created.id, started.workflow_run_id)
    action = await async_session.get(ActionRequest, gate_read.pending_action.id)
    action_id = action.id
    report = await async_session.get(ReviewReport, UUID(action.metadata_["review_report_id"]))
    corruptions = {
        "reviewer_agent_role": "untrusted_reviewer",
        "raw_report": {"private": "provider detail"},
        "warnings": [{"code": "bad", "message": "Bad."}, "not-an-issue"],
        "notes": [{"code": "bad", "message": "Bad."}, "not-an-issue"],
        "suggested_actions": [{"code": "bad", "message": "Bad."}, "not-an-issue"],
        "passed": not report.passed,
        "blocking_issues": [] if gate == "regeneration" else [{"code": "bad", "message": "Bad."}],
    }
    setattr(report, field, corruptions[field])
    await async_session.commit()

    with pytest.raises(WorkflowStateError):
        await service.get_project_creation_run(created.id, started.workflow_run_id)
    kwargs = (
        {"decision": "select", "option_id": "glass-archive"}
        if gate == "selection"
        else {"decision": "regenerate"}
    )
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(created.id, started.workflow_run_id, action_id, **kwargs)
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("gate", ["selection", "regeneration"])
@pytest.mark.parametrize("artifact", ["missing", "corrupt"])
async def test_issue65_artifact_read_or_parse_failure_is_safe_and_fails_closed(
    async_session: AsyncSession, tmp_path: Path, gate: str, artifact: str, monkeypatch
) -> None:
    created = await project(async_session, tmp_path / f"artifact-{gate}-{artifact}")
    review = BlockingThenClean() if gate == "regeneration" else CleanReview()
    service = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate_read = await service.get_project_creation_run(created.id, started.workflow_run_id)
    action = await async_session.get(ActionRequest, gate_read.pending_action.id)
    action_id = action.id

    async def broken_read(*args, **kwargs):
        if artifact == "missing":
            raise OSError("private provider/file detail")
        return "# corrupted artifact\n"

    monkeypatch.setattr(DocumentService, "read_version_content", broken_read)
    with pytest.raises(WorkflowStateError) as error:
        await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert "private provider/file detail" not in str(error.value)
    kwargs = (
        {"decision": "select", "option_id": "glass-archive"}
        if gate == "selection"
        else {"decision": "regenerate"}
    )
    with pytest.raises(WorkflowStateError) as error:
        await service.resolve_action(created.id, started.workflow_run_id, action_id, **kwargs)
    assert "private provider/file detail" not in str(error.value)
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"


async def project(session: AsyncSession, root: Path):
    return await ProjectService(session, ProjectWorkspace(root)).create_project(
        slug=f"issue65-{root.name}", title="Issue 65"
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_regeneration_releases_workflow_and_action_locks_before_provider_call(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    created = await project(async_session, tmp_path)
    review = BlockingThenClean()
    initial = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    started = await initial.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await initial.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None

    provider_entered, release_provider = asyncio.Event(), asyncio.Event()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    resolver_session = sessions()
    competing_session = sessions()
    task: asyncio.Task[ProjectCreationState] | None = None
    try:
        resolver = ProjectCreationService(
            resolver_session,
            ProjectCreationComposition(
                ConceptAgent(EventBlockedConcepts(provider_entered, release_provider)),
                ChiefEditor(review),
            ),
        )
        task = asyncio.create_task(
            resolver.resolve_action(
                created.id, started.workflow_run_id, gate.pending_action.id, decision="regenerate"
            )
        )
        await asyncio.wait_for(provider_entered.wait(), timeout=2)

        async with competing_session.begin():
            assert await competing_session.scalar(
                select(WorkflowRun)
                .where(WorkflowRun.id == started.workflow_run_id)
                .with_for_update(nowait=True)
            ) is not None
            assert await competing_session.scalar(
                select(ActionRequest)
                .where(ActionRequest.id == gate.pending_action.id)
                .with_for_update(nowait=True)
            ) is not None

        release_provider.set()
        resolved = await asyncio.wait_for(task, timeout=2)
        assert resolved.status is ProjectCreationStatus.CONCEPT_OPTIONS
    finally:
        release_provider.set()
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await resolver_session.close()
        await competing_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_regeneration_revalidates_gate_after_unlocked_provider_interval(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    created = await project(async_session, tmp_path)
    review = BlockingThenClean()
    initial = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    started = await initial.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await initial.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    before = tuple(
        [
            await async_session.scalar(select(func.count()).select_from(model))
            for model in (DocumentVersion, ReviewReport, ActionRequest, WorkflowCheckpoint, WorkflowEvent)
        ]
    )

    provider_entered, release_provider = asyncio.Event(), asyncio.Event()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    resolver_session = sessions()
    racing_session = sessions()
    task: asyncio.Task[ProjectCreationState] | None = None
    try:
        resolver = ProjectCreationService(
            resolver_session,
            ProjectCreationComposition(
                ConceptAgent(EventBlockedConcepts(provider_entered, release_provider)),
                ChiefEditor(review),
            ),
        )
        task = asyncio.create_task(
            resolver.resolve_action(
                created.id, started.workflow_run_id, gate.pending_action.id, decision="regenerate"
            )
        )
        await asyncio.wait_for(provider_entered.wait(), timeout=2)
        action = await racing_session.get(ActionRequest, gate.pending_action.id)
        assert action is not None
        action.status = "approved"
        await asyncio.wait_for(racing_session.commit(), timeout=1)

        release_provider.set()
        with pytest.raises(WorkflowStateError):
            await asyncio.wait_for(task, timeout=2)
        assert before == tuple(
            [
                await async_session.scalar(select(func.count()).select_from(model))
                for model in (
                    DocumentVersion,
                    ReviewReport,
                    ActionRequest,
                    WorkflowCheckpoint,
                    WorkflowEvent,
                )
            ]
        )
    finally:
        release_provider.set()
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await resolver_session.close()
        await racing_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse"])
async def test_issue65_snapshot_hash_mismatch_fails_closed_for_read_and_selection(
    async_session: AsyncSession, tmp_path: Path, decision: str
) -> None:
    created = await project(async_session, tmp_path / decision)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    action = await async_session.get(ActionRequest, gate.pending_action.id)
    action_id, project_id = action.id, created.id
    version = await async_session.get(DocumentVersion, UUID(action.metadata_["concept_version_id"]))
    assert version is not None and version.snapshot_path is not None
    (tmp_path / decision / created.slug / version.snapshot_path).write_text(
        "# Concept Options\n\n## Option `glass-archive`: Altered prose\n\n"
        "A different valid logline.\n\n"
        "A different valid premise.\n\n"
        "Genres: fantasy, mystery\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowStateError):
        await service.get_project_creation_run(created.id, started.workflow_run_id)
    kwargs = (
        {"option_id": "glass-archive"}
        if decision == "select"
        else {"fused_concept": "A valid author fusion."}
    )
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            project_id, started.workflow_run_id, action_id, decision=decision, **kwargs
        )
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"
    assert await async_session.scalar(
        select(Document).where(
            Document.project_id == project_id, Document.path == "pitch/selected_concept.md"
        )
    ) is None


@pytest.mark.integration
@pytest.mark.anyio
async def test_selection_writes_the_validated_selected_option_not_only_its_id(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id,
        ConceptAgentRequest(project_id=created.id, user_seed="private seed"),
    )
    run = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert run.pending_action is not None
    await service.resolve_action(
        created.id,
        started.workflow_run_id,
        run.pending_action.id,
        decision="select",
        option_id="glass-archive",
    )
    selected = await async_session.scalar(
        select(Document).where(
            Document.project_id == created.id, Document.path == "pitch/selected_concept.md"
        )
    )
    assert selected is not None
    content = await DocumentService(async_session).read_current_content(selected.id)
    assert "The Glass Archive" in content.content
    assert "Recover a stolen memory." in content.content
    assert (
        await async_session.scalar(
            select(DocumentVersion).where(DocumentVersion.document_id == selected.id)
        )
    ) is not None
    assert (
        await async_session.scalar(
            select(ReviewReport).where(ReviewReport.workflow_run_id == started.workflow_run_id)
        )
    ) is not None
    actions = list(await async_session.scalars(select(ActionRequest)))
    checkpoints = list(await async_session.scalars(select(WorkflowCheckpoint)))
    events = list(await async_session.scalars(select(WorkflowEvent)))
    assert all(
        "private seed" not in str(item.metadata_) and "private seed" not in item.prompt
        for item in actions
    )
    assert all("private seed" not in str(item.state_json) for item in checkpoints)
    assert all("private seed" not in str(item.payload) for item in events)


@pytest.mark.integration
@pytest.mark.anyio
async def test_blocking_review_regenerates_once_and_replay_fails_closed(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    created = await project(async_session, tmp_path)
    review = BlockingThenClean()
    service = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    first = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert first.pending_action is not None
    assert first.pending_action.type == "project_creation_concept_regeneration"
    assert first.pending_action.allowed_decisions == ("regenerate", "feedback")
    assert first.pending_action.review_severity == "blocking"
    await service.resolve_action(
        created.id,
        started.workflow_run_id,
        first.pending_action.id,
        decision="feedback",
        feedback="private feedback",
    )
    second = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert (
        second.pending_action is not None
        and second.pending_action.type == "project_creation_concept_selection"
    )
    old = await async_session.get(ActionRequest, first.pending_action.id)
    assert old is not None and old.status == "revised" and old.user_feedback is None
    versions = list(await async_session.scalars(select(DocumentVersion)))
    assert len(versions) == 2
    checkpoints = list(
        await async_session.scalars(
            select(WorkflowCheckpoint).where(
                WorkflowCheckpoint.workflow_run_id == started.workflow_run_id
            )
        )
    )
    assert len(checkpoints) == 3
    with pytest.raises(Exception):
        await service.resolve_action(
            created.id, started.workflow_run_id, first.pending_action.id, decision="regenerate"
        )
    assert len(list(await async_session.scalars(select(DocumentVersion)))) == 2


@pytest.mark.integration
@pytest.mark.anyio
async def test_provider_failure_leaves_no_durable_artifact_and_session_is_usable(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    created = await project(async_session, tmp_path)
    created_id = created.id
    service = ProjectCreationService(
        async_session,
        ProjectCreationComposition(ConceptAgent(FailingConcepts()), ChiefEditor(CleanReview())),
    )
    with pytest.raises(Exception) as error:
        await service.start(
            created_id, ConceptAgentRequest(project_id=created_id, user_seed="seed-not-to-store")
        )
    assert "secret" not in str(error.value)
    assert await async_session.scalar(select(func.count()).select_from(WorkflowRun)) == 0
    assert await async_session.scalar(select(func.count()).select_from(Document)) == 0
    assert await async_session.scalar(select(func.count()).select_from(ActionRequest)) == 0
    # A new transaction can start normally with the same AsyncSession.
    healthy = ProjectCreationService(async_session, composition())
    assert (
        await healthy.start(
            created_id, ConceptAgentRequest(project_id=created_id, user_seed="new seed")
        )
    ).workflow_run_id


@pytest.mark.integration
@pytest.mark.anyio
async def test_issue65_start_releases_preprovider_transaction_and_losing_race_leaves_no_artifacts(
    async_session: AsyncSession, integration_database_url: str, tmp_path: Path
) -> None:
    created = await project(async_session, tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    blocked_session = sessions()
    competing_session = sessions()
    observer_session = sessions()
    blocked_task: asyncio.Task[object] | None = None
    artifact_models = (
        WorkflowRun,
        Document,
        DocumentVersion,
        ReviewReport,
        ActionRequest,
        WorkflowCheckpoint,
        WorkflowEvent,
    )

    async def artifact_counts(session: AsyncSession) -> tuple[int, ...]:
        return tuple(
            [await session.scalar(select(func.count()).select_from(model)) for model in artifact_models]
        )

    try:
        blocked = ProjectCreationService(
            blocked_session,
            ProjectCreationComposition(
                ConceptAgent(EventBlockedConcepts(entered, release)), ChiefEditor(CleanReview())
            ),
        )
        blocked_task = asyncio.create_task(
            blocked.start(created.id, ConceptAgentRequest(project_id=created.id, user_seed="blocked"))
        )
        await asyncio.wait_for(entered.wait(), timeout=2)

        assert not blocked_session.in_transaction()
        async with observer_session.begin():
            assert await observer_session.scalar(
                select(Project).where(Project.id == created.id).with_for_update(nowait=True)
            ) is not None

        competing = ProjectCreationService(competing_session, composition())
        winner = await asyncio.wait_for(
            competing.start(created.id, ConceptAgentRequest(project_id=created.id, user_seed="winner")),
            timeout=2,
        )
        assert winner.workflow_run_id
        before_counts = await artifact_counts(observer_session)
        workspace_files = tuple(
            sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
        )

        release.set()
        with pytest.raises(ConflictError):
            await asyncio.wait_for(blocked_task, timeout=2)

        assert await artifact_counts(observer_session) == before_counts
        assert tuple(
            sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
        ) == workspace_files
        active_runs = list(
            await observer_session.scalars(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == created.id,
                    WorkflowRun.workflow_type == ProjectCreationService._WORKFLOW_TYPE,
                )
            )
        )
        assert [run.id for run in active_runs] == [winner.workflow_run_id]
        persisted_project = await observer_session.get(Project, created.id)
        assert persisted_project is not None
        assert persisted_project.current_workflow_id == winner.workflow_run_id

        await observer_session.rollback()
        async with observer_session.begin():
            assert await observer_session.scalar(
                select(Project).where(Project.id == created.id).with_for_update(nowait=True)
            ) is not None
    finally:
        release.set()
        if blocked_task is not None and not blocked_task.done():
            blocked_task.cancel()
            with suppress(asyncio.CancelledError):
                await blocked_task
        await blocked_session.close()
        await competing_session.close()
        await observer_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_stage_failure_rolls_back_every_durable_row(
    async_session, tmp_path, monkeypatch
):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())

    async def fail(*args, **kwargs):
        raise RuntimeError("post-stage failure")

    monkeypatch.setattr(service, "_record_review_and_gate", fail)
    with pytest.raises(RuntimeError):
        await service.start(
            created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
        )
    for model in (
        WorkflowRun,
        Document,
        DocumentVersion,
        ReviewReport,
        ActionRequest,
        WorkflowCheckpoint,
        WorkflowEvent,
    ):
        assert await async_session.scalar(select(func.count()).select_from(model)) == 0
    assert not (tmp_path / "pitch").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_post_commit_file_failure_is_safe_and_durable(async_session, tmp_path, monkeypatch):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    monkeypatch.setattr(
        DocumentService,
        "write_staged_files",
        lambda *a: (_ for _ in ()).throw(OSError("private path")),
    )
    with pytest.raises(DocumentCommitIndeterminateError) as error:
        await service.start(
            created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
        )
    assert "private path" not in str(error.value)
    assert await async_session.scalar(select(func.count()).select_from(WorkflowRun)) == 1
    assert await async_session.scalar(select(func.count()).select_from(Document)) == 1
    assert await async_session.scalar(select(func.count()).select_from(ActionRequest)) == 1


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("key", ["review_report_id", "concept_document_id", "concept_version_id"])
@pytest.mark.parametrize("decision", ["select", "fuse"])
async def test_selection_binding_corruption_fails_closed(async_session, tmp_path, key, decision):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    read = await service.get_project_creation_run(created.id, started.workflow_run_id)
    action = await async_session.get(ActionRequest, read.pending_action.id)
    action_id = action.id
    action.metadata_ = {**action.metadata_, key: str(uuid4())}
    await async_session.commit()
    with pytest.raises(WorkflowStateError):
        await service.get_project_creation_run(created.id, started.workflow_run_id)
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            created.id,
            started.workflow_run_id,
            action_id,
            decision=decision,
            **(
                {"option_id": "glass-archive"}
                if decision == "select"
                else {"fused_concept": "author fusion"}
            ),
        )
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"
    assert (
        await async_session.scalar(
            select(Document).where(Document.path == "pitch/selected_concept.md")
        )
        is None
    )


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse"])
async def test_public_read_rejects_corrupt_selection_binding(async_session, tmp_path, decision):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    action = await async_session.get(ActionRequest, gate.pending_action.id)
    action.metadata_ = {**action.metadata_, "concept_version_id": str(uuid4())}
    await async_session.commit()
    with pytest.raises(WorkflowStateError):
        await service.get_project_creation_run(created.id, started.workflow_run_id)


@pytest.mark.integration
@pytest.mark.anyio
async def test_public_read_rejects_corrupt_regeneration_binding(async_session, tmp_path):
    created = await project(async_session, tmp_path)
    review = BlockingThenClean()
    service = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    action = await async_session.get(ActionRequest, gate.pending_action.id)
    action.metadata_ = {**action.metadata_, "review_severity": "warning"}
    await async_session.commit()
    with pytest.raises(WorkflowStateError):
        await service.get_project_creation_run(created.id, started.workflow_run_id)


@pytest.mark.integration
@pytest.mark.anyio
async def test_selected_terminal_allows_second_start(async_session, tmp_path):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    first = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="one")
    )
    gate = await service.get_project_creation_run(created.id, first.workflow_run_id)
    await service.resolve_action(
        created.id,
        first.workflow_run_id,
        gate.pending_action.id,
        decision="select",
        option_id="glass-archive",
    )
    selected = await async_session.get(WorkflowRun, first.workflow_run_id)
    assert (
        selected is not None
        and selected.status == "concept_selected"
        and selected.completed_at is not None
    )
    second = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="two")
    )
    assert second.workflow_run_id != first.workflow_run_id


@pytest.mark.integration
@pytest.mark.anyio
async def test_regeneration_stage_failure_rolls_back(async_session, tmp_path, monkeypatch):
    created = await project(async_session, tmp_path)
    review = BlockingThenClean()
    service = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    action = await async_session.get(ActionRequest, gate.pending_action.id)
    document = await async_session.scalar(
        select(Document).where(
            Document.project_id == created.id, Document.path == "pitch/concept_options.md"
        )
    )
    version_id = document.current_version_id
    action_id, document_id, run_id = action.id, document.id, started.workflow_run_id
    models = (DocumentVersion, ReviewReport, ActionRequest, WorkflowCheckpoint, WorkflowEvent)
    counts = tuple(
        [await async_session.scalar(select(func.count()).select_from(model)) for model in models]
    )

    async def fail(*args, **kwargs):
        raise RuntimeError("post-stage")

    monkeypatch.setattr(service, "_record_review_and_gate", fail)
    with pytest.raises(RuntimeError):
        await service.resolve_action(created.id, run_id, action_id, decision="regenerate")
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"
    assert (await async_session.get(Document, document_id)).current_version_id == version_id
    assert counts == tuple(
        [await async_session.scalar(select(func.count()).select_from(model)) for model in models]
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_selection_rejects_valid_foreign_report_binding(async_session, tmp_path):
    first, second = (
        await project(async_session, tmp_path / "first"),
        await project(async_session, tmp_path / "second"),
    )
    service = ProjectCreationService(async_session, composition())
    one = await service.start(first.id, ConceptAgentRequest(project_id=first.id, user_seed="one"))
    two = await service.start(second.id, ConceptAgentRequest(project_id=second.id, user_seed="two"))
    gate = await service.get_project_creation_run(first.id, one.workflow_run_id)
    foreign_action = await service.get_project_creation_run(second.id, two.workflow_run_id)
    action = await async_session.get(ActionRequest, gate.pending_action.id)
    other = await async_session.get(ActionRequest, foreign_action.pending_action.id)
    action_id, project_id = action.id, first.id
    action.metadata_ = dict(other.metadata_)
    await async_session.commit()
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            project_id, one.workflow_run_id, action_id, decision="select", option_id="glass-archive"
        )
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"
    assert (
        await async_session.scalar(
            select(Document).where(
                Document.project_id == project_id, Document.path == "pitch/selected_concept.md"
            )
        )
        is None
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_selected_document_appends_for_later_select_and_fuse(async_session, tmp_path):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    for seed, decision in (("one", "select"), ("two", "select"), ("three", "fuse")):
        started = await service.start(
            created.id, ConceptAgentRequest(project_id=created.id, user_seed=seed)
        )
        gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
        kwargs = (
            {"option_id": "glass-archive"}
            if decision == "select"
            else {"fused_concept": "Author fused premise."}
        )
        await service.resolve_action(
            created.id, started.workflow_run_id, gate.pending_action.id, decision=decision, **kwargs
        )
    selected = await async_session.scalar(
        select(Document).where(
            Document.project_id == created.id, Document.path == "pitch/selected_concept.md"
        )
    )
    assert (
        await async_session.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == selected.id)
        )
        == 3
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_fuse_rejects_valid_foreign_binding(async_session, tmp_path):
    first, second = (
        await project(async_session, tmp_path / "first"),
        await project(async_session, tmp_path / "second"),
    )
    service = ProjectCreationService(async_session, composition())
    one = await service.start(first.id, ConceptAgentRequest(project_id=first.id, user_seed="one"))
    two = await service.start(second.id, ConceptAgentRequest(project_id=second.id, user_seed="two"))
    a = await service.get_project_creation_run(first.id, one.workflow_run_id)
    b = await service.get_project_creation_run(second.id, two.workflow_run_id)
    action = await async_session.get(ActionRequest, a.pending_action.id)
    other = await async_session.get(ActionRequest, b.pending_action.id)
    action_id = action.id
    action.metadata_ = dict(other.metadata_)
    await async_session.commit()
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            first.id, one.workflow_run_id, action_id, decision="fuse", fused_concept="fusion"
        )
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse"])
async def test_selection_post_commit_file_failure_is_safe(
    async_session, tmp_path, monkeypatch, decision
):
    created = await project(async_session, tmp_path / decision)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    monkeypatch.setattr(
        DocumentService,
        "write_staged_files",
        lambda *a: (_ for _ in ()).throw(OSError("private filesystem path")),
    )
    kwargs = {"option_id": "glass-archive"} if decision == "select" else {"fused_concept": "fusion"}
    with pytest.raises(DocumentCommitIndeterminateError) as error:
        await service.resolve_action(
            created.id, started.workflow_run_id, gate.pending_action.id, decision=decision, **kwargs
        )
    assert "private filesystem path" not in str(error.value)
    assert (await async_session.get(ActionRequest, gate.pending_action.id)).status == "approved"


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse"])
async def test_selection_pre_commit_failure_rolls_back(
    async_session, tmp_path, monkeypatch, decision
):
    created = await project(async_session, tmp_path / decision)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    action_id, run_id = gate.pending_action.id, started.workflow_run_id
    before = tuple(
        [
            await async_session.scalar(select(func.count()).select_from(m))
            for m in (Document, DocumentVersion, ActionRequest, WorkflowCheckpoint, WorkflowEvent)
        ]
    )

    def fail(*args, **kwargs):
        raise RuntimeError("precommit")

    monkeypatch.setattr(service, "_persist_transition", fail)
    kwargs = {"option_id": "glass-archive"} if decision == "select" else {"fused_concept": "fused"}
    with pytest.raises(RuntimeError):
        await service.resolve_action(created.id, run_id, action_id, decision=decision, **kwargs)
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"
    run = await async_session.get(WorkflowRun, run_id)
    assert run.status == "concept_options" and run.awaiting_user
    assert before == tuple(
        [
            await async_session.scalar(select(func.count()).select_from(m))
            for m in (Document, DocumentVersion, ActionRequest, WorkflowCheckpoint, WorkflowEvent)
        ]
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_regeneration_post_commit_file_failure_is_safe(async_session, tmp_path, monkeypatch):
    created = await project(async_session, tmp_path)
    review = BlockingThenClean()
    service = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    monkeypatch.setattr(
        DocumentService,
        "write_staged_files",
        lambda *a: (_ for _ in ()).throw(OSError("private filesystem path")),
    )
    with pytest.raises(DocumentCommitIndeterminateError) as error:
        await service.resolve_action(
            created.id, started.workflow_run_id, gate.pending_action.id, decision="regenerate"
        )
    assert "private filesystem path" not in str(error.value)
    assert (await async_session.get(ActionRequest, gate.pending_action.id)).status == "revised"


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse"])
async def test_mismatched_revision_checkpoint_selection_action_fails_closed(
    async_session, tmp_path, decision
):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    payload = {
        "version": 1,
        "status": "revision_required",
        "current_node": "concept_revision",
        "awaiting_user": True,
        "action_request_id": str(gate.pending_action.id),
    }
    await async_session.execute(
        WorkflowCheckpoint.__table__.update()
        .where(
            WorkflowCheckpoint.workflow_run_id == started.workflow_run_id,
            WorkflowCheckpoint.checkpoint_index == 1,
        )
        .values(state_json=payload)
    )
    await async_session.execute(
        WorkflowRun.__table__.update()
        .where(WorkflowRun.id == started.workflow_run_id)
        .values(status="revision_required", current_node="concept_revision", awaiting_user=True)
    )
    await async_session.commit()
    kwargs = {"option_id": "glass-archive"} if decision == "select" else {"fused_concept": "fuse"}
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            created.id, started.workflow_run_id, gate.pending_action.id, decision=decision, **kwargs
        )
    assert (await async_session.get(ActionRequest, gate.pending_action.id)).status == "pending"


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse"])
async def test_report_target_foreign_document_or_version_fails_closed(
    async_session, tmp_path, decision
):
    first, second = (
        await project(async_session, tmp_path / "one"),
        await project(async_session, tmp_path / "two"),
    )
    service = ProjectCreationService(async_session, composition())
    one = await service.start(first.id, ConceptAgentRequest(project_id=first.id, user_seed="one"))
    two = await service.start(second.id, ConceptAgentRequest(project_id=second.id, user_seed="two"))
    gate = await service.get_project_creation_run(first.id, one.workflow_run_id)
    action = await async_session.get(ActionRequest, gate.pending_action.id)
    action_id = action.id
    foreign_report = await async_session.scalar(
        select(ReviewReport).where(ReviewReport.workflow_run_id == two.workflow_run_id)
    )
    own_report = await async_session.scalar(
        select(ReviewReport).where(ReviewReport.workflow_run_id == one.workflow_run_id)
    )
    own_report.target_document_id, own_report.target_version_id = (
        foreign_report.target_document_id,
        foreign_report.target_version_id,
    )
    action.metadata_["concept_document_id"], action.metadata_["concept_version_id"] = (
        str(foreign_report.target_document_id),
        str(foreign_report.target_version_id),
    )
    await async_session.commit()
    kwargs = {"option_id": "glass-archive"} if decision == "select" else {"fused_concept": "fuse"}
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            first.id, one.workflow_run_id, action_id, decision=decision, **kwargs
        )
    assert (await async_session.get(ActionRequest, action_id)).status == "pending"


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse"])
async def test_report_target_stale_same_project_prior_run_version_fails_closed(
    async_session, tmp_path, decision
):
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())

    first = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="first")
    )
    first_gate = await service.get_project_creation_run(created.id, first.workflow_run_id)
    concept_document = await async_session.scalar(
        select(Document).where(
            Document.project_id == created.id, Document.path == "pitch/concept_options.md"
        )
    )
    stale_version_id = concept_document.current_version_id
    await service.resolve_action(
        created.id,
        first.workflow_run_id,
        first_gate.pending_action.id,
        decision="select",
        option_id="glass-archive",
    )

    second = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="second")
    )
    second_gate = await service.get_project_creation_run(created.id, second.workflow_run_id)
    action = await async_session.get(ActionRequest, second_gate.pending_action.id)
    report = await async_session.scalar(
        select(ReviewReport).where(ReviewReport.workflow_run_id == second.workflow_run_id)
    )
    selected = await async_session.scalar(
        select(Document).where(
            Document.project_id == created.id, Document.path == "pitch/selected_concept.md"
        )
    )
    action_id, selected_id, selected_version_id = (
        action.id,
        selected.id,
        selected.current_version_id,
    )

    report.target_document_id = concept_document.id
    report.target_version_id = stale_version_id
    action.metadata_["concept_document_id"] = str(concept_document.id)
    action.metadata_["concept_version_id"] = str(stale_version_id)
    await async_session.commit()

    kwargs = (
        {"option_id": "glass-archive"}
        if decision == "select"
        else {"fused_concept": "A fused author concept."}
    )
    with pytest.raises(WorkflowStateError):
        await service.get_project_creation_run(created.id, second.workflow_run_id)
    with pytest.raises(WorkflowStateError):
        await service.resolve_action(
            created.id,
            second.workflow_run_id,
            action_id,
            decision=decision,
            **kwargs,
        )

    assert (await async_session.get(ActionRequest, action_id)).status == "pending"
    assert (
        await async_session.get(Document, selected_id)
    ).current_version_id == selected_version_id


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse"])
async def test_selection_rechecks_locked_concept_binding_after_document_race(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    monkeypatch,
    decision: str,
) -> None:
    created = await project(async_session, tmp_path / decision)
    initial = ProjectCreationService(async_session, composition())
    started = await initial.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    project_id = created.id
    gate = await initial.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    concept_document = await async_session.scalar(
        select(Document).where(
            Document.project_id == project_id, Document.path == "pitch/concept_options.md"
        )
    )
    assert concept_document is not None and concept_document.current_version_id is not None
    concept_document_id = concept_document.id

    entered, release = asyncio.Event(), asyncio.Event()
    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    resolver_session = sessions()
    racing_session = sessions()
    resolver = ProjectCreationService(resolver_session, composition())
    original_load = resolver._load_locked_issue65_action

    async def block_after_initial_validation(*args, **kwargs):
        loaded = await original_load(*args, **kwargs)
        entered.set()
        await release.wait()
        return loaded

    monkeypatch.setattr(resolver, "_load_locked_issue65_action", block_after_initial_validation)
    task: asyncio.Task[ProjectCreationState] | None = None
    try:
        kwargs = (
            {"option_id": "glass-archive"}
            if decision == "select"
            else {"fused_concept": "A valid author fusion."}
        )
        task = asyncio.create_task(
            resolver.resolve_action(
                project_id,
                started.workflow_run_id,
                gate.pending_action.id,
                decision=decision,
                **kwargs,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)

        racing_document = await racing_session.get(Document, concept_document_id)
        assert racing_document is not None and racing_document.current_version_id is not None
        current = await DocumentService(racing_session).read_current_content(racing_document.id)
        await DocumentService(racing_session).stage_write_document(
            document_id=racing_document.id,
            content=current.content,
            source=DocumentSource.CONCEPT_AGENT,
            expected_current_version_id=racing_document.current_version_id,
            change_summary="Concurrent concept update",
        )
        await racing_session.commit()

        release.set()
        with pytest.raises(WorkflowStateError):
            await asyncio.wait_for(task, timeout=2)
        async_session.expire_all()
        action = await async_session.get(ActionRequest, gate.pending_action.id)
        assert action is not None and action.status == "pending"
        assert (
            await async_session.scalar(
                select(Document).where(
                    Document.project_id == project_id, Document.path == "pitch/selected_concept.md"
                )
            )
            is None
        )
        assert (
            await async_session.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.document_id == concept_document_id)
            )
            == 2
        )
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await resolver_session.close()
        await racing_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["select", "fuse"])
@pytest.mark.parametrize("selected_exists", [False, True])
async def test_selection_stage_failure_rolls_back_create_and_existing_document_paths(
    async_session: AsyncSession, tmp_path: Path, monkeypatch, decision: str, selected_exists: bool
) -> None:
    created = await project(async_session, tmp_path / f"{decision}-{selected_exists}")
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    project_id = created.id
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    existing_document = None
    if selected_exists:
        existing_document = await DocumentService(async_session).create_document(
            project_id=created.id,
            document_type=DocumentType.PITCH,
            title="Selected concept",
            path="pitch/selected_concept.md",
            content="# Existing selected concept\n",
            source=DocumentSource.USER,
        )
    action_id, run_id = gate.pending_action.id, started.workflow_run_id
    existing_document_id = existing_document.id if existing_document else None
    before = tuple(
        [
            await async_session.scalar(select(func.count()).select_from(model))
            for model in (Document, DocumentVersion, ActionRequest, WorkflowCheckpoint, WorkflowEvent)
        ]
    )
    existing_version_id = existing_document.current_version_id if existing_document else None

    async def fail_stage(*args, **kwargs):
        raise RuntimeError("selection stage failure")

    monkeypatch.setattr(
        DocumentService,
        "stage_write_document" if selected_exists else "stage_create_document",
        fail_stage,
    )
    kwargs = (
        {"option_id": "glass-archive"}
        if decision == "select"
        else {"fused_concept": "A valid author fusion."}
    )
    with pytest.raises(RuntimeError, match="selection stage failure"):
        await service.resolve_action(project_id, run_id, action_id, decision=decision, **kwargs)

    action = await async_session.get(ActionRequest, action_id)
    run = await async_session.get(WorkflowRun, run_id)
    assert action is not None and action.status == "pending"
    assert run is not None and run.status == "concept_options" and run.awaiting_user
    if existing_document is None:
        assert (
            await async_session.scalar(
                select(Document).where(
                    Document.project_id == project_id, Document.path == "pitch/selected_concept.md"
                )
            )
            is None
        )
    else:
        unchanged = await async_session.get(Document, existing_document_id)
        assert unchanged is not None and unchanged.current_version_id == existing_version_id
    assert before == tuple(
        [
            await async_session.scalar(select(func.count()).select_from(model))
            for model in (Document, DocumentVersion, ActionRequest, WorkflowCheckpoint, WorkflowEvent)
        ]
    )
    assert await async_session.scalar(select(WorkflowRun.id).where(WorkflowRun.id == run_id)) == run_id


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["invalid_decision", "replayed", "foreign", "corrupt_binding"])
async def test_regeneration_safe_failures_release_run_and_action_locks(
    async_session: AsyncSession,
    integration_database_url: str,
    tmp_path: Path,
    failure: str,
) -> None:
    """A reused resolver session must not retain preflight locks after a safe error."""
    created = await project(async_session, tmp_path / failure)
    review = BlockingThenClean()
    service = ProjectCreationService(
        async_session, ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(review))
    )
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    action_id = gate.pending_action.id

    if failure == "replayed":
        await service.resolve_action(
            created.id, started.workflow_run_id, action_id, decision="regenerate"
        )
    elif failure == "foreign":
        other = await project(async_session, tmp_path / "foreign-project")
        foreign_started = await service.start(
            other.id, ConceptAgentRequest(project_id=other.id, user_seed="other")
        )
        foreign_gate = await service.get_project_creation_run(other.id, foreign_started.workflow_run_id)
        assert foreign_gate.pending_action is not None
        action_id = foreign_gate.pending_action.id
    elif failure == "corrupt_binding":
        action = await async_session.get(ActionRequest, action_id)
        assert action is not None
        action.metadata_ = {**action.metadata_, "concept_version_id": str(uuid4())}
        await async_session.commit()

    engine = create_async_engine(integration_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    resolver_session, competing_session = sessions(), sessions()
    try:
        resolver = ProjectCreationService(resolver_session, composition())
        expected = NotFoundError if failure == "foreign" else WorkflowStateError
        kwargs = {"decision": "bad" if failure == "invalid_decision" else "regenerate"}
        with pytest.raises(expected):
            await resolver.resolve_action(created.id, started.workflow_run_id, action_id, **kwargs)

        async with competing_session.begin():
            assert await competing_session.scalar(
                select(WorkflowRun)
                .where(WorkflowRun.id == started.workflow_run_id)
                .with_for_update(nowait=True)
            ) is not None
            if failure != "foreign":
                assert await competing_session.scalar(
                    select(ActionRequest).where(ActionRequest.id == action_id).with_for_update(nowait=True)
                ) is not None
    finally:
        await resolver_session.close()
        await competing_session.close()
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ["missing_completed_at", "pending_gate"])
async def test_start_rejects_corrupt_terminal_run_without_new_artifacts(
    async_session: AsyncSession, tmp_path: Path, corruption: str
) -> None:
    created = await project(async_session, tmp_path / corruption)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    await service.resolve_action(
        created.id,
        started.workflow_run_id,
        gate.pending_action.id,
        decision="select",
        option_id="glass-archive",
    )
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    if corruption == "missing_completed_at":
        run.completed_at = None
    else:
        async_session.add(
            ActionRequest(
                workflow_run_id=run.id,
                project_id=created.id,
                request_type="injected_pending_gate",
                status="pending",
                prompt="",
                options=[],
                default_option=None,
            )
        )
    await async_session.commit()
    before = tuple(
        [
            await async_session.scalar(select(func.count()).select_from(model))
            for model in (
                WorkflowRun,
                Document,
                DocumentVersion,
                ReviewReport,
                ActionRequest,
                WorkflowCheckpoint,
                WorkflowEvent,
            )
        ]
    )
    workspace_files = tuple(sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")))

    with pytest.raises(WorkflowStateError):
        await service.start(
            created.id, ConceptAgentRequest(project_id=created.id, user_seed="next seed")
        )

    assert before == tuple(
        [
            await async_session.scalar(select(func.count()).select_from(model))
            for model in (
                WorkflowRun,
                Document,
                DocumentVersion,
                ReviewReport,
                ActionRequest,
                WorkflowCheckpoint,
                WorkflowEvent,
            )
        ]
    )
    assert workspace_files == tuple(
        sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_start_rejects_terminal_run_with_deleted_resolution_action(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    """A terminal run whose resolution action was deleted is corrupt and must block a new start."""
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    await service.resolve_action(
        created.id,
        started.workflow_run_id,
        gate.pending_action.id,
        decision="select",
        option_id="glass-archive",
    )
    # Corrupt the terminal run by deleting its resolution action.
    action = await async_session.get(ActionRequest, gate.pending_action.id)
    assert action is not None and action.status == "approved"
    await async_session.delete(action)
    await async_session.commit()

    before = tuple(
        [
            await async_session.scalar(select(func.count()).select_from(model))
            for model in (
                WorkflowRun,
                Document,
                DocumentVersion,
                ReviewReport,
                ActionRequest,
                WorkflowCheckpoint,
                WorkflowEvent,
            )
        ]
    )
    workspace_files = tuple(
        sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    )

    with pytest.raises(WorkflowStateError):
        await service.start(
            created.id, ConceptAgentRequest(project_id=created.id, user_seed="next seed")
        )

    assert before == tuple(
        [
            await async_session.scalar(select(func.count()).select_from(model))
            for model in (
                WorkflowRun,
                Document,
                DocumentVersion,
                ReviewReport,
                ActionRequest,
                WorkflowCheckpoint,
                WorkflowEvent,
            )
        ]
    )
    assert workspace_files == tuple(
        sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_fused_crlf_content_is_normalized_before_hash_and_file_write(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    created = await project(async_session, tmp_path)
    service = ProjectCreationService(async_session, composition())
    started = await service.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await service.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    await service.resolve_action(
        created.id,
        started.workflow_run_id,
        gate.pending_action.id,
        decision="fuse",
        fused_concept="A storm-bound city.\r\nIts archivist rewrites fate.",
    )
    document = await async_session.scalar(
        select(Document).where(
            Document.project_id == created.id, Document.path == "pitch/selected_concept.md"
        )
    )
    assert document is not None and document.current_version_id is not None
    version = await async_session.scalar(
        select(DocumentVersion).where(DocumentVersion.id == document.current_version_id)
    )
    assert version is not None and version.snapshot_path is not None
    current = await DocumentService(async_session).read_current_content(document.id)
    direct = (tmp_path / created.slug / document.path).read_bytes().decode("utf-8")
    snapshot = (tmp_path / created.slug / version.snapshot_path).read_bytes().decode("utf-8")
    assert "\r" not in direct and "\r" not in snapshot
    assert current.content == direct == snapshot
    assert sha256_content(direct) == version.content_hash


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["start", "regenerate"])
@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (MalformedChiefReview, ProviderInvalidOutputError),
        (FailingChiefReview, ProviderUnavailableError),
    ],
)
async def test_chief_editor_failures_are_safe_for_start_and_regeneration(
    async_session: AsyncSession,
    tmp_path: Path,
    operation: str,
    provider: type[MalformedChiefReview] | type[FailingChiefReview],
    expected: type[Exception],
) -> None:
    created = await project(async_session, tmp_path / operation / provider.__name__)
    if operation == "start":
        service = ProjectCreationService(
            async_session,
            ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(provider())),
        )
        before_files = tuple(sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")))
        with pytest.raises(expected) as error:
            await service.start(
                created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
            )
        assert "raw secret" not in str(error.value)
        for model in (
            WorkflowRun,
            Document,
            DocumentVersion,
            ReviewReport,
            ActionRequest,
            WorkflowCheckpoint,
            WorkflowEvent,
        ):
            assert await async_session.scalar(select(func.count()).select_from(model)) == 0
        assert before_files == tuple(
            sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
        )
        return

    initial = ProjectCreationService(
        async_session,
        ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(BlockingThenClean())),
    )
    started = await initial.start(
        created.id, ConceptAgentRequest(project_id=created.id, user_seed="seed")
    )
    gate = await initial.get_project_creation_run(created.id, started.workflow_run_id)
    assert gate.pending_action is not None
    before = tuple(
        [
            await async_session.scalar(select(func.count()).select_from(model))
            for model in (DocumentVersion, ReviewReport, ActionRequest, WorkflowCheckpoint, WorkflowEvent)
        ]
    )
    before_files = tuple(sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")))
    resolver = ProjectCreationService(
        async_session,
        ProjectCreationComposition(ConceptAgent(Concepts()), ChiefEditor(provider())),
    )
    with pytest.raises(expected) as error:
        await resolver.resolve_action(
            created.id, started.workflow_run_id, gate.pending_action.id, decision="regenerate"
        )
    assert "raw secret" not in str(error.value)
    assert before == tuple(
        [
            await async_session.scalar(select(func.count()).select_from(model))
            for model in (DocumentVersion, ReviewReport, ActionRequest, WorkflowCheckpoint, WorkflowEvent)
        ]
    )
    action = await async_session.get(ActionRequest, gate.pending_action.id)
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert action is not None and action.status == "pending"
    assert run is not None and run.status == "revision_required" and run.awaiting_user
    assert before_files == tuple(sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")))
