"""Persist an explicit selection before resolving its gate or invoking Revision."""

from uuid import UUID

from app.core.errors import ConflictError
from app.services.chapter_production_recovery_evidence import (
    _locked_review_revision_reports, locked_review_document,
)
from app.services.chapter_review_validation import validated_persisted_review_report, validated_resolved_review_action
from app.services.review_revision_saga import _report_slots, _review_stage
from app.services.review_revision_selection import (
    ReviewRevisionIntent, ReviewRevisionSelection, revision_intent, selected_findings,
)
from app.services.chapter_production_v2_contracts import ChapterProductionV2Updated
from app.workflows.chapter_production import ChapterActionKind, ChapterProductionStatus


async def _record_selection(service, project_id, chapter_id, run_id, actor_id, selection):
    await service._require_project_owner(project_id, actor_id)
    chapter = await service._chapter(project_id, chapter_id, lock=True)
    run = await service._run(project_id, chapter_id, run_id, lock=True)
    state, checkpoint = await service._locked_state(run)
    previous = revision_intent(run)
    if previous is not None and previous.selection.request_id == selection.request_id:
        if previous.selection != selection or previous.actor_user_id != actor_id:
            raise ConflictError("The revision request inputs changed.")
        if (previous.result_version_id is None and state.status is ChapterProductionStatus.REVIEW_REVISION
                and service._run_metadata(run)["provider_attempt"] is not None):
            await service._commit()
            await service.reconcile_indeterminate(project_id, chapter_id, run_id, actor_user_id=actor_id)
            run = await service._run(project_id, chapter_id, run_id, lock=True)
            previous = revision_intent(run)
        await service._commit()
        return previous
    if previous is not None and previous.result_version_id is None:
        raise ConflictError("An earlier revision must be recovered first.")
    if (state.document_id != str(selection.document_id)
            or state.document_version_id != str(selection.version_id)
            or state.action_request_id != (str(selection.action_request_id) if selection.action_request_id else None)
            or (state.awaiting_user and state.action_kind not in {
                ChapterActionKind.REVIEW_WARNING, ChapterActionKind.REVIEW_REVISION})
            or (not state.awaiting_user and state.status is not ChapterProductionStatus.REVISION_READY)):
        raise ConflictError("The displayed review or draft has changed.")
    document, version = await locked_review_document(service, project_id=project_id,
        chapter_id=chapter_id, state=state, chapter=chapter)
    slots = _report_slots(state)
    reports = await _locked_review_revision_reports(service, slots, project_id, chapter_id,
        run, document.id, version.id)
    for report, (_, mode, _) in zip(reports, slots, strict=True):
        await validated_persisted_review_report(service, row=report, run=run,
            document=document, version=version, stage=_review_stage(mode))
    findings = selected_findings(reports, selection)
    segment_map = await service.documents.derive_chapter_segment_map(project_id=project_id,
        chapter_id=chapter_id, document_id=document.id, version_id=version.id)
    evidence = {sid for item in findings for sid in item.finding.evidence_segment_ids}
    targets = tuple(item.segment_id for item in segment_map.segments if item.segment_id in evidence)
    intent = ReviewRevisionIntent(selection=selection, actor_user_id=actor_id,
        checkpoint_id=checkpoint.id, content_hash=version.content_hash,
        report_hash=service._review_report_input_hash(reports), target_segment_ids=targets)
    run.metadata_ = {**run.metadata_, "review_revision_intent": intent.model_dump(mode="json")}
    # Validate the actual provider envelope before consuming the user's action.
    from types import SimpleNamespace
    service._review_revision_request(context=SimpleNamespace(run=run, reports=reports,
        document=document, version=version, segment_map=segment_map), project_id=project_id,
        chapter_id=chapter_id, target_segment_ids=targets)
    if selection.action_request_id is not None:
        await service.resolve_review_action(project_id, chapter_id, run_id,
            selection.action_request_id, actor_user_id=actor_id, decision="request_revision")
    else:
        service._append_state(run, checkpoint, state.request_selected_review_revision())
        await service._commit()
    return intent


async def request_review_revision(service, project_id, chapter_id, run_id, actor_id, selection):
    service._validated_ids(project_id, chapter_id, run_id, actor_id)
    if type(selection) is not ReviewRevisionSelection:
        raise ConflictError("Invalid revision selection.")
    try:
        intent = await _record_selection(service, project_id, chapter_id, run_id, actor_id, selection)
    except Exception:
        await service._rollback()
        raise
    if intent.result_version_id is not None:
        return ChapterProductionV2Updated(workflow_run_id=run_id,
            draft_document_id=selection.document_id, draft_version_id=intent.result_version_id,
            action_request_id=None)
    return await service.execute_review_revision(project_id, chapter_id, run_id,
        actor_user_id=actor_id, report_ids=selection.report_ids,
        target_segment_ids=intent.target_segment_ids)


async def validate_voluntary_revision(service, run, document, version, report):
    intent = revision_intent(run)
    if intent is None or intent.selection.action_request_id is not None:
        return False
    if (intent.selection.document_id != document.id or intent.selection.version_id != version.id
            or intent.content_hash != version.content_hash
            or intent.selection.report_ids[-1] != report.id):
        raise ConflictError("The revision evidence changed.")
    pairs = await service._readiness.validated_pairs(run)
    matches = [pair for pair in pairs if pair.checkpoint.id == intent.checkpoint_id]
    if len(matches) != 1 or matches[0].state.document_version_id != str(version.id):
        raise ConflictError("The revision has no matching ready checkpoint.")
    await service._require_project_owner(run.project_id, UUID(str(intent.actor_user_id)))
    return True


async def validate_revision_authority(service, *, run, document, version, report, stage):
    if not await validate_voluntary_revision(service, run, document, version, report):
        await validated_resolved_review_action(service, run=run, document=document,
            version=version, report=report, stage=stage)
