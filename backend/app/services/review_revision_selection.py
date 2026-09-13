"""Exact finding selections shared by HTTP, provider input and recovery evidence."""

import json
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.agents.chapter_writer_contracts import _StrictChapterModel, _canonical_uuid, SelectedReviewFinding
from app.agents.chapter_review_contracts import ChapterReviewFinding
from app.services.chapter_production_v2_contracts import ChapterProductionV2ValidationError
from app.workspace.hashing import sha256_content


class ReviewFindingSelection(_StrictChapterModel):
    report_id: UUID
    sequence: int = Field(ge=1, le=128)


class ReviewRevisionSelection(_StrictChapterModel):
    request_id: UUID
    document_id: UUID
    version_id: UUID
    action_request_id: UUID | None
    report_ids: tuple[UUID, ...] = Field(min_length=1, max_length=3)
    selected_findings: tuple[ReviewFindingSelection, ...] = Field(min_length=1, max_length=384)

    @field_validator("request_id", "action_request_id", mode="before")
    @classmethod
    def valid_id(cls, value):
        return _canonical_uuid(value) if value is not None else None

    @field_validator("report_ids", mode="before")
    @classmethod
    def valid_reports(cls, value):
        if type(value) not in (list, tuple):
            raise ValueError("Invalid reports")
        return tuple(_canonical_uuid(item) for item in value)

    @model_validator(mode="after")
    def valid_selection(self):
        ids = self.report_ids
        keys = [(item.report_id, item.sequence) for item in self.selected_findings]
        if (not self.request_id.int or (self.action_request_id is not None and not self.action_request_id.int)
                or any(not item.int for item in ids) or len(ids) != len(set(ids))
                or len(keys) != len(set(keys)) or any(item[0] not in ids for item in keys)):
            raise ValueError("Invalid review selection")
        return self


class ReviewRevisionIntent(_StrictChapterModel):
    selection: ReviewRevisionSelection
    actor_user_id: UUID
    checkpoint_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_segment_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)
    result_version_id: UUID | None = None

    @field_validator("actor_user_id", "checkpoint_id", "result_version_id", mode="before")
    @classmethod
    def valid_id(cls, value):
        return _canonical_uuid(value) if value is not None else None


def revision_intent(run):
    value = getattr(run, "metadata_", {}).get("review_revision_intent")
    if value is None:
        return None
    try:
        return ReviewRevisionIntent.model_validate_json(json.dumps(value))
    except ValueError:
        raise ChapterProductionV2ValidationError() from None


def selected_findings(reports, selection):
    """Resolve persisted findings; no client text or invented segment identity is accepted."""
    known = {}
    required = set()
    for report in reports:
        for item in (*report.blocking_issues, *report.warnings, *report.notes):
            finding = ChapterReviewFinding.model_validate({key: value for key, value in item.items()
                if key not in {"segmenter_version", "segment_map_hash"}})
            key = (report.id, finding.sequence)
            known[key] = SelectedReviewFinding(report_id=report.id, finding=finding)
            if finding.required:
                required.add(key)
    keys = [(item.report_id, item.sequence) for item in selection.selected_findings]
    if (tuple(report.id for report in reports) != selection.report_ids
            or any(key not in known for key in keys) or not required <= set(keys)):
        raise ChapterProductionV2ValidationError()
    order = {report.id: index for index, report in enumerate(reports)}
    if keys != sorted(keys, key=lambda key: (order[key[0]], key[1])):
        raise ChapterProductionV2ValidationError()
    return tuple(known[key] for key in keys)


def revision_input_hash(service, run, reports):
    report_hash = service._review_report_input_hash(reports)
    intent = revision_intent(run)
    if intent is None or intent.selection.report_ids != tuple(report.id for report in reports):
        return report_hash
    if intent.report_hash != report_hash:
        raise ChapterProductionV2ValidationError()
    return sha256_content(report_hash + intent.selection.model_dump_json())
