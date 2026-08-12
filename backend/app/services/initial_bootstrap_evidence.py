"""Pure pristine-run evidence for Chapter Production V2 bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.documents.chapter_segments import CURRENT_CHAPTER_SEGMENTER_VERSION
from app.services.chapter_production_v2_contracts import ChapterProductionV2ValidationError
from app.workflows.chapter_production import ChapterProductionState


_CONTRACT_VERSION = "chapter-production-v2"
_REVIEW_POLICY_VERSION = "chapter-quality-v1"


def _invalid() -> ChapterProductionV2ValidationError:
    return ChapterProductionV2ValidationError()


def _valid_uuid(value: object) -> bool:
    return type(value) is UUID and value.int != 0


def _valid_hash(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _require_binding(value: object) -> InitialBootstrapBinding:
    failed = type(value) is not InitialBootstrapBinding
    if not failed:
        try:
            value.__post_init__()
        except BaseException:
            failed = True
    if failed:
        raise _invalid() from None
    return value


def _exact_payload(value: object, expected: dict[str, object]) -> bool:
    if type(value) is not dict:
        return False
    keys = tuple(value)
    if any(type(key) is not str for key in keys) or set(keys) != set(expected):
        return False
    for key, expected_value in expected.items():
        actual = value[key]
        if (expected_value is None and actual is not None) or (
            expected_value is not None
            and (type(actual) is not type(expected_value) or actual != expected_value)
        ):
            return False
    return True


@dataclass(frozen=True, slots=True, repr=False)
class InitialBootstrapBinding:
    workflow_run_id: UUID
    chapter_id: UUID
    outline_document_id: UUID
    outline_version_id: UUID
    outline_content_hash: str
    operation_key: str
    chief_editor_required: bool

    def __post_init__(self) -> None:
        if (
            not all(
                _valid_uuid(value)
                for value in (
                    self.workflow_run_id, self.chapter_id,
                    self.outline_document_id, self.outline_version_id,
                )
            )
            or not _valid_hash(self.outline_content_hash)
            or not _valid_hash(self.operation_key)
            or type(self.chief_editor_required) is not bool
        ):
            raise _invalid() from None

    def __repr__(self) -> str:
        return "InitialBootstrapBinding()"


def pristine_run_metadata(binding: InitialBootstrapBinding) -> dict[str, object]:
    binding = _require_binding(binding)
    return {
        "contract_version": _CONTRACT_VERSION,
        "review_policy_version": _REVIEW_POLICY_VERSION,
        "chief_editor_required": binding.chief_editor_required,
        "outline_document_id": str(binding.outline_document_id),
        "outline_version_id": str(binding.outline_version_id),
        "outline_content_hash": binding.outline_content_hash,
        "segmenter_version": CURRENT_CHAPTER_SEGMENTER_VERSION,
        "operation_key": binding.operation_key,
        "provider_attempt": None,
        "reviewer_claim": None,
    }


def pristine_checkpoint(binding: InitialBootstrapBinding) -> dict[str, object]:
    binding = _require_binding(binding)
    failed = False
    try:
        checkpoint = ChapterProductionState.initial(
            chapter_workflow_run_id=str(binding.workflow_run_id),
            chapter_id=str(binding.chapter_id),
            review_policy_version=_REVIEW_POLICY_VERSION,
            chief_editor_required=binding.chief_editor_required,
        ).to_checkpoint()
    except BaseException:
        failed = True
    if failed:
        raise _invalid() from None
    return checkpoint


def _validated_metadata(binding: InitialBootstrapBinding, value: object) -> dict[str, object]:
    expected = pristine_run_metadata(binding)
    if type(value) is not dict:
        raise _invalid() from None
    keys = tuple(value)
    if any(type(key) is not str for key in keys):
        raise _invalid() from None
    legacy = {key: item for key, item in expected.items() if key != "reviewer_claim"}
    if _exact_payload(value, expected) or (
        set(keys) == set(legacy) and _exact_payload(value, legacy)
    ):
        return expected
    raise _invalid() from None


def validate_pristine_initial_evidence(
    binding: InitialBootstrapBinding,
    *,
    workflow_type: object,
    status: object,
    current_node: object,
    next_node: object,
    awaiting_user: object,
    metadata: object,
    checkpoint_markers: object,
) -> dict[str, object]:
    binding = _require_binding(binding)
    if (
        type(checkpoint_markers) is not tuple
        or len(checkpoint_markers) != 1
        or type(checkpoint_markers[0]) is not tuple
        or len(checkpoint_markers[0]) != 3
    ):
        raise _invalid() from None
    checkpoint_index, checkpoint_node_name, checkpoint_state = checkpoint_markers[0]
    expected_projection = ("chapter_production", "DRAFTING", "drafting", None, False, 0, "drafting")
    actual_projection = (
        workflow_type,
        status,
        current_node,
        next_node,
        awaiting_user,
        checkpoint_index,
        checkpoint_node_name,
    )
    if any(
        type(actual) is not type(expected) or actual != expected
        for actual, expected in zip(actual_projection, expected_projection, strict=True)
    ):
        raise _invalid() from None
    normalized = _validated_metadata(binding, metadata)
    if not _exact_payload(checkpoint_state, pristine_checkpoint(binding)):
        raise _invalid() from None
    return normalized


__all__ = [
    "InitialBootstrapBinding", "pristine_checkpoint", "pristine_run_metadata",
    "validate_pristine_initial_evidence",
]
