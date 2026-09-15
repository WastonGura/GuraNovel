"""Unit tests verifying outline content is propagated into draft and revision requests."""

from uuid import uuid4
from app.agents.chapter_writer_contracts import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    InitialDraftRequest,
    SourceDraftReference,
    SourceDraftSegment,
    UserFeedbackReference,
    UserFeedbackRevisionRequest,
)
from app.agents.context_assembly import assemble_writer_context
from app.agents.profiles import ProfileRegistry


def test_initial_draft_request_outline_content_sufficient():
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    seg_id = uuid4()

    outline_text = "Chapter Outline: A cold rainy evening in the city."
    req = InitialDraftRequest(
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_run_id=run_id,
        approved_outline=ApprovedOutlineReference(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=uuid4(),
            version_id=uuid4(),
            content=outline_text,
        ),
        allowed_segments=(
            AllowedChapterSegment(
                segment_id=seg_id,
                index=1,
                title="Scene 1",
                brief="The rain started falling.",
            ),
        ),
    )

    registry = ProfileRegistry()
    profile = registry.load("writer_agent", "initial_draft")
    ctx = assemble_writer_context(req, profile)
    assert ctx.outline_content == outline_text
    assert len(ctx.active_segments) == 1


def test_user_feedback_revision_request_outline_content_sufficient():
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    seg_id = uuid4()
    draft_doc_id = uuid4()
    draft_ver_id = uuid4()

    outline_text = "Chapter Outline: Scene outline with pacing notes."
    req = UserFeedbackRevisionRequest(
        project_id=project_id,
        chapter_id=chapter_id,
        workflow_run_id=run_id,
        approved_outline=ApprovedOutlineReference(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=uuid4(),
            version_id=uuid4(),
            content=outline_text,
        ),
        source_draft=SourceDraftReference(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=draft_doc_id,
            version_id=draft_ver_id,
            segments=(
                SourceDraftSegment(
                    segment_id=seg_id,
                    index=1,
                    title="Scene 1",
                    content="The rain began to fall heavily on the old roof.",
                ),
            ),
        ),
        allowed_segments=(
            AllowedChapterSegment(
                segment_id=seg_id,
                index=1,
                title="Scene 1",
                brief="Scene 1 brief",
            ),
        ),
        target_segment_ids=(seg_id,),
        feedback_refs=(
            UserFeedbackReference(
                feedback_id=uuid4(),
                project_id=project_id,
                chapter_id=chapter_id,
                workflow_run_id=run_id,
                source_draft_document_id=draft_doc_id,
                source_draft_version_id=draft_ver_id,
                instruction="Enhance the atmosphere and character sensory details.",
            ),
        ),
    )

    registry = ProfileRegistry()
    profile = registry.load("revision_agent", "user_feedback_revision")
    ctx = assemble_writer_context(req, profile)
    assert ctx.outline_content == outline_text
    assert len(ctx.source_segments) == 1
