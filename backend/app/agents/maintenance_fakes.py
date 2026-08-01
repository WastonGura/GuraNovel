"""Deterministic, credential-free providers for maintenance workflow tests."""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel

from app.agents.maintenance_contracts import (
    ApplyChangeRequest,
    ConsistencyReviewOutcome,
    MaintenanceImpactRequest,
    PostChangeRequest,
    RevisionOperationKind,
    RevisionPlanRequest,
)
from app.agents.profiles import AgentProfile


def _stable_id(kind: str, *parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, ":".join(("guranovel", kind, *(str(part) for part in parts))))


def canonical_json_bytes(result: BaseModel) -> bytes:
    """Encode a validated result identically across calls and processes."""

    return json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class DeterministicMaintenanceProvider:
    """Generate fixed-shape results using only typed IDs and an allowlisted profile mode."""

    def __init__(
        self,
        consistency_outcome: ConsistencyReviewOutcome = ConsistencyReviewOutcome.CLEAN,
    ) -> None:
        if not isinstance(consistency_outcome, ConsistencyReviewOutcome):
            raise ValueError("consistency outcome is not typed")
        self._consistency_outcome = consistency_outcome

    async def analyze_maintenance_impact(
        self, request: MaintenanceImpactRequest, profile: AgentProfile
    ) -> object:
        item_type = "outline" if profile.name == "chief_editor" else "world"
        affected_items = []
        rewrites = []
        for document in sorted(request.document_refs, key=lambda item: str(item.document_id)):
            affected_id = _stable_id(
                "affected",
                profile.name,
                request.change_request_id,
                document.document_id,
                document.current_version_id,
            )
            affected_items.append(
                {
                    "stable_reference": f"{item_type}/{affected_id}",
                    "item_type": item_type,
                    "impact_level": "medium",
                    "document": document.model_dump(mode="json"),
                    "reason": "The referenced document participates in the requested change.",
                }
            )
            rewrites.append(
                {
                    "requirement_id": str(_stable_id("rewrite", profile.name, affected_id)),
                    "affected_item_reference": f"{item_type}/{affected_id}",
                    "document": document.model_dump(mode="json"),
                    "instruction": "Prepare a reviewed replacement version for this document.",
                }
            )
        if not affected_items:
            affected_items.append(
                {
                    "stable_reference": (
                        f"{item_type}/"
                        f"{_stable_id('affected', profile.name, request.change_request_id)}"
                    ),
                    "item_type": item_type,
                    "impact_level": "medium",
                    "document": None,
                    "reason": "The requested change affects a canonical non-document item.",
                }
            )
        output: dict[str, object] = {
            "affected_items": affected_items,
            "impact_summary": "The supplied canonical references require a reviewed revision.",
            "required_rewrites": rewrites,
            "safe_to_change": True,
            "warnings": [],
        }
        if profile.name == "chief_editor":
            output.update(
                {
                    "reader_expectation_impact": "medium",
                    "commercial_impact": "medium",
                }
            )
        return output

    async def plan_revision(self, request: RevisionPlanRequest, profile: AgentProfile) -> object:
        affected_by_document: dict[tuple[str, str], list[str]] = {}
        unbound_affected_ids: list[str] = []
        for item in request.affected_items:
            if item.document is None:
                unbound_affected_ids.append(str(item.affected_item_id))
                continue
            key = (str(item.document.document_id), str(item.document.current_version_id))
            affected_by_document.setdefault(key, []).append(str(item.affected_item_id))

        operations = []
        sorted_documents = sorted(request.document_refs, key=lambda item: str(item.document_id))
        for index, document in enumerate(sorted_documents):
            key = (str(document.document_id), str(document.current_version_id))
            affected_ids = sorted(affected_by_document.get(key, []))
            if index == 0:
                affected_ids = sorted((*affected_ids, *unbound_affected_ids))
            if not affected_ids:
                continue
            operations.append(
                {
                    "operation_id": str(
                        _stable_id(
                            "operation",
                            profile.name,
                            document.document_id,
                            document.current_version_id,
                        )
                    ),
                    "sequence": len(operations) + 1,
                    "operation": "revise",
                    "target": document.model_dump(mode="json"),
                    "affected_item_ids": affected_ids,
                    "instruction": "Prepare a new version and retain the current version for rollback.",
                }
            )
        return {
            "plan_id": str(
                _stable_id(
                    "plan", profile.name, request.workflow_run_id, request.change_request_id
                )
            ),
            "summary": "Apply the proposed version changes in canonical sequence.",
            "operations": operations,
            "safety": {
                "requires_user_confirmation": True,
                "preserve_existing_versions": True,
                "direct_write_authority": False,
            },
            "warnings": [],
        }

    async def propose_changes(
        self, request: ApplyChangeRequest, profile: AgentProfile
    ) -> object:
        edits = []
        for operation in request.operations:
            if operation.operation is RevisionOperationKind.RETAIN:
                continue
            edits.append(
                {
                    "proposed_edit_id": str(
                        _stable_id(
                            "proposed-edit",
                            request.approval_id,
                            request.revision_plan_document_id,
                            request.revision_plan_version_id,
                            operation.operation_id,
                        )
                    ),
                    "sequence": len(edits) + 1,
                    "project_id": str(request.project_id),
                    "workflow_run_id": str(request.workflow_run_id),
                    "change_request_id": str(request.change_request_id),
                    "approval_id": str(request.approval_id),
                    "revision_plan_id": str(request.revision_plan_id),
                    "revision_plan_document_id": str(request.revision_plan_document_id),
                    "revision_plan_version_id": str(request.revision_plan_version_id),
                    "revision_operation_id": str(operation.operation_id),
                    "document_id": str(operation.target.document_id),
                    "expected_current_version_id": str(
                        operation.target.current_version_id
                    ),
                    "operation": "replace_content",
                    "content": (
                        "# Proposed maintenance revision\n\n"
                        "This deterministic proposal is linked to document "
                        f"{operation.target.document_id} and approved operation "
                        f"{operation.operation_id}.\n"
                    ),
                    "rationale": "Generate a replacement body for the approved revision operation.",
                }
            )
        return {
            "change_set_id": str(
                _stable_id(
                    "change-set",
                    profile.name,
                    request.project_id,
                    request.change_request_id,
                    request.approval_id,
                    request.revision_plan_document_id,
                    request.revision_plan_version_id,
                )
            ),
            "project_id": str(request.project_id),
            "workflow_run_id": str(request.workflow_run_id),
            "change_request_id": str(request.change_request_id),
            "approval_id": str(request.approval_id),
            "revision_plan_id": str(request.revision_plan_id),
            "revision_plan_document_id": str(request.revision_plan_document_id),
            "revision_plan_version_id": str(request.revision_plan_version_id),
            "proposed_edits": edits,
        }

    async def review_consistency(
        self, request: PostChangeRequest, profile: AgentProfile
    ) -> object:
        outcome = self._consistency_outcome
        findings: list[dict[str, object]] = []
        if outcome is not ConsistencyReviewOutcome.CLEAN:
            blocking = outcome is ConsistencyReviewOutcome.BLOCKING
            affected_documents = [
                {
                    "document_id": str(item.document_id),
                    "current_version_id": str(item.current_version_id),
                }
                for item in sorted(
                    request.applied_changes, key=lambda item: str(item.document_id)
                )
            ]
            findings.append(
                {
                    "finding_id": str(
                        _stable_id(
                            "consistency-finding",
                            outcome.value,
                            request.change_set_id,
                        )
                    ),
                    "sequence": 1,
                    "code": f"post_change_{outcome.value}",
                    "severity": outcome.value,
                    "affected_documents": affected_documents,
                    "blocking": blocking,
                    "suggested_corrective_action": (
                        "Prepare a corrective revision plan before project completion."
                        if blocking
                        else "Ask the user whether to accept this consistency warning."
                    ),
                }
            )
        applied_version_ids = sorted(
            str(item.current_version_id) for item in request.applied_changes
        )
        return {
            "review_id": str(
                _stable_id(
                    "consistency-review",
                    profile.name,
                    outcome.value,
                    request.change_set_id,
                    *applied_version_ids,
                )
            ),
            "project_id": str(request.project_id),
            "workflow_run_id": str(request.workflow_run_id),
            "change_request_id": str(request.change_request_id),
            "approval_id": str(request.approval_id),
            "revision_plan_id": str(request.revision_plan_id),
            "revision_plan_document_id": str(request.revision_plan_document_id),
            "revision_plan_version_id": str(request.revision_plan_version_id),
            "change_set_id": str(request.change_set_id),
            "outcome": outcome.value,
            "findings": findings,
        }


class DeterministicApplyChangeProvider(DeterministicMaintenanceProvider):
    pass


class DeterministicPostChangeProvider(DeterministicMaintenanceProvider):
    def __init__(self, outcome: ConsistencyReviewOutcome) -> None:
        super().__init__(consistency_outcome=outcome)


__all__ = [
    "DeterministicApplyChangeProvider",
    "DeterministicMaintenanceProvider",
    "DeterministicPostChangeProvider",
    "canonical_json_bytes",
]
