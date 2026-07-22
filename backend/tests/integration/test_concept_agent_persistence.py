from pathlib import Path
from uuid import uuid4

import pytest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import ConceptGenerationOutput, ConceptOption, persist_concept_generation_output
from app.core.errors import AgentOutputInvalidError, AppError, ConflictError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Document,
    DocumentSource,
    DocumentType,
    DocumentVersion,
    Project,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
)
from app.services import DocumentService
from app.services.project_creation_service import ProjectCreationService


def concept_output() -> ConceptGenerationOutput:
    return ConceptGenerationOutput(
        options=[
            ConceptOption(
                id="glass-archive",
                title="The Glass Archive",
                logline="A memory thief threatens a city.",
                premise="An apprentice archivist stops stolen memories rewriting her home.",
                genres=["fantasy"],
            )
        ]
    )


async def create_started_project(async_session: AsyncSession, workspace_root: Path) -> tuple[Project, WorkflowRun]:
    project = Project(
        slug=f"concept-persistence-{workspace_root.name}",
        title="Concept",
        workspace_root=str(workspace_root),
    )
    async_session.add(project)
    await async_session.commit()
    started = await ProjectCreationService(async_session).start(project.id)
    run = await async_session.get(WorkflowRun, started.workflow_run_id)
    assert run is not None
    return project, run


async def event_count(async_session: AsyncSession) -> int:
    return await async_session.scalar(select(func.count()).select_from(WorkflowEvent)) or 0


async def assert_no_new_concept_artifacts(
    async_session: AsyncSession, workspace_root: Path, events_before: int
) -> None:
    assert (await async_session.scalars(select(Document))).all() == []
    assert (await async_session.scalars(select(DocumentVersion))).all() == []
    assert await event_count(async_session) == events_before
    assert not (workspace_root / "pitch").exists()


@pytest.mark.integration
@pytest.mark.anyio
async def test_validated_concepts_persist_only_as_document_service_version_chain(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, workflow_run = await create_started_project(async_session, tmp_path)
    output = concept_output()

    document = await persist_concept_generation_output(
        document_service=DocumentService(async_session),
        project_id=project.id,
        workflow_run_id=workflow_run.id,
        output=output,
    )
    version = document.current_version
    assert version is not None
    assert document.path == "pitch/concept_options.md"
    assert document.type == DocumentType.PITCH.value
    assert version.source == DocumentSource.CONCEPT_AGENT.value
    assert version.agent_role == "concept_agent"
    assert version.workflow_run_id == workflow_run.id
    assert (tmp_path / document.path).read_text() == (
        "# Concept Options\n\n## Option `glass-archive`: The Glass Archive\n\nA memory thief threatens a city.\n\n"
        "An apprentice archivist stops stolen memories rewriting her home.\n\nGenres: fantasy\n"
    )
    assert len((await async_session.scalars(select(DocumentVersion))).all()) == 1

    with pytest.raises(ConflictError):
        await persist_concept_generation_output(
            document_service=DocumentService(async_session),
            project_id=project.id,
            workflow_run_id=workflow_run.id,
            output=output,
        )
    assert len((await async_session.scalars(select(Document))).all()) == 1


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize("case", ["missing", "other_project", "wrong_type"])
async def test_concept_persistence_requires_a_matching_project_creation_workflow_run(
    async_session: AsyncSession, tmp_path: Path, case: str
) -> None:
    project, workflow_run = await create_started_project(async_session, tmp_path)
    workflow_run_id = workflow_run.id
    if case == "missing":
        workflow_run_id = uuid4()
    elif case == "other_project":
        other, other_run = await create_started_project(async_session, tmp_path / "other")
        assert other.id != project.id
        workflow_run_id = other_run.id
    elif case == "wrong_type":
        await async_session.execute(
            WorkflowRun.__table__.update()
            .where(WorkflowRun.id == workflow_run.id)
            .values(workflow_type="chapter_production")
        )
        await async_session.commit()
    events_before = await event_count(async_session)

    with pytest.raises(AppError) as error:
        await persist_concept_generation_output(
            document_service=DocumentService(async_session),
            project_id=project.id,
            workflow_run_id=workflow_run_id,
            output=concept_output(),
        )

    assert error.value.details is None
    assert str(project.id) not in str(error.value)
    await assert_no_new_concept_artifacts(async_session, tmp_path, events_before)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        "stale_pointer",
        "terminal",
        "waiting",
        "corrupt_checkpoint",
        "corrupt_projection",
        "pending_action",
    ],
)
async def test_concept_persistence_rejects_noneligible_project_creation_state(
    async_session: AsyncSession, tmp_path: Path, case: str
) -> None:
    project, workflow_run = await create_started_project(async_session, tmp_path)
    service = ProjectCreationService(async_session)
    if case == "stale_pointer":
        await async_session.execute(
            Project.__table__.update().where(Project.id == project.id).values(current_workflow_id=uuid4())
        )
        await async_session.commit()
    elif case == "terminal":
        waiting = await service.request_concept_review(workflow_run.id)
        await service.resume_concept_review(workflow_run.id, waiting.action_request_id, "rejected")
    elif case == "waiting":
        await service.request_concept_review(workflow_run.id)
    elif case == "corrupt_projection":
        await async_session.execute(
            WorkflowRun.__table__.update().where(WorkflowRun.id == workflow_run.id).values(next_node="bad")
        )
        await async_session.commit()
    elif case == "corrupt_checkpoint":
        await async_session.execute(
            WorkflowCheckpoint.__table__.update()
            .where(WorkflowCheckpoint.workflow_run_id == workflow_run.id)
            .values(state_json={"corrupt": "checkpoint"})
        )
        await async_session.commit()
    else:
        async_session.add(
            ActionRequest(
                workflow_run_id=workflow_run.id,
                project_id=project.id,
                request_type="project_creation_concept_review",
                status=ActionRequestStatus.PENDING.value,
                prompt="",
                options=[],
                default_option=None,
            )
        )
        await async_session.commit()
    events_before = await event_count(async_session)

    with pytest.raises(AppError) as error:
        await persist_concept_generation_output(
            document_service=DocumentService(async_session),
            project_id=project.id,
            workflow_run_id=workflow_run.id,
            output=concept_output(),
        )

    assert error.value.details is None
    assert str(project.id) not in str(error.value)
    await assert_no_new_concept_artifacts(async_session, tmp_path, events_before)


@pytest.mark.integration
@pytest.mark.anyio
async def test_invalid_concept_output_creates_no_document_or_version(
    async_session: AsyncSession, tmp_path: Path
) -> None:
    project, workflow_run = await create_started_project(async_session, tmp_path)
    invalid = ConceptGenerationOutput.model_construct(options=[])
    events_before = await event_count(async_session)

    with pytest.raises(AgentOutputInvalidError):
        await persist_concept_generation_output(
            document_service=DocumentService(async_session),
            project_id=project.id,
            workflow_run_id=workflow_run.id,
            output=invalid,
        )

    await assert_no_new_concept_artifacts(async_session, tmp_path, events_before)
