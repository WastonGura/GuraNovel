"""Narrow persistence boundary for already-validated concept output."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select

from app.agents.contracts import (
    ConceptGenerationOutput,
    validate_concept_generation_output,
)
from app.agents.errors import ConceptArtifactWorkflowError
from app.models import (
    ActionRequest,
    ActionRequestStatus,
    Document,
    DocumentSource,
    DocumentType,
    Project,
    WorkflowCheckpoint,
    WorkflowRun,
    WorkflowType,
)
from app.services.document_service import DocumentService
from app.workflows.project_creation import (
    ProjectCreationState,
    ProjectCreationStatus,
    ProjectCreationValidationError,
)


_CONCEPT_REVIEW_ACTION = "project_creation_concept_review"
_CONCEPT_OPTION = re.compile(
    r"^## Option `(?P<id>[a-z][a-z0-9-]{0,63})`: (?P<title>[^\n]+)\n\n"
    r"(?P<logline>[^\n]+)\n\n(?P<premise>[^\n]+)\n\nGenres: (?P<genres>[^\n]+)$",
    re.MULTILINE,
)


def render_concept_options_markdown(output: ConceptGenerationOutput) -> str:
    """Render deterministic Markdown from a validated value object."""
    sections = ["# Concept Options"]
    for option in output.options:
        sections.append(
            "\n\n".join(
                (
                    f"## Option `{option.id}`: {option.title}",
                    option.logline,
                    option.premise,
                    f"Genres: {', '.join(option.genres)}",
                )
            )
        )
    return "\n\n".join(sections) + "\n"


def parse_concept_options_markdown(content: str) -> ConceptGenerationOutput:
    """Return the canonical validated concept artifact, or one safe workflow error."""
    if not isinstance(content, str):
        raise ConceptArtifactWorkflowError()
    options = []
    for match in _CONCEPT_OPTION.finditer(content.rstrip()):
        options.append(
            {
                "id": match["id"],
                "title": match["title"],
                "logline": match["logline"],
                "premise": match["premise"],
                "genres": [part.strip() for part in match["genres"].split(",")],
            }
        )
    try:
        output = validate_concept_generation_output({"options": options})
    except Exception:
        raise ConceptArtifactWorkflowError() from None
    if render_concept_options_markdown(output) != content:
        raise ConceptArtifactWorkflowError()
    return output


def selected_concept_markdown(content: str, option_id: str) -> str:
    """Reconstruct one validated option from the reviewed, versioned artifact.

    The artifact is deliberately the only durable source for option prose; action
    rows retain IDs only.  The format is generated above, not accepted from users.
    """
    return selected_concept_markdown_from_output(parse_concept_options_markdown(content), option_id)


def selected_concept_markdown_from_output(
    output: ConceptGenerationOutput, option_id: str
) -> str:
    """Render a selected concept from an already validated canonical artifact."""
    option = next((item for item in output.options if item.id == option_id), None)
    if option is None:
        raise ConceptArtifactWorkflowError()
    return (
        "\n\n".join(
            (
                "# Selected Concept",
                f"## {option.title}",
                option.logline,
                option.premise,
                f"Genres: {', '.join(option.genres)}",
            )
        )
        + "\n"
    )


async def persist_concept_generation_output(
    *,
    document_service: DocumentService,
    project_id: UUID,
    workflow_run_id: UUID | None,
    output: ConceptGenerationOutput,
) -> Document:
    """Create the one concept artifact through DocumentService's version chain.

    Existing ``pitch/concept_options.md`` documents use DocumentService's normal
    safe conflict behavior.  This function performs validation before any write.
    """
    validated = validate_concept_generation_output(output)
    await _require_project_creation_workflow_run(
        document_service=document_service,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
    )
    return await document_service.create_document(
        project_id=project_id,
        document_type=DocumentType.PITCH,
        title="Concept options",
        path="pitch/concept_options.md",
        content=render_concept_options_markdown(validated),
        source=DocumentSource.CONCEPT_AGENT,
        agent_role="concept_agent",
        workflow_run_id=workflow_run_id,
        change_summary="Generated concept options",
    )


async def stage_concept_generation_output(
    *,
    document_service: DocumentService,
    project_id: UUID,
    workflow_run_id: UUID | None,
    output: ConceptGenerationOutput,
) -> tuple[Document, UUID, tuple[tuple[str, str], ...]]:
    """Validate and stage the #65 artifact without committing the caller transaction."""
    validated = validate_concept_generation_output(output)
    await _require_project_creation_workflow_run(
        document_service=document_service, project_id=project_id, workflow_run_id=workflow_run_id
    )
    content = render_concept_options_markdown(validated)
    document = await document_service.session.scalar(
        select(Document)
        .where(Document.project_id == project_id, Document.path == "pitch/concept_options.md")
        .with_for_update()
    )
    if document is not None and document.current_version_id is not None:
        version, *writes = await document_service.stage_write_document(
            document_id=document.id,
            content=content,
            source=DocumentSource.CONCEPT_AGENT,
            expected_current_version_id=document.current_version_id,
            agent_role="concept_agent",
            workflow_run_id=workflow_run_id,
            change_summary="Generated concept options for new creation run",
        )
        return document, version.id, tuple(writes)
    document, *writes = await document_service.stage_create_document(
        project_id=project_id,
        document_type=DocumentType.PITCH,
        title="Concept options",
        path="pitch/concept_options.md",
        content=content,
        source=DocumentSource.CONCEPT_AGENT,
        agent_role="concept_agent",
        workflow_run_id=workflow_run_id,
        change_summary="Generated concept options",
    )
    if document.current_version_id is None:
        raise ConceptArtifactWorkflowError()
    return document, document.current_version_id, tuple(writes)


