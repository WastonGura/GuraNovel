"""Deterministic, credential-free providers for maintenance workflow tests."""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel

from app.agents.maintenance_contracts import MaintenanceImpactRequest, RevisionPlanRequest
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


__all__ = ["DeterministicMaintenanceProvider", "canonical_json_bytes"]
