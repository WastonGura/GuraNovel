"""Durable outline batches with transaction-free provider calls and author selection."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.outline_options import OutlineOptions, generate_outline_options
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError
from app.models import Chapter, Document, DocumentSource, DocumentType, Project, WorkflowRun
from app.services.document_service import DocumentService
from app.services.setting_context_resolver import SettingContextResolver
from app.services.studio_ideas import idea_value
from app.services.studio_scope import studio_chapter

WORKFLOW_TYPE = "chapter_outline"


async def outline_chapter(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> Chapter:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                          {"key": f"chapter-production-v2:{chapter_id}"})
    return await studio_chapter(session, project_id, chapter_id)


async def ensure_outline_writable(session: AsyncSession, chapter: Chapter) -> None:
    production = await session.scalar(select(WorkflowRun.id).where(
        WorkflowRun.chapter_id == chapter.id, WorkflowRun.workflow_type == "chapter_production",
        WorkflowRun.status.not_in(("COMPLETED", "CANCELLED", "FAILED", "completed", "cancelled", "failed", "rejected")),
    ).limit(1))
    if chapter.current_draft_document_id or chapter.final_document_id or production:
        raise ConflictError("Prose production has started. The outline is locked.")


async def scoped_run(session: AsyncSession, project_id: UUID, chapter_id: UUID, run_id: UUID) -> WorkflowRun:
    run = await session.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id,
        WorkflowRun.project_id == project_id, WorkflowRun.chapter_id == chapter_id,
        WorkflowRun.workflow_type == WORKFLOW_TYPE).execution_options(populate_existing=True))
    if run is None:
        raise NotFoundError("Outline generation not found.")
    return run


def batch_response(run: WorkflowRun) -> dict:
    value = run.metadata_
    status = run.status
    if status == "running" and datetime.now(UTC) > datetime.fromisoformat(value["deadline"]):
        status = "uncertain"
    return dict(id=run.id, project_id=run.project_id, chapter_id=run.chapter_id, status=status,
        idea_text=value["idea_text"], idea_revision=value["idea_revision"], context_hash=value["context_hash"],
        candidates=value.get("candidates", []), started_at=run.started_at,
        failure_code=value.get("failure_code"), provenance=value.get("provenance", {}))


async def list_batches(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> list[dict]:
    await studio_chapter(session, project_id, chapter_id)
    runs = await session.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project_id,
        WorkflowRun.chapter_id == chapter_id, WorkflowRun.workflow_type == WORKFLOW_TYPE)
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc()).limit(20))
    return [batch_response(run) for run in runs]


async def create_batch(session: AsyncSession, project_id: UUID, chapter_id: UUID, *,
                       request_id: UUID, expected_idea_revision: int) -> dict:
    chapter = await outline_chapter(session, project_id, chapter_id)
    existing = await session.get(WorkflowRun, request_id)
    if existing is not None:
        run = await scoped_run(session, project_id, chapter_id, request_id)
        if run.metadata_["idea_revision"] != expected_idea_revision:
            raise ConflictError("Request identity does not match the original idea revision.")
        return batch_response(run)
    await ensure_outline_writable(session, chapter)
    idea = idea_value(chapter)
    if not idea["text"].strip() or idea["revision"] != expected_idea_revision:
        raise ConflictError("Save and review the current chapter idea before generating.")
    active = await session.scalar(select(WorkflowRun.id).where(WorkflowRun.chapter_id == chapter_id,
        WorkflowRun.workflow_type == WORKFLOW_TYPE, WorkflowRun.status.in_(("running", "uncertain"))).limit(1))
    if active:
        raise ConflictError("An outline request is still running or needs reconciliation.")
    project = await session.get(Project, project_id)
    resolver = SettingContextResolver(session)
    bundle = await resolver.resolve_for_collection(project.setting_collection_id) if project.setting_collection_id else await resolver.resolve_for_project(project_id)
    previous = await session.scalar(select(Chapter).where(Chapter.project_id == project_id,
        Chapter.chapter_number < chapter.chapter_number).order_by(Chapter.chapter_number.desc()).limit(1))
    previous_context = None
    if previous and (previous.final_document_id or previous.current_draft_document_id):
        previous_id = previous.final_document_id or previous.current_draft_document_id
        content = await DocumentService(session).read_current_content(previous_id)
        previous_context = {"chapter_id": str(previous.id), "document_id": str(previous_id),
                            "version_id": str(content.version_id), "content": content.content}
    snapshot = {"project_id": str(project_id), "chapter_id": str(chapter_id), "novel_title": project.title,
                "chapter_title": chapter.title, "chapter_number": chapter.chapter_number,
                "idea_text": idea["text"], "idea_revision": idea["revision"],
                "setting_bundle": bundle.to_dict(), "previous_chapter": previous_context}
    context_hash = sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    run = WorkflowRun(id=request_id, project_id=project_id, chapter_id=chapter_id,
        workflow_type=WORKFLOW_TYPE, status="running", current_node="outline_options", awaiting_user=False,
        metadata_={"idea_text": idea["text"], "idea_revision": idea["revision"], "snapshot": snapshot,
                   "context_hash": context_hash,
                   "deadline": (datetime.now(UTC) + timedelta(seconds=(settings.openai_compatible_timeout_seconds or 30) + 30)).isoformat()})
    session.add(run)
    await session.commit()  # Persist the request and release all locks before the model call.
    try:
        output, provenance = await generate_outline_options(snapshot)
        output = OutlineOptions.model_validate(output)
    except Exception as error:
        await session.rollback()
        await outline_chapter(session, project_id, chapter_id)
        run = await scoped_run(session, project_id, chapter_id, request_id)
        if run.status == "running":
            uncertain = getattr(error, "code", "") in {"provider_timeout", "provider_unavailable"}
            run.status = "uncertain" if uncertain else "failed"
            run.metadata_ = {**run.metadata_, "failure_code": "provider_result_uncertain" if uncertain else "outline_generation_failed"}
            await session.commit()
        return batch_response(run)
    chapter = await outline_chapter(session, project_id, chapter_id)
    run = await scoped_run(session, project_id, chapter_id, request_id)
    if run.status != "running":
        return batch_response(run)
    await ensure_outline_writable(session, chapter)
    candidates = [{"id": str(uuid4()), **item.model_dump()} for item in output.candidates]
    content = json.dumps({"candidates": candidates}, ensure_ascii=False)
    documents = DocumentService(session)
    document, current_write, version_write = await documents.stage_create_document(
        project_id=project_id, chapter_id=chapter_id, document_type=DocumentType.CHAPTER_OUTLINE_OPTIONS,
        title="Chapter outline alternatives", path=f"chapters/{chapter.chapter_number:04d}/outline-options/{request_id}.json",
        content=content, source=DocumentSource.OUTLINE_AGENT, workflow_run_id=request_id,
        metadata={"studio_outline_batch": True, "context_hash": context_hash})
    run.metadata_ = {**run.metadata_, "candidates": candidates, "provenance": provenance,
                     "document_id": str(document.id), "version_id": str(document.current_version_id)}
    run.status = "completed"; run.completed_at = datetime.now(UTC)
    await documents.commit_staged_document(document, (current_write, version_write))
    return batch_response(run)


async def cancel_uncertain_batch(session: AsyncSession, project_id: UUID, chapter_id: UUID, run_id: UUID) -> dict:
    await outline_chapter(session, project_id, chapter_id)
    run = await scoped_run(session, project_id, chapter_id, run_id)
    if run.status == "cancelled":
        return batch_response(run)
    if batch_response(run)["status"] != "uncertain":
        raise ConflictError("Only an uncertain generation can be abandoned explicitly.")
    run.status = "cancelled"
    run.metadata_ = {**run.metadata_, "failure_code": "author_abandoned_uncertain_request"}
    await session.commit()
    return batch_response(run)


async def select_candidate(session: AsyncSession, project_id: UUID, chapter_id: UUID, run_id: UUID, *,
                           request_id: UUID, candidate_id: UUID, expected_current_version_id: UUID | None) -> dict:
    chapter = await outline_chapter(session, project_id, chapter_id)
    await ensure_outline_writable(session, chapter)
    run = await scoped_run(session, project_id, chapter_id, run_id)
    if run.status != "completed":
        raise ConflictError("Outline generation is not complete.")
    candidate = next((item for item in run.metadata_["candidates"] if item["id"] == str(candidate_id)), None)
    if candidate is None:
        raise NotFoundError("Outline candidate not found.")
    receipt_key = "outline_selection"
    receipt = (chapter.metadata_ or {}).get(receipt_key, {})
    current = await session.get(Document, chapter.current_outline_document_id) if chapter.current_outline_document_id else None
    if receipt.get("request_id") == str(request_id):
        if receipt.get("candidate_id") != str(candidate_id) or receipt.get("run_id") != str(run_id) or receipt.get("expected_version") != (str(expected_current_version_id) if expected_current_version_id else None):
            raise ConflictError("Selection request identity was reused.")
        if current is None or str(current.current_version_id) != receipt.get("version_id"):
            raise ConflictError("The selected outline has changed since this request.")
        return dict(chapter_id=chapter_id, document_id=current.id, version_id=current.current_version_id)
    if (current.current_version_id if current else None) != expected_current_version_id:
        raise ConflictError("The selected outline changed. Keep your edits and reload.")
    documents = DocumentService(session)
    document, current_write, version_write = await documents.stage_create_document(
        project_id=project_id, chapter_id=chapter_id, document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title=candidate["title"], path=f"chapters/{chapter.chapter_number:04d}/selected-outlines/{request_id}.md",
        content=candidate["content"], source=DocumentSource.USER,
        metadata={"studio_selected_outline": True, "generation_id": str(run_id), "candidate_id": str(candidate_id)})
    chapter.current_outline_document_id = document.id
    chapter.approved_outline_version_id = None; chapter.status = "OUTLINE_DISCUSSION"
    chapter.metadata_ = {**(chapter.metadata_ or {}), receipt_key: {"request_id": str(request_id),
        "run_id": str(run_id), "candidate_id": str(candidate_id), "expected_version": str(expected_current_version_id) if expected_current_version_id else None,
        "version_id": str(document.current_version_id)}}
    await documents.commit_staged_document(document, (current_write, version_write))
    return dict(chapter_id=chapter_id, document_id=document.id, version_id=document.current_version_id)


async def save_selected_outline(session: AsyncSession, project_id: UUID, chapter_id: UUID, *,
                                content: str, expected_current_version_id: UUID):
    chapter = await outline_chapter(session, project_id, chapter_id)
    await ensure_outline_writable(session, chapter)
    document = await session.get(Document, chapter.current_outline_document_id) if chapter.current_outline_document_id else None
    if document is None or document.project_id != project_id or document.chapter_id != chapter_id or document.type != DocumentType.CHAPTER_SELECTED_OUTLINE.value:
        raise NotFoundError("Selected outline not found.")
    documents = DocumentService(session)
    version, current_write, version_write = await documents.stage_write_document(document_id=document.id,
        content=content, expected_current_version_id=expected_current_version_id, source=DocumentSource.USER,
        studio_outline_edit=True)
    chapter.approved_outline_version_id = None; chapter.status = "OUTLINE_DISCUSSION"
    await documents.commit_staged_document(document, (current_write, version_write))
    return version
