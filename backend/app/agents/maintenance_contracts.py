"""Strict provider-neutral contracts for project-maintenance agents."""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.llm.errors import ProviderInvalidOutputError
from app.workflows.project_maintenance_types import AffectedItemType, ImpactLevel


_MACHINE_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}")
_STABLE_REFERENCE = re.compile(
    rf"(?:{'|'.join(re.escape(item.value) for item in AffectedItemType)})/"
    r"[a-z0-9][a-z0-9_-]{0,63}"
)
_WINDOWS_DRIVE_PATH = re.compile(r"(?:^|[^a-z0-9])[a-z]:", re.IGNORECASE)
_WINDOWS_DEVICE = re.compile(
    r"(?:^|[^a-z0-9])(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
_ENCODED_PATH_MATERIAL = re.compile(r"%(?:[0-9a-f]{2}|u[0-9a-f]{4})", re.IGNORECASE)
_EXTERNAL_URI_SCHEME = re.compile(
    r"(?:^|[^a-z0-9])(?:[a-z][a-z0-9+.-]*):(?=\S)",
    re.IGNORECASE,
)
_BARE_DOTTED_TOKEN = re.compile(
    r"(?:^|[^a-z0-9])(?:[a-z0-9_-][a-z0-9_-]*(?:\.[a-z0-9_-]+)*\.[a-z]{2,63})"
    r"(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
_CREDENTIAL_MATERIAL = re.compile(
    r"(?:\b(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|passwd|secret|token)\b"
    r"\s*[:=]\s*\S+|\bsk-[a-z0-9_-]{8,}\b)",
    re.IGNORECASE,
)
_PUBLIC_HEADER_MATERIAL = re.compile(
    r"\b(?:x-[a-z0-9-]+|authorization|set-cookie|content-type|referer|user-agent)\s*:",
    re.IGNORECASE,
)
_AMBIGUOUS_HEADER_LINE = re.compile(r"(?:^|[\r\n])\s*cookie\s*:", re.IGNORECASE)
_SERVER_METADATA = re.compile(
    r"(?:\bServer\s*:\s*\S+|(?:^|[\r\n])\s*server\s*:\s*\S+)"
)
_DETAILS_DIAGNOSTIC = re.compile(
    r"\b(?:provider\s+)?details\s+(?:error|exception|server)\s*:", re.IGNORECASE
)
_RAW_EXCEPTION_MATERIAL = re.compile(
    r"(?:\b(?:Error|ERROR|Exception|EXCEPTION|Traceback|TRACEBACK)\s*:"
    r"|(?:^|[\r\n])\s*(?:error|exception|traceback)\s*:)"
)
_EXCEPTION_CLASS_MATERIAL = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception)\s*:")
_CLOUD_REGION = (
    r"(?:(?:[a-z]{2}|us-gov)(?:-[a-z0-9]+)+-\d+"
    r"|(?:[a-z]{2}|africa|asia|australia|europe|northamerica|southamerica)(?:-[a-z]+)+\d+)"
)
_PROVIDER_METADATA = re.compile(
    rf"(?:\bmodel\s*[:=]?\s*\S+\s+in\s+region\s*[:=]?\s*{_CLOUD_REGION}"
    rf"|\bprovider\s*[:=]?\s*\S+\s+model\s*[:=]?\s*\S+\s+region\s*[:=]?\s*{_CLOUD_REGION}"
    rf"|\bprovider\s*[:=]\s*\S+|\bendpoint\s*[:=]\s*\S+|\bregion\s*[:=]\s*{_CLOUD_REGION}"
    r"|\bmodel\s*=\s*[a-z0-9][a-z0-9._-]*"
    r"|\b(?:provider\s+)?details\s+model\s*[:=]\s*[a-z0-9][a-z0-9._-]*"
    r"|\bregion\s*=\s*[a-z0-9][a-z0-9._-]*"
    r"|\bserving\s+model\s+\S+\s+in\s+region\s+\S+"
    r"|(?:^|[\r\n])\s*model\s*[:=]\s*(?:gpt|claude|gemini|llama|mistral|command|qwen)"
    r"[a-z0-9._-]*\s*(?:$|[\r\n]))",
    re.IGNORECASE,
)
_UUID_FIELDS = (
    "project_id",
    "workflow_run_id",
    "change_request_id",
    "document_id",
    "current_version_id",
    "warning_id",
    "requirement_id",
    "operation_id",
    "plan_id",
    "approval_id",
    "change_set_id",
    "proposed_edit_id",
    "previous_version_id",
    "review_id",
    "finding_id",
    "revision_plan_id",
    "revision_operation_id",
    "revision_plan_document_id",
    "revision_plan_version_id",
    "expected_current_version_id",
)

_MAX_PROPOSED_CONTENT_CHARACTERS = 200_000
_MAX_PROPOSED_CONTENT_BYTES = 262_144


def _canonical_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        parsed = value
    elif type(value) is str:
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise ValueError("invalid reference") from error
        if str(parsed) != value:
            raise ValueError("reference is not canonical")
    else:
        raise ValueError("invalid reference")
    if parsed.int == 0:
        raise ValueError("reference cannot be nil")
    return parsed


def _validated_stable_reference(
    value: object, *, item_type: AffectedItemType | None = None
) -> str:
    if type(value) is not str:
        raise ValueError("invalid stable reference")
    if (
        _STABLE_REFERENCE.fullmatch(value) is None
        or _CREDENTIAL_MATERIAL.search(value) is not None
        or (item_type is not None and not value.startswith(f"{item_type.value}/"))
    ):
        raise ValueError("invalid stable reference")
    return value


def _safe_planning_text(value: str, field: str) -> str:
    """Reject path-like or externally addressable strings from durable agent results."""

    if (
        not value.strip()
        or value != value.strip()
        or "\x00" in value
        or any(marker in value for marker in ("/", "\\", "~", "?", "#", ".."))
        or _WINDOWS_DRIVE_PATH.search(value) is not None
        or _WINDOWS_DEVICE.search(value) is not None
        or _ENCODED_PATH_MATERIAL.search(value) is not None
        or _EXTERNAL_URI_SCHEME.search(value) is not None
        or _BARE_DOTTED_TOKEN.search(value) is not None
        or _CREDENTIAL_MATERIAL.search(value) is not None
        or _PUBLIC_HEADER_MATERIAL.search(value) is not None
        or _AMBIGUOUS_HEADER_LINE.search(value) is not None
        or _SERVER_METADATA.search(value) is not None
        or _DETAILS_DIAGNOSTIC.search(value) is not None
        or _RAW_EXCEPTION_MATERIAL.search(value) is not None
        or _EXCEPTION_CLASS_MATERIAL.search(value) is not None
        or _PROVIDER_METADATA.search(value) is not None
    ):
        raise ValueError(f"invalid {field}")
    return value


class WarningSeverity(str, Enum):
    BLOCKING = "blocking"
    ADVISORY = "advisory"


class RevisionOperationKind(str, Enum):
    REVISE = "revise"
    RETIRE = "retire"
    RETAIN = "retain"


class ProposedEditOperation(str, Enum):
    """The sole provider-authored operation supported by application mediation."""

    REPLACE_CONTENT = "replace_content"


class ConsistencyFindingSeverity(str, Enum):
    WARNING = "warning"
    BLOCKING = "blocking"


class ConsistencyReviewOutcome(str, Enum):
    CLEAN = "clean"
    WARNING = "warning"
    BLOCKING = "blocking"


class _StrictMaintenanceModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    @field_validator(*_UUID_FIELDS, mode="before", check_fields=False)
    @classmethod
    def canonical_non_nil_uuid(cls, value: object) -> UUID:
        return _canonical_uuid(value)

    @field_validator("affected_item_id", mode="before", check_fields=False)
    @classmethod
    def canonical_affected_item_id(cls, value: object) -> UUID:
        return _canonical_uuid(value)

    @field_validator("affected_item_ids", mode="before", check_fields=False)
    @classmethod
    def canonical_affected_item_ids(cls, value: object) -> tuple[UUID, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("invalid affected item references")
        return tuple(_canonical_uuid(item) for item in value)

    @field_validator(
        "document_refs",
        "affected_items",
        "affected_item_ids",
        "affected_item_references",
        "required_rewrites",
        "warnings",
        "operations",
        "proposed_edits",
        "applied_changes",
        "affected_documents",
        "findings",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def immutable_collections(cls, value: object) -> object:
        if type(value) is list:
            return tuple(value)
        return value


class DocumentVersionReference(_StrictMaintenanceModel):
    """A database identity pair; paths and document content are intentionally absent."""

    document_id: UUID
    current_version_id: UUID


class MaintenanceImpactRequest(_StrictMaintenanceModel):
    project_id: UUID
    workflow_run_id: UUID
    change_request_id: UUID
    change_request: str = Field(min_length=1, max_length=4000, repr=False)
    document_refs: tuple[DocumentVersionReference, ...] = Field(default=(), max_length=128)

    @field_validator("change_request")
    @classmethod
    def bounded_transient_request(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or "\x00" in value:
            raise ValueError("invalid change request")
        return value

    @model_validator(mode="after")
    def unique_document_refs(self) -> MaintenanceImpactRequest:
        identities = [(item.document_id, item.current_version_id) for item in self.document_refs]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate document reference")
        if len({item.document_id for item in self.document_refs}) != len(self.document_refs):
            raise ValueError("multiple current versions for one document")
        return self


class _AffectedItemFields(_StrictMaintenanceModel):
    stable_reference: str = Field(min_length=1, max_length=128)
    item_type: AffectedItemType
    impact_level: ImpactLevel
    document: DocumentVersionReference | None = None
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("stable_reference")
    @classmethod
    def bounded_stable_reference(cls, value: str) -> str:
        return _validated_stable_reference(value)

    @field_validator("item_type", mode="before")
    @classmethod
    def typed_item_type(cls, value: object) -> AffectedItemType:
        if isinstance(value, AffectedItemType):
            return value
        if type(value) is str:
            try:
                return AffectedItemType(value)
            except ValueError as error:
                raise ValueError("invalid affected item type") from error
        raise ValueError("invalid affected item type")

    @field_validator("impact_level", mode="before")
    @classmethod
    def typed_impact_level(cls, value: object) -> ImpactLevel:
        if isinstance(value, ImpactLevel):
            return value
        if type(value) is str:
            try:
                return ImpactLevel(value)
            except ValueError as error:
                raise ValueError("invalid impact level") from error
        raise ValueError("invalid impact level")

    @field_validator("reason")
    @classmethod
    def bounded_reason(cls, value: str) -> str:
        return _safe_planning_text(value, "affected-item reason")

    @model_validator(mode="after")
    def stable_reference_matches_item_type(self) -> "_AffectedItemFields":
        _validated_stable_reference(self.stable_reference, item_type=self.item_type)
        return self


class ImpactAffectedItem(_AffectedItemFields):
    """Provider-authored impact identity; it deliberately has no database identifier."""


class AffectedItemReference(_AffectedItemFields):
    """Server-persisted affected item supplied later to revision-planning agents."""

    affected_item_id: UUID


class ImpactWarning(_StrictMaintenanceModel):
    warning_id: UUID
    severity: WarningSeverity
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1000)
    affected_item_references: tuple[str, ...] = Field(min_length=1, max_length=64)

    @field_validator("severity", mode="before")
    @classmethod
    def typed_severity(cls, value: object) -> WarningSeverity:
        if isinstance(value, WarningSeverity):
            return value
        if type(value) is str:
            try:
                return WarningSeverity(value)
            except ValueError as error:
                raise ValueError("invalid warning severity") from error
        raise ValueError("invalid warning severity")

    @field_validator("code")
    @classmethod
    def safe_code(cls, value: str) -> str:
        if _MACHINE_IDENTIFIER.fullmatch(value) is None:
            raise ValueError("invalid warning code")
        return value

    @field_validator("message")
    @classmethod
    def bounded_message(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or "\x00" in value:
            raise ValueError("invalid warning message")
        return value

    @field_validator("affected_item_references", mode="before")
    @classmethod
    def stable_affected_item_references(cls, value: object) -> tuple[str, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("invalid affected item references")
        return tuple(_validated_stable_reference(item) for item in value)

    @model_validator(mode="after")
    def unique_affected_items(self) -> ImpactWarning:
        if len(set(self.affected_item_references)) != len(self.affected_item_references):
            raise ValueError("duplicate warning reference")
        return self


class RevisionWarning(_StrictMaintenanceModel):
    warning_id: UUID
    severity: WarningSeverity
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1000)
    affected_item_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)

    @field_validator("severity", mode="before")
    @classmethod
    def typed_severity(cls, value: object) -> WarningSeverity:
        if isinstance(value, WarningSeverity):
            return value
        if type(value) is str:
            try:
                return WarningSeverity(value)
            except ValueError as error:
                raise ValueError("invalid warning severity") from error
        raise ValueError("invalid warning severity")

    @field_validator("code")
    @classmethod
    def safe_code(cls, value: str) -> str:
        if _MACHINE_IDENTIFIER.fullmatch(value) is None:
            raise ValueError("invalid warning code")
        return value

    @field_validator("message")
    @classmethod
    def bounded_message(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or "\x00" in value:
            raise ValueError("invalid warning message")
        return value

    @model_validator(mode="after")
    def unique_affected_items(self) -> RevisionWarning:
        if len(set(self.affected_item_ids)) != len(self.affected_item_ids):
            raise ValueError("duplicate warning reference")
        return self


class RewriteRequirement(_StrictMaintenanceModel):
    requirement_id: UUID
    affected_item_reference: str = Field(min_length=1, max_length=128)
    document: DocumentVersionReference
    instruction: str = Field(min_length=1, max_length=1000)

    @field_validator("instruction")
    @classmethod
    def bounded_instruction(cls, value: str) -> str:
        return _safe_planning_text(value, "rewrite instruction")

    @field_validator("affected_item_reference")
    @classmethod
    def stable_affected_item_reference(cls, value: str) -> str:
        return _validated_stable_reference(value)


class MaintenanceImpactOutput(_StrictMaintenanceModel):
    affected_items: tuple[ImpactAffectedItem, ...] = Field(min_length=1, max_length=64)
    impact_summary: str = Field(min_length=1, max_length=2000)
    required_rewrites: tuple[RewriteRequirement, ...] = Field(default=(), max_length=64)
    safe_to_change: bool
    warnings: tuple[ImpactWarning, ...] = Field(default=(), max_length=64)

    @field_validator("impact_summary")
    @classmethod
    def bounded_summary(cls, value: str) -> str:
        return _safe_planning_text(value, "impact summary")

    @model_validator(mode="after")
    def internally_consistent(self) -> MaintenanceImpactOutput:
        stable_references = {item.stable_reference for item in self.affected_items}
        if len(stable_references) != len(self.affected_items):
            raise ValueError("duplicate stable affected-item reference")
        requirement_ids = {item.requirement_id for item in self.required_rewrites}
        if len(requirement_ids) != len(self.required_rewrites):
            raise ValueError("duplicate rewrite requirement id")
        warning_ids = {item.warning_id for item in self.warnings}
        if len(warning_ids) != len(self.warnings):
            raise ValueError("duplicate warning id")
        for requirement in self.required_rewrites:
            if requirement.affected_item_reference not in stable_references:
                raise ValueError("rewrite requirement has an unknown reference")
            affected_item = next(
                item
                for item in self.affected_items
                if item.stable_reference == requirement.affected_item_reference
            )
            if affected_item.document is not None and affected_item.document != requirement.document:
                raise ValueError("rewrite target does not match its affected item")
        if any(
            not set(item.affected_item_references) <= stable_references
            for item in self.warnings
        ):
            raise ValueError("warning has an unknown affected item reference")
        has_blocker = any(item.severity is WarningSeverity.BLOCKING for item in self.warnings)
        if self.safe_to_change is has_blocker:
            raise ValueError("safety flag contradicts blocking warnings")
        return self


class LoreImpactOutput(MaintenanceImpactOutput):
    pass


class ChiefEditorMaintenanceImpactOutput(MaintenanceImpactOutput):
    """Commercial review adds explicit reader-expectation and market impact levels."""

    reader_expectation_impact: ImpactLevel
    commercial_impact: ImpactLevel

    @field_validator("reader_expectation_impact", "commercial_impact", mode="before")
    @classmethod
    def typed_commercial_impact(cls, value: object) -> ImpactLevel:
        if isinstance(value, ImpactLevel):
            return value
        if type(value) is str:
            try:
                return ImpactLevel(value)
            except ValueError as error:
                raise ValueError("invalid commercial impact level") from error
        raise ValueError("invalid commercial impact level")


ChiefEditorImpactOutput = ChiefEditorMaintenanceImpactOutput
LoreMaintenanceImpactOutput = LoreImpactOutput


class RevisionPlanRequest(_StrictMaintenanceModel):
    project_id: UUID
    workflow_run_id: UUID
    change_request_id: UUID
    change_request: str = Field(min_length=1, max_length=4000, repr=False)
    affected_items: tuple[AffectedItemReference, ...] = Field(min_length=1, max_length=64)
    document_refs: tuple[DocumentVersionReference, ...] = Field(min_length=1, max_length=128)

    @field_validator("change_request")
    @classmethod
    def bounded_transient_request(cls, value: str) -> str:
        if not value.strip() or value != value.strip() or "\x00" in value:
            raise ValueError("invalid change request")
        return value

    @model_validator(mode="after")
    def known_unique_references(self) -> RevisionPlanRequest:
        item_ids = [item.affected_item_id for item in self.affected_items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("duplicate affected item id")
        stable_references = [item.stable_reference for item in self.affected_items]
        if len(set(stable_references)) != len(stable_references):
            raise ValueError("duplicate stable affected-item reference")
        documents = [(item.document_id, item.current_version_id) for item in self.document_refs]
        if len(set(documents)) != len(documents):
            raise ValueError("duplicate document reference")
        if len({item.document_id for item in self.document_refs}) != len(self.document_refs):
            raise ValueError("multiple current versions for one document")
        known_documents = set(documents)
        if any(
            item.document is not None
            and (item.document.document_id, item.document.current_version_id)
            not in known_documents
            for item in self.affected_items
        ):
            raise ValueError("affected item uses an unknown document reference")
        return self


class RevisionOperation(_StrictMaintenanceModel):
    operation_id: UUID
    sequence: Annotated[int, Field(ge=1, le=128)]
    operation: RevisionOperationKind
    target: DocumentVersionReference
    affected_item_ids: tuple[UUID, ...] = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1, max_length=1000)

    @field_validator("operation", mode="before")
    @classmethod
    def typed_operation(cls, value: object) -> RevisionOperationKind:
        if isinstance(value, RevisionOperationKind):
            return value
        if type(value) is str:
            try:
                return RevisionOperationKind(value)
            except ValueError as error:
                raise ValueError("invalid revision operation") from error
        raise ValueError("invalid revision operation")

    @field_validator("instruction")
    @classmethod
    def no_path_or_content_authority(cls, value: str) -> str:
        return _safe_planning_text(value, "revision instruction")

    @model_validator(mode="after")
    def unique_affected_items(self) -> RevisionOperation:
        if len(set(self.affected_item_ids)) != len(self.affected_item_ids):
            raise ValueError("duplicate operation affected item")
        return self


class RevisionSafety(_StrictMaintenanceModel):
    requires_user_confirmation: Literal[True]
    preserve_existing_versions: Literal[True]
    direct_write_authority: Literal[False]


class RevisionPlanOutput(_StrictMaintenanceModel):
    plan_id: UUID
    summary: str = Field(min_length=1, max_length=2000)
    operations: tuple[RevisionOperation, ...] = Field(min_length=1, max_length=128)
    safety: RevisionSafety
    warnings: tuple[RevisionWarning, ...] = Field(default=(), max_length=64)

    @field_validator("summary")
    @classmethod
    def bounded_summary(cls, value: str) -> str:
        return _safe_planning_text(value, "revision summary")

    @model_validator(mode="after")
    def deterministic_operation_order(self) -> RevisionPlanOutput:
        operation_ids = [item.operation_id for item in self.operations]
        sequences = [item.sequence for item in self.operations]
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("duplicate revision operation id")
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("revision operations are not in canonical sequence")
        if len({item.warning_id for item in self.warnings}) != len(self.warnings):
            raise ValueError("duplicate warning id")
        return self


class ApplyChangeRequest(_StrictMaintenanceModel):
    """Approved, content-free provenance supplied to the proposal-only Archivist."""

    project_id: UUID
    workflow_run_id: UUID
    change_request_id: UUID
    approval_id: UUID
    revision_plan_id: UUID
    revision_plan_document_id: UUID
    revision_plan_version_id: UUID
    operations: tuple[RevisionOperation, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def approved_revisions_only(self) -> ApplyChangeRequest:
        operation_ids = [item.operation_id for item in self.operations]
        sequences = [item.sequence for item in self.operations]
        document_ids = [item.target.document_id for item in self.operations]
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("duplicate revision operation id")
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("revision operations are not in canonical sequence")
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("multiple operations target one document")
        if any(item.operation is RevisionOperationKind.RETIRE for item in self.operations):
            raise ValueError("unsupported apply-change revision operation")
        if not any(item.operation is RevisionOperationKind.REVISE for item in self.operations):
            raise ValueError("apply-change request has no proposed edit operation")
        return self


class ProposedDocumentEdit(_StrictMaintenanceModel):
    """A proposed version body, never an instruction to mutate canonical storage."""

    proposed_edit_id: UUID
    sequence: Annotated[int, Field(ge=1, le=128)]
    project_id: UUID
    workflow_run_id: UUID
    change_request_id: UUID
    approval_id: UUID
    revision_plan_id: UUID
    revision_plan_document_id: UUID
    revision_plan_version_id: UUID
    revision_operation_id: UUID
    document_id: UUID
    expected_current_version_id: UUID
    operation: ProposedEditOperation
    content: str = Field(
        min_length=1,
        max_length=_MAX_PROPOSED_CONTENT_CHARACTERS,
        repr=False,
    )
    rationale: str = Field(min_length=1, max_length=1000)

    @field_validator("operation", mode="before")
    @classmethod
    def typed_operation(cls, value: object) -> ProposedEditOperation:
        if isinstance(value, ProposedEditOperation):
            return value
        if type(value) is str:
            try:
                return ProposedEditOperation(value)
            except ValueError as error:
                raise ValueError("unsupported proposed edit operation") from error
        raise ValueError("unsupported proposed edit operation")

    @field_validator("content")
    @classmethod
    def bounded_safe_content(cls, value: str) -> str:
        if not value.strip() or any(
            unicodedata.category(character) == "Cc" and character not in {"\t", "\n", "\r"}
            for character in value
        ):
            raise ValueError("invalid proposed document content")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("invalid proposed document content") from None
        if len(encoded) > _MAX_PROPOSED_CONTENT_BYTES:
            raise ValueError("proposed document content is too large")
        return value

    @field_validator("rationale")
    @classmethod
    def bounded_rationale(cls, value: str) -> str:
        return _safe_planning_text(value, "proposed edit rationale")


class ApplyChangeOutput(_StrictMaintenanceModel):
    """A validated proposal set; it intentionally has no applied/version-written flags."""

    change_set_id: UUID
    project_id: UUID
    workflow_run_id: UUID
    change_request_id: UUID
    approval_id: UUID
    revision_plan_id: UUID
    revision_plan_document_id: UUID
    revision_plan_version_id: UUID
    proposed_edits: tuple[ProposedDocumentEdit, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def deterministic_unique_edits(self) -> ApplyChangeOutput:
        edit_ids = [item.proposed_edit_id for item in self.proposed_edits]
        operation_ids = [item.revision_operation_id for item in self.proposed_edits]
        document_ids = [item.document_id for item in self.proposed_edits]
        sequences = [item.sequence for item in self.proposed_edits]
        if len(set(edit_ids)) != len(edit_ids):
            raise ValueError("duplicate proposed edit id")
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("duplicate proposed revision operation")
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("multiple proposed edits target one document")
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("proposed edits are not in canonical sequence")
        lineage = (
            self.project_id,
            self.workflow_run_id,
            self.change_request_id,
            self.approval_id,
            self.revision_plan_id,
            self.revision_plan_document_id,
            self.revision_plan_version_id,
        )
        if any(
            (
                item.project_id,
                item.workflow_run_id,
                item.change_request_id,
                item.approval_id,
                item.revision_plan_id,
                item.revision_plan_document_id,
                item.revision_plan_version_id,
            )
            != lineage
            for item in self.proposed_edits
        ):
            raise ValueError("proposed edit lineage is inconsistent")
        return self


class AppliedDocumentReference(_StrictMaintenanceModel):
    """Server-issued result of mediating one proposed edit through canonical services."""

    proposed_edit_id: UUID
    document_id: UUID
    previous_version_id: UUID
    current_version_id: UUID

    @model_validator(mode="after")
    def version_advanced(self) -> AppliedDocumentReference:
        if self.previous_version_id == self.current_version_id:
            raise ValueError("applied document version did not advance")
        return self


class PostChangeRequest(_StrictMaintenanceModel):
    project_id: UUID
    workflow_run_id: UUID
    change_request_id: UUID
    approval_id: UUID
    revision_plan_id: UUID
    revision_plan_document_id: UUID
    revision_plan_version_id: UUID
    change_set_id: UUID
    applied_changes: tuple[AppliedDocumentReference, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_applied_changes(self) -> PostChangeRequest:
        edit_ids = [item.proposed_edit_id for item in self.applied_changes]
        document_ids = [item.document_id for item in self.applied_changes]
        previous_versions = [item.previous_version_id for item in self.applied_changes]
        current_versions = [item.current_version_id for item in self.applied_changes]
        if (
            len(set(edit_ids)) != len(edit_ids)
            or len(set(document_ids)) != len(document_ids)
            or len(set(previous_versions)) != len(previous_versions)
            or len(set(current_versions)) != len(current_versions)
        ):
            raise ValueError("duplicate applied change reference")
        return self


class ConsistencyFinding(_StrictMaintenanceModel):
    finding_id: UUID
    sequence: Annotated[int, Field(ge=1, le=128)]
    code: str = Field(min_length=1, max_length=64)
    severity: ConsistencyFindingSeverity
    affected_documents: tuple[DocumentVersionReference, ...] = Field(
        min_length=1, max_length=128
    )
    blocking: bool
    suggested_corrective_action: str = Field(min_length=1, max_length=1000)

    @field_validator("code")
    @classmethod
    def stable_code(cls, value: str) -> str:
        if _MACHINE_IDENTIFIER.fullmatch(value) is None:
            raise ValueError("invalid consistency finding code")
        return value

    @field_validator("severity", mode="before")
    @classmethod
    def typed_severity(cls, value: object) -> ConsistencyFindingSeverity:
        if isinstance(value, ConsistencyFindingSeverity):
            return value
        if type(value) is str:
            try:
                return ConsistencyFindingSeverity(value)
            except ValueError as error:
                raise ValueError("invalid consistency finding severity") from error
        raise ValueError("invalid consistency finding severity")

    @field_validator("suggested_corrective_action")
    @classmethod
    def bounded_corrective_action(cls, value: str) -> str:
        return _safe_planning_text(value, "corrective action")

    @model_validator(mode="after")
    def severity_matches_blocking_flag(self) -> ConsistencyFinding:
        if self.blocking is not (self.severity is ConsistencyFindingSeverity.BLOCKING):
            raise ValueError("consistency finding blocking flag contradicts severity")
        documents = [
            (item.document_id, item.current_version_id) for item in self.affected_documents
        ]
        if len(set(documents)) != len(documents):
            raise ValueError("duplicate consistency affected document")
        return self


class ConsistencyReviewOutput(_StrictMaintenanceModel):
    """Advisory routing result; canonical workflow state remains server-owned."""

    review_id: UUID
    project_id: UUID
    workflow_run_id: UUID
    change_request_id: UUID
    approval_id: UUID
    revision_plan_id: UUID
    revision_plan_document_id: UUID
    revision_plan_version_id: UUID
    change_set_id: UUID
    outcome: ConsistencyReviewOutcome
    findings: tuple[ConsistencyFinding, ...] = Field(default=(), max_length=128)

    @field_validator("outcome", mode="before")
    @classmethod
    def typed_outcome(cls, value: object) -> ConsistencyReviewOutcome:
        if isinstance(value, ConsistencyReviewOutcome):
            return value
        if type(value) is str:
            try:
                return ConsistencyReviewOutcome(value)
            except ValueError as error:
                raise ValueError("invalid consistency review outcome") from error
        raise ValueError("invalid consistency review outcome")

    @model_validator(mode="after")
    def consistent_outcome(self) -> ConsistencyReviewOutput:
        sequences = [item.sequence for item in self.findings]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("consistency findings are not in canonical sequence")
        if len({item.finding_id for item in self.findings}) != len(self.findings):
            raise ValueError("duplicate consistency finding id")
        blockers = [item for item in self.findings if item.blocking]
        if self.outcome is ConsistencyReviewOutcome.CLEAN and self.findings:
            raise ValueError("clean consistency review cannot contain findings")
        if self.outcome is ConsistencyReviewOutcome.WARNING and (
            not self.findings or blockers
        ):
            raise ValueError("warning consistency review has contradictory findings")
        if self.outcome is ConsistencyReviewOutcome.BLOCKING and not blockers:
            raise ValueError("blocking consistency review requires a blocking finding")
        return self


def _validate_output(raw_output: object, output_type: type[_StrictMaintenanceModel]):
    try:
        if isinstance(raw_output, BaseModel):
            raw_output = raw_output.model_dump(mode="json")
        return output_type.model_validate(raw_output)
    except (TypeError, ValueError, ValidationError):
        pass
    raise ProviderInvalidOutputError() from None


def validate_lore_impact_output(
    raw_output: object, *, request: MaintenanceImpactRequest | None = None
) -> LoreImpactOutput:
    result = _validate_output(raw_output, LoreImpactOutput)
    assert isinstance(result, LoreImpactOutput)
    _validate_impact_references(result, request)
    return result


def validate_chief_editor_impact_output(
    raw_output: object, *, request: MaintenanceImpactRequest | None = None
) -> ChiefEditorMaintenanceImpactOutput:
    result = _validate_output(raw_output, ChiefEditorMaintenanceImpactOutput)
    assert isinstance(result, ChiefEditorMaintenanceImpactOutput)
    _validate_impact_references(result, request)
    return result


def _validate_impact_references(
    result: MaintenanceImpactOutput, request: MaintenanceImpactRequest | None
) -> None:
    if request is None:
        return
    known = {(item.document_id, item.current_version_id) for item in request.document_refs}
    if any(
        item.document is not None
        and (item.document.document_id, item.document.current_version_id) not in known
        for item in result.affected_items
    ) or any(
        (item.document.document_id, item.document.current_version_id) not in known
        for item in result.required_rewrites
    ):
        raise ProviderInvalidOutputError() from None


def validate_revision_plan_output(
    raw_output: object, *, request: RevisionPlanRequest | None = None
) -> RevisionPlanOutput:
    result = _validate_output(raw_output, RevisionPlanOutput)
    assert isinstance(result, RevisionPlanOutput)
    if request is None:
        return result
    known_items = {item.affected_item_id: item for item in request.affected_items}
    known_documents = {
        (item.document_id, item.current_version_id) for item in request.document_refs
    }
    if any(
        not set(operation.affected_item_ids) <= set(known_items)
        or (operation.target.document_id, operation.target.current_version_id)
        not in known_documents
        or any(
            known_items[item_id].document is not None
            and known_items[item_id].document != operation.target
            for item_id in operation.affected_item_ids
            if item_id in known_items
        )
        for operation in result.operations
    ) or any(
        not set(warning.affected_item_ids) <= set(known_items) for warning in result.warnings
    ):
        raise ProviderInvalidOutputError() from None
    return result


def validate_apply_change_output(
    raw_output: object, *, request: ApplyChangeRequest
) -> ApplyChangeOutput:
    result = _validate_output(raw_output, ApplyChangeOutput)
    assert isinstance(result, ApplyChangeOutput)
    request_lineage = (
        request.project_id,
        request.workflow_run_id,
        request.change_request_id,
        request.approval_id,
        request.revision_plan_id,
        request.revision_plan_document_id,
        request.revision_plan_version_id,
    )
    result_lineage = (
        result.project_id,
        result.workflow_run_id,
        result.change_request_id,
        result.approval_id,
        result.revision_plan_id,
        result.revision_plan_document_id,
        result.revision_plan_version_id,
    )
    operations = [
        item for item in request.operations if item.operation is RevisionOperationKind.REVISE
    ]
    operations_by_id = {item.operation_id: item for item in operations}
    if result_lineage != request_lineage or len(result.proposed_edits) != len(operations):
        raise ProviderInvalidOutputError() from None
    for sequence, edit in enumerate(result.proposed_edits, start=1):
        operation = operations_by_id.get(edit.revision_operation_id)
        if (
            operation is None
            or edit.sequence != sequence
            or operation is not operations[sequence - 1]
            or edit.document_id != operation.target.document_id
            or edit.expected_current_version_id != operation.target.current_version_id
        ):
            raise ProviderInvalidOutputError() from None
    return result


def validate_consistency_review_output(
    raw_output: object, *, request: PostChangeRequest
) -> ConsistencyReviewOutput:
    result = _validate_output(raw_output, ConsistencyReviewOutput)
    assert isinstance(result, ConsistencyReviewOutput)
    if (
        result.project_id,
        result.workflow_run_id,
        result.change_request_id,
        result.approval_id,
        result.revision_plan_id,
        result.revision_plan_document_id,
        result.revision_plan_version_id,
        result.change_set_id,
    ) != (
        request.project_id,
        request.workflow_run_id,
        request.change_request_id,
        request.approval_id,
        request.revision_plan_id,
        request.revision_plan_document_id,
        request.revision_plan_version_id,
        request.change_set_id,
    ):
        raise ProviderInvalidOutputError() from None
    known_documents = {
        (item.document_id, item.current_version_id) for item in request.applied_changes
    }
    if any(
        (document.document_id, document.current_version_id) not in known_documents
        for finding in result.findings
        for document in finding.affected_documents
    ):
        raise ProviderInvalidOutputError() from None
    return result


# Naming aliases retain the stage terminology used by workflow/design callers.
ArchivistApplyChangeRequest = ApplyChangeRequest
ArchivistApplyChangeOutput = ApplyChangeOutput
LorePostChangeRequest = PostChangeRequest
LorePostChangeOutput = ConsistencyReviewOutput


def validate_maintenance_stable_reference(
    value: object, *, item_type: AffectedItemType
) -> str:
    """Validate a provider-authored reference before exposing persisted data."""

    return _validated_stable_reference(value, item_type=item_type)


def validate_public_maintenance_text(value: str, field: str) -> str:
    """Validate provider-authored text at the public projection boundary."""

    return _safe_planning_text(value, field)


__all__ = [
    "AffectedItemReference",
    "AffectedItemType",
    "AppliedDocumentReference",
    "ApplyChangeOutput",
    "ApplyChangeRequest",
    "ArchivistApplyChangeOutput",
    "ArchivistApplyChangeRequest",
    "ChiefEditorImpactOutput",
    "ChiefEditorMaintenanceImpactOutput",
    "DocumentVersionReference",
    "ImpactAffectedItem",
    "ImpactLevel",
    "ImpactWarning",
    "ConsistencyFinding",
    "ConsistencyFindingSeverity",
    "ConsistencyReviewOutcome",
    "ConsistencyReviewOutput",
    "LoreImpactOutput",
    "LoreMaintenanceImpactOutput",
    "MaintenanceImpactOutput",
    "MaintenanceImpactRequest",
    "LorePostChangeOutput",
    "LorePostChangeRequest",
    "PostChangeRequest",
    "ProposedDocumentEdit",
    "ProposedEditOperation",
    "RevisionOperation",
    "RevisionOperationKind",
    "RevisionPlanOutput",
    "RevisionPlanRequest",
    "RevisionSafety",
    "RevisionWarning",
    "RewriteRequirement",
    "WarningSeverity",
    "validate_chief_editor_impact_output",
    "validate_apply_change_output",
    "validate_consistency_review_output",
    "validate_lore_impact_output",
    "validate_maintenance_stable_reference",
    "validate_public_maintenance_text",
    "validate_revision_plan_output",
]