async def _require_project_creation_workflow_run(
    *, document_service: DocumentService, project_id: UUID, workflow_run_id: UUID | None
) -> None:
    """Lock and validate the durable initial graph state before persistence."""
    if workflow_run_id is None:
        raise ConceptArtifactWorkflowError()
    try:
        project = await document_service.session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        workflow_run = await document_service.session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.id == workflow_run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            project is None
            or project.current_workflow_id != workflow_run_id
            or workflow_run is None
            or workflow_run.project_id != project_id
            or workflow_run.workflow_type != WorkflowType.PROJECT_CREATION.value
        ):
            raise ConceptArtifactWorkflowError()
        checkpoint = await document_service.session.scalar(
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == workflow_run.id)
            .order_by(WorkflowCheckpoint.checkpoint_index.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if checkpoint is None:
            raise ConceptArtifactWorkflowError()
        try:
            state = ProjectCreationState.from_checkpoint(checkpoint.state_json)
        except ProjectCreationValidationError:
            raise ConceptArtifactWorkflowError() from None
        pending_action_ids = list(
            await document_service.session.scalars(
                select(ActionRequest.id)
                .where(
                    ActionRequest.workflow_run_id == workflow_run.id,
                    ActionRequest.project_id == project.id,
                    ActionRequest.request_type == _CONCEPT_REVIEW_ACTION,
                    ActionRequest.status == ActionRequestStatus.PENDING.value,
                )
                .with_for_update()
            )
        )
        if (
            workflow_run.status != state.status.value
            or workflow_run.current_node != state.current_node
            or workflow_run.next_node is not None
            or workflow_run.awaiting_user != state.awaiting_user
            or state.is_terminal != (workflow_run.completed_at is not None)
        ):
            raise ConceptArtifactWorkflowError()
        if state.awaiting_user:
            if (
                len(pending_action_ids) != 1
                or str(pending_action_ids[0]) != state.action_request_id
            ):
                raise ConceptArtifactWorkflowError()
        elif pending_action_ids:
            raise ConceptArtifactWorkflowError()
        if (
            state.status is not ProjectCreationStatus.USER_IDEA
            or state.is_terminal
            or state.awaiting_user
            or pending_action_ids
        ):
            raise ConceptArtifactWorkflowError()
    except ConceptArtifactWorkflowError:
        raise
    except Exception:
        raise ConceptArtifactWorkflowError() from None
