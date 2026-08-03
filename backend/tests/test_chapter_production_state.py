from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from itertools import product

import pytest

from app.models.enums import ActionRequestStatus
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterFailureCode,
    ChapterFailureReconciliationBinding,
    ChapterFailureReconciliationOutcome,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterProductionValidationError,
    ChapterReviewOutcome,
    ChapterReviewBinding,
    ChapterReviewStage,
    LegacyChapterProductionSnapshot,
)


RUN_ID = "088f8342-dcc9-49ce-9cc9-d93a2a48e211"
CHAPTER_ID = "5168d6a9-8ae5-4fc7-8f92-30d8eb696692"
DOCUMENT_ID = "5b9f53dd-17a7-4b89-aef5-f9f74dc3370a"
VERSION_1 = "9af9afc2-2f9c-4cf0-974b-fb6bc1667a48"
VERSION_2 = "66a61b49-3801-48f3-97cc-b071987a58a2"
ACTION_ID = "b5beae0b-8be1-46b2-bf48-b6cda3239ea7"
OTHER_ACTION_ID = "fc059a3b-17f4-4b61-98ad-492d66393f32"
EDITOR_REPORT_ID = "0a8a5147-b2b9-4796-9eb3-10530e47b1d8"
CHIEF_REPORT_ID = "28db6a5f-425f-4132-854e-a88ce2572683"
LORE_REPORT_ID = "91a38213-71c7-4adc-b50c-a3f2bb2ce22b"
HASH_1 = "1" * 64
HASH_2 = "2" * 64
POLICY = "chapter-quality-v1"


def initial(*, chief_required: bool = True) -> ChapterProductionState:
    return ChapterProductionState.initial(
        chapter_workflow_run_id=RUN_ID,
        chapter_id=CHAPTER_ID,
        review_policy_version=POLICY,
        chief_editor_required=chief_required,
    )


def action(
    kind: ChapterActionKind,
    *,
    action_id: str = ACTION_ID,
    workflow_run_id: str = RUN_ID,
    chapter_id: str = CHAPTER_ID,
    request_type: str | None = None,
    status: ActionRequestStatus = ActionRequestStatus.PENDING,
    pending_count: int = 1,
    document_id: str = DOCUMENT_ID,
    document_version_id: str = VERSION_1,
    content_hash: str = HASH_1,
    current_document_id: str = DOCUMENT_ID,
    current_document_version_id: str = VERSION_1,
    current_content_hash: str = HASH_1,
) -> ChapterActionBinding:
    expected_type = {
        ChapterActionKind.AUTHOR_REVISION: "chapter_author_revision",
        ChapterActionKind.REVIEW_WARNING: "chapter_review_warning",
        ChapterActionKind.REVIEW_REVISION: "chapter_review_revision",
    }[kind]
    return ChapterActionBinding(
        action_request_id=action_id,
        workflow_run_id=workflow_run_id,
        chapter_id=chapter_id,
        request_type=request_type or expected_type,
        kind=kind,
        status=status,
        pending_count=pending_count,
        document_id=document_id,
        document_version_id=document_version_id,
        content_hash=content_hash,
        current_document_id=current_document_id,
        current_document_version_id=current_document_version_id,
        current_content_hash=current_content_hash,
    )


def author_gate(*, chief_required: bool = True) -> ChapterProductionState:
    return initial(chief_required=chief_required).submit_draft(
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_1,
        content_hash=HASH_1,
        action=action(ChapterActionKind.AUTHOR_REVISION),
    )


def editor_review(*, chief_required: bool = True) -> ChapterProductionState:
    return author_gate(chief_required=chief_required).resolve_action(
        action=action(ChapterActionKind.AUTHOR_REVISION),
        decision=ChapterActionDecision.ACCEPT,
    )


def binding(
    state: ChapterProductionState,
    stage: ChapterReviewStage,
    report_id: str,
    *,
    passed: bool = True,
    workflow_run_id: str = RUN_ID,
    chapter_id: str = CHAPTER_ID,
    document_id: str = DOCUMENT_ID,
    document_version_id: str | None = None,
) -> ChapterReviewBinding:
    mode, role = {
        ChapterReviewStage.EDITOR: ("chapter_editor", "editor_agent"),
        ChapterReviewStage.CHIEF_EDITOR: ("chapter_chief_final", "chief_editor_agent"),
        ChapterReviewStage.LORE: ("chapter_final_lore", "lore_agent"),
    }[stage]
    return ChapterReviewBinding(
        report_id=report_id,
        stage=stage,
        workflow_run_id=workflow_run_id,
        chapter_id=chapter_id,
        document_id=document_id,
        document_version_id=document_version_id or state.document_version_id or VERSION_1,
        review_mode=mode,
        reviewer_agent_role=role,
        passed=passed,
    )


def review_passed(
    state: ChapterProductionState,
    stage: ChapterReviewStage,
    report_id: str,
) -> ChapterProductionState:
    return state.record_review(
        outcome=ChapterReviewOutcome.PASSED,
        review=binding(state, stage, report_id),
    )


def revision_ready(*, chief_required: bool = True) -> ChapterProductionState:
    state = review_passed(editor_review(chief_required=chief_required), ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
    if chief_required:
        state = review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
    pre_ready = review_passed(state, ChapterReviewStage.LORE, LORE_REPORT_ID)
    return pre_ready.finalize_revision_ready(
        document_id=DOCUMENT_ID,
        current_document_version_id=VERSION_1,
        version_content_hash=HASH_1,
        editor_report=binding(pre_ready, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
        chief_editor_report=(
            binding(pre_ready, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
            if chief_required
            else None
        ),
        lore_report=binding(pre_ready, ChapterReviewStage.LORE, LORE_REPORT_ID),
    )


def lore_pre_ready(*, chief_required: bool = True) -> ChapterProductionState:
    state = review_passed(
        editor_review(chief_required=chief_required),
        ChapterReviewStage.EDITOR,
        EDITOR_REPORT_ID,
    )
    if chief_required:
        state = review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
    return review_passed(state, ChapterReviewStage.LORE, LORE_REPORT_ID)


def review_gate(stage: ChapterReviewStage) -> ChapterProductionState:
    state = editor_review()
    if stage is ChapterReviewStage.EDITOR:
        return state
    state = review_passed(state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
    if stage is ChapterReviewStage.CHIEF_EDITOR:
        return state
    return review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)


def finding_gate(
    stage: ChapterReviewStage, outcome: ChapterReviewOutcome
) -> ChapterProductionState:
    state = review_gate(stage)
    report_id = {
        ChapterReviewStage.EDITOR: EDITOR_REPORT_ID,
        ChapterReviewStage.CHIEF_EDITOR: CHIEF_REPORT_ID,
        ChapterReviewStage.LORE: LORE_REPORT_ID,
    }[stage]
    kind = (
        ChapterActionKind.REVIEW_WARNING
        if outcome is ChapterReviewOutcome.WARNING
        else ChapterActionKind.REVIEW_REVISION
    )
    return state.record_review(
        outcome=outcome,
        review=binding(
            state,
            stage,
            report_id,
            passed=outcome is ChapterReviewOutcome.WARNING,
        ),
        action=action(kind),
    )


def restore_finalized_checkpoint(state: ChapterProductionState) -> ChapterProductionState:
    return ChapterProductionState.from_revision_ready_checkpoint(
        state.to_checkpoint(),
        workflow_run_id=RUN_ID,
        chapter_id=CHAPTER_ID,
        run_workflow_type="chapter_production",
        run_status=state.status.value,
        run_current_node=state.current_node,
        run_awaiting_user=state.awaiting_user,
        checkpoint_workflow_run_id=RUN_ID,
        checkpoint_node_name=state.current_node,
        document_id=DOCUMENT_ID,
        current_document_version_id=VERSION_1,
        version_content_hash=HASH_1,
        editor_report=binding(state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
        chief_editor_report=binding(
            state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID
        ),
        lore_report=binding(state, ChapterReviewStage.LORE, LORE_REPORT_ID),
    )


def failure_reconciliation(
    state: ChapterProductionState,
    outcome: ChapterFailureReconciliationOutcome,
    *,
    failure_code: ChapterFailureCode | None = None,
    workflow_run_id: str = RUN_ID,
    chapter_id: str = CHAPTER_ID,
    document_id: str = DOCUMENT_ID,
    current_document_version_id: str = VERSION_1,
    current_content_hash: str = HASH_1,
) -> ChapterFailureReconciliationBinding:
    assert state.failure_code is not None
    return ChapterFailureReconciliationBinding(
        workflow_run_id=workflow_run_id,
        chapter_id=chapter_id,
        failure_code=failure_code or state.failure_code,
        outcome=outcome,
        document_id=document_id,
        current_document_version_id=current_document_version_id,
        current_content_hash=current_content_hash,
    )


def test_statuses_cover_the_v2_durable_lifecycle() -> None:
    assert {status.value for status in ChapterProductionStatus} == {
        "DRAFTING",
        "AUTHOR_REVISION",
        "EDITOR_REVIEW",
        "REVIEW_REVISION",
        "CHIEF_FINAL_REVIEW",
        "LORE_FINAL_REVIEW",
        "REVISION_READY",
        "ARCHIVE_UPDATE",
        "COMPLETED",
        "CANCELLED",
        "FAILED",
    }


def test_happy_path_requires_exact_version_bound_editor_chief_and_lore_reports() -> None:
    state = editor_review()
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW

    state = review_passed(state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
    assert state.status is ChapterProductionStatus.CHIEF_FINAL_REVIEW
    state = review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
    assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW
    state = review_passed(state, ChapterReviewStage.LORE, LORE_REPORT_ID)

    assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW
    assert state.semantic_ready_key is None
    assert state.to_checkpoint()["status"] == "LORE_FINAL_REVIEW"
    with pytest.raises(ChapterProductionValidationError):
        state.begin_archive_update()

    ready = revision_ready()
    assert ready.status is ChapterProductionStatus.REVISION_READY
    assert ready.current_node == "REVISION_READY"
    assert ready.semantic_ready_key == (RUN_ID, VERSION_1, POLICY)
    assert ready.editor_report_id == EDITOR_REPORT_ID
    assert ready.chief_editor_report_id == CHIEF_REPORT_ID
    assert ready.lore_report_id == LORE_REPORT_ID
    assert ready.begin_archive_update().complete().status is ChapterProductionStatus.COMPLETED


def test_policy_can_skip_chief_but_never_editor_or_lore() -> None:
    state = review_passed(
        editor_review(chief_required=False), ChapterReviewStage.EDITOR, EDITOR_REPORT_ID
    )
    assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW
    pre_ready = review_passed(state, ChapterReviewStage.LORE, LORE_REPORT_ID)
    assert pre_ready.status is ChapterProductionStatus.LORE_FINAL_REVIEW
    ready = revision_ready(chief_required=False)
    assert ready.status is ChapterProductionStatus.REVISION_READY
    assert ready.chief_editor_report_id is None


def test_finalize_revision_ready_fails_closed_for_stale_or_invalid_live_facts() -> None:
    state = review_passed(editor_review(), ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
    state = review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
    pre_ready = review_passed(state, ChapterReviewStage.LORE, LORE_REPORT_ID)
    editor = binding(pre_ready, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
    chief = binding(pre_ready, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
    lore = binding(pre_ready, ChapterReviewStage.LORE, LORE_REPORT_ID)
    baseline = {
        "document_id": DOCUMENT_ID,
        "current_document_version_id": VERSION_1,
        "version_content_hash": HASH_1,
        "editor_report": editor,
        "chief_editor_report": chief,
        "lore_report": lore,
    }
    corruptions = (
        {"current_document_version_id": VERSION_2},
        {"version_content_hash": HASH_2},
        {
            "lore_report": binding(
                pre_ready,
                ChapterReviewStage.LORE,
                LORE_REPORT_ID,
                document_version_id=VERSION_2,
            )
        },
        {
            "lore_report": binding(
                pre_ready,
                ChapterReviewStage.LORE,
                LORE_REPORT_ID,
                passed=False,
            )
        },
    )
    for corruption in corruptions:
        with pytest.raises(ChapterProductionValidationError):
            pre_ready.finalize_revision_ready(**(baseline | corruption))
        assert pre_ready.status is ChapterProductionStatus.LORE_FINAL_REVIEW
        assert pre_ready.to_checkpoint()["status"] == "LORE_FINAL_REVIEW"

    with pytest.raises(ChapterProductionValidationError):
        replace(
            pre_ready,
            status=ChapterProductionStatus.REVISION_READY,
            current_node="REVISION_READY",
        )


@pytest.mark.parametrize(
    ("stage", "report_id"),
    [
        (ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
        (ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID),
        (ChapterReviewStage.LORE, LORE_REPORT_ID),
    ],
)
def test_every_review_stage_rejects_cross_version_or_cross_document_reports(
    stage: ChapterReviewStage, report_id: str
) -> None:
    state = editor_review()
    if stage is not ChapterReviewStage.EDITOR:
        state = review_passed(state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
    if stage is ChapterReviewStage.LORE:
        state = review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)

    for document_id, version_id in ((str.replace(DOCUMENT_ID, "5", "6", 1), VERSION_1), (DOCUMENT_ID, VERSION_2)):
        with pytest.raises(ChapterProductionValidationError):
            state.record_review(
                outcome=ChapterReviewOutcome.PASSED,
                review=binding(
                    state,
                    stage,
                    report_id,
                    document_id=document_id,
                    document_version_id=version_id,
                ),
            )


def test_blocking_review_enters_review_revision_and_cannot_be_overridden() -> None:
    waiting = editor_review().record_review(
        outcome=ChapterReviewOutcome.BLOCKING,
        review=binding(
            editor_review(), ChapterReviewStage.EDITOR, EDITOR_REPORT_ID, passed=False
        ),
        action=action(ChapterActionKind.REVIEW_REVISION),
    )
    assert waiting.status is ChapterProductionStatus.REVIEW_REVISION
    assert waiting.action_kind is ChapterActionKind.REVIEW_REVISION
    with pytest.raises(ChapterProductionValidationError):
        waiting.resolve_action(
            action=action(ChapterActionKind.REVIEW_REVISION),
            decision=ChapterActionDecision.ACCEPT_WARNING,
        )

    revising = waiting.resolve_action(
        action=action(ChapterActionKind.REVIEW_REVISION),
        decision=ChapterActionDecision.REQUEST_REVISION,
    )
    assert revising.status is ChapterProductionStatus.REVIEW_REVISION
    assert not revising.awaiting_user
    next_review = revising.submit_review_revision(
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_2,
        content_hash=HASH_2,
    )
    assert next_review.status is ChapterProductionStatus.EDITOR_REVIEW
    assert next_review.document_version_id == VERSION_2
    assert next_review.editor_report_id is None


@pytest.mark.parametrize(
    "review",
    [
        binding(
            editor_review(),
            ChapterReviewStage.EDITOR,
            EDITOR_REPORT_ID,
            workflow_run_id=OTHER_ACTION_ID,
        ),
        binding(
            editor_review(),
            ChapterReviewStage.EDITOR,
            EDITOR_REPORT_ID,
            chapter_id=OTHER_ACTION_ID,
        ),
        binding(
            editor_review(),
            ChapterReviewStage.EDITOR,
            EDITOR_REPORT_ID,
            document_id=CHAPTER_ID,
        ),
        binding(
            editor_review(),
            ChapterReviewStage.EDITOR,
            EDITOR_REPORT_ID,
            document_version_id=VERSION_2,
        ),
    ],
)
def test_record_review_rejects_cross_scope_live_bindings(
    review: ChapterReviewBinding,
) -> None:
    with pytest.raises(ChapterProductionValidationError):
        editor_review().record_review(
            outcome=ChapterReviewOutcome.PASSED,
            review=review,
        )


@pytest.mark.parametrize(
    ("outcome", "passed"),
    [
        (ChapterReviewOutcome.PASSED, False),
        (ChapterReviewOutcome.WARNING, False),
        (ChapterReviewOutcome.BLOCKING, True),
    ],
)
def test_record_review_rejects_report_passed_outcome_mismatches(
    outcome: ChapterReviewOutcome, passed: bool
) -> None:
    with pytest.raises(ChapterProductionValidationError):
        editor_review().record_review(
            outcome=outcome,
            review=binding(
                editor_review(),
                ChapterReviewStage.EDITOR,
                EDITOR_REPORT_ID,
                passed=passed,
            ),
            action=(
                action(
                    ChapterActionKind.REVIEW_REVISION
                    if outcome is ChapterReviewOutcome.BLOCKING
                    else ChapterActionKind.REVIEW_WARNING
                )
                if outcome is not ChapterReviewOutcome.PASSED
                else None
            ),
        )


def test_pending_action_blocks_every_review_stage_and_outcome() -> None:
    states: list[ChapterProductionState] = []
    for stage, report_id in (
        (ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
        (ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID),
        (ChapterReviewStage.LORE, LORE_REPORT_ID),
    ):
        state = editor_review()
        if stage is not ChapterReviewStage.EDITOR:
            state = review_passed(state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
        if stage is ChapterReviewStage.LORE:
            state = review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
        states.append(
            state.record_review(
                outcome=ChapterReviewOutcome.WARNING,
                review=binding(state, stage, report_id),
                action=action(ChapterActionKind.REVIEW_WARNING),
            )
        )

    for waiting, attempted_stage, outcome in product(
        states, ChapterReviewStage, ChapterReviewOutcome
    ):
        before = waiting.to_checkpoint()
        with pytest.raises(ChapterProductionValidationError):
            waiting.record_review(
                outcome=outcome,
                review=binding(
                    waiting,
                    attempted_stage,
                    OTHER_ACTION_ID,
                    passed=outcome is not ChapterReviewOutcome.BLOCKING,
                ),
                action=(
                    action(
                        ChapterActionKind.REVIEW_REVISION
                        if outcome is ChapterReviewOutcome.BLOCKING
                        else ChapterActionKind.REVIEW_WARNING,
                        action_id=OTHER_ACTION_ID,
                    )
                    if outcome is not ChapterReviewOutcome.PASSED
                    else None
                ),
            )
        assert waiting.to_checkpoint() == before


@pytest.mark.parametrize(
    ("stage", "report_id"),
    [
        (ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
        (ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID),
        (ChapterReviewStage.LORE, LORE_REPORT_ID),
    ],
)
def test_warning_requires_one_live_action_and_explicit_acceptance(
    stage: ChapterReviewStage, report_id: str
) -> None:
    state = editor_review()
    if stage is not ChapterReviewStage.EDITOR:
        state = review_passed(state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
    if stage is ChapterReviewStage.LORE:
        state = review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
    waiting = state.record_review(
        outcome=ChapterReviewOutcome.WARNING,
        review=binding(state, stage, report_id),
        action=action(ChapterActionKind.REVIEW_WARNING),
    )
    assert waiting.awaiting_user
    assert waiting.action_kind is ChapterActionKind.REVIEW_WARNING

    accepted = waiting.resolve_action(
        action=action(ChapterActionKind.REVIEW_WARNING),
        decision=ChapterActionDecision.ACCEPT_WARNING,
    )
    expected = {
        ChapterReviewStage.EDITOR: ChapterProductionStatus.CHIEF_FINAL_REVIEW,
        ChapterReviewStage.CHIEF_EDITOR: ChapterProductionStatus.LORE_FINAL_REVIEW,
        ChapterReviewStage.LORE: ChapterProductionStatus.LORE_FINAL_REVIEW,
    }
    assert accepted.status is expected[stage]


def test_author_actions_cover_accept_revision_manual_edit_and_cancel() -> None:
    waiting = author_gate()
    assert waiting.action_kind is ChapterActionKind.AUTHOR_REVISION
    assert waiting.resolve_action(
        action=action(ChapterActionKind.AUTHOR_REVISION),
        decision=ChapterActionDecision.ACCEPT,
    ).status is ChapterProductionStatus.EDITOR_REVIEW
    assert waiting.resolve_action(
        action=action(ChapterActionKind.AUTHOR_REVISION),
        decision=ChapterActionDecision.REQUEST_REVISION,
    ).status is ChapterProductionStatus.DRAFTING
    assert waiting.resolve_action(
        action=action(ChapterActionKind.AUTHOR_REVISION),
        decision=ChapterActionDecision.CANCEL,
    ).status is ChapterProductionStatus.CANCELLED

    edited = waiting.resolve_action(
        action=action(ChapterActionKind.AUTHOR_REVISION),
        decision=ChapterActionDecision.SUBMIT_MANUAL_EDIT,
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_2,
        content_hash=HASH_2,
    )
    assert edited.status is ChapterProductionStatus.EDITOR_REVIEW
    assert edited.document_version_id == VERSION_2


def test_action_binding_rejects_wrong_request_type_even_when_other_fields_look_safe() -> None:
    with pytest.raises(ChapterProductionValidationError):
        action(
            ChapterActionKind.AUTHOR_REVISION,
            request_type="chapter_review_warning",
        )


@pytest.mark.parametrize(
    "candidate",
    [
        action(ChapterActionKind.AUTHOR_REVISION, workflow_run_id=OTHER_ACTION_ID),
        action(ChapterActionKind.AUTHOR_REVISION, chapter_id=OTHER_ACTION_ID),
        action(ChapterActionKind.AUTHOR_REVISION, pending_count=2),
        action(
            ChapterActionKind.AUTHOR_REVISION,
            status=ActionRequestStatus.APPROVED,
        ),
        action(ChapterActionKind.REVIEW_WARNING),
    ],
)
def test_submit_draft_requires_one_exact_scoped_pending_author_action(
    candidate: ChapterActionBinding,
) -> None:
    with pytest.raises(ChapterProductionValidationError):
        initial().submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=candidate,
        )


@pytest.mark.parametrize(
    "corruption",
    [
        {"document_id": CHAPTER_ID},
        {"document_version_id": VERSION_2},
        {"content_hash": HASH_2},
        {"current_document_id": CHAPTER_ID},
        {"current_document_version_id": VERSION_2},
        {"current_content_hash": HASH_2},
    ],
)
def test_submit_draft_requires_exact_action_target_and_locked_current_binding(
    corruption: dict[str, str],
) -> None:
    with pytest.raises(ChapterProductionValidationError):
        initial().submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=replace(
                action(ChapterActionKind.AUTHOR_REVISION), **corruption
            ),
        )


def test_review_finding_requires_the_exact_scoped_action_kind() -> None:
    state = editor_review()
    for candidate in (
        action(ChapterActionKind.REVIEW_REVISION),
        action(ChapterActionKind.REVIEW_WARNING, workflow_run_id=OTHER_ACTION_ID),
        action(ChapterActionKind.REVIEW_WARNING, chapter_id=OTHER_ACTION_ID),
        action(ChapterActionKind.REVIEW_WARNING, pending_count=2),
        action(
            ChapterActionKind.REVIEW_WARNING,
            status=ActionRequestStatus.APPROVED,
        ),
    ):
        with pytest.raises(ChapterProductionValidationError):
            state.record_review(
                outcome=ChapterReviewOutcome.WARNING,
                review=binding(state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
                action=candidate,
            )
    with pytest.raises(ChapterProductionValidationError):
        state.record_review(
            outcome=ChapterReviewOutcome.BLOCKING,
            review=binding(
                state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID, passed=False
            ),
            action=action(ChapterActionKind.REVIEW_WARNING),
        )


def test_author_requested_revision_requires_a_new_version_of_the_same_document() -> None:
    revising = author_gate().resolve_action(
        action=action(ChapterActionKind.AUTHOR_REVISION),
        decision=ChapterActionDecision.REQUEST_REVISION,
    )
    for document_id, version_id in ((DOCUMENT_ID, VERSION_1), (CHAPTER_ID, VERSION_2)):
        with pytest.raises(ChapterProductionValidationError):
            revising.submit_draft(
                document_id=document_id,
                document_version_id=version_id,
                content_hash=HASH_2,
                action=action(ChapterActionKind.AUTHOR_REVISION),
            )
    updated = revising.submit_draft(
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_2,
        content_hash=HASH_2,
        action=action(
            ChapterActionKind.AUTHOR_REVISION,
            document_version_id=VERSION_2,
            content_hash=HASH_2,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
    )
    assert updated.status is ChapterProductionStatus.AUTHOR_REVISION
    assert updated.document_version_id == VERSION_2


def test_cancel_rejects_irrelevant_version_fields() -> None:
    with pytest.raises(ChapterProductionValidationError):
        author_gate().resolve_action(
            action=action(ChapterActionKind.AUTHOR_REVISION),
            decision=ChapterActionDecision.CANCEL,
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_2,
            content_hash=HASH_2,
        )


@pytest.mark.parametrize(
    ("live_action_id", "action_status"),
    [
        (OTHER_ACTION_ID, ActionRequestStatus.PENDING),
        (ACTION_ID, ActionRequestStatus.APPROVED),
        (ACTION_ID, ActionRequestStatus.REVISED),
        (ACTION_ID, ActionRequestStatus.CANCELLED),
    ],
)
def test_stale_or_replayed_actions_fail_closed(
    live_action_id: str, action_status: ActionRequestStatus
) -> None:
    with pytest.raises(ChapterProductionValidationError):
        author_gate().resolve_action(
            action=action(
                ChapterActionKind.AUTHOR_REVISION,
                action_id=live_action_id,
                status=action_status,
            ),
            decision=ChapterActionDecision.ACCEPT,
        )


@pytest.mark.parametrize(
    "candidate",
    [
        action(ChapterActionKind.AUTHOR_REVISION, workflow_run_id=OTHER_ACTION_ID),
        action(ChapterActionKind.AUTHOR_REVISION, chapter_id=OTHER_ACTION_ID),
        action(ChapterActionKind.AUTHOR_REVISION, pending_count=2),
        action(ChapterActionKind.REVIEW_WARNING),
    ],
)
def test_resolve_action_rejects_foreign_wrong_kind_or_duplicate_pending_binding(
    candidate: ChapterActionBinding,
) -> None:
    with pytest.raises(ChapterProductionValidationError):
        author_gate().resolve_action(
            action=candidate,
            decision=ChapterActionDecision.ACCEPT,
        )


@pytest.mark.parametrize(
    ("gate_factory", "kind", "decision"),
    [
        pytest.param(
            author_gate,
            ChapterActionKind.AUTHOR_REVISION,
            ChapterActionDecision.ACCEPT,
            id="author-accept",
        ),
        pytest.param(
            author_gate,
            ChapterActionKind.AUTHOR_REVISION,
            ChapterActionDecision.CANCEL,
            id="author-cancel",
        ),
        pytest.param(
            author_gate,
            ChapterActionKind.AUTHOR_REVISION,
            ChapterActionDecision.REQUEST_REVISION,
            id="author-request-revision",
        ),
        pytest.param(
            author_gate,
            ChapterActionKind.AUTHOR_REVISION,
            ChapterActionDecision.SUBMIT_MANUAL_EDIT,
            id="author-manual-edit",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            ChapterActionDecision.ACCEPT_WARNING,
            id="warning-accept",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            ChapterActionDecision.CANCEL,
            id="warning-cancel",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            ChapterActionDecision.REQUEST_REVISION,
            id="warning-request-revision",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            ChapterActionDecision.SUBMIT_MANUAL_EDIT,
            id="warning-manual-edit",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            ChapterActionKind.REVIEW_REVISION,
            ChapterActionDecision.CANCEL,
            id="blocking-cancel",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            ChapterActionKind.REVIEW_REVISION,
            ChapterActionDecision.REQUEST_REVISION,
            id="blocking-request-revision",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            ChapterActionKind.REVIEW_REVISION,
            ChapterActionDecision.SUBMIT_MANUAL_EDIT,
            id="blocking-manual-edit",
        ),
    ],
)
def test_canonical_version_change_makes_every_pending_action_decision_stale(
    gate_factory: object,
    kind: ChapterActionKind,
    decision: ChapterActionDecision,
) -> None:
    state = gate_factory()  # type: ignore[operator]
    stale_action = action(
        kind,
        current_document_version_id=VERSION_2,
        current_content_hash=HASH_2,
    )
    kwargs = (
        {
            "document_id": DOCUMENT_ID,
            "document_version_id": VERSION_2,
            "content_hash": HASH_2,
        }
        if decision is ChapterActionDecision.SUBMIT_MANUAL_EDIT
        else {}
    )
    with pytest.raises(ChapterProductionValidationError, match="stale"):
        state.resolve_action(action=stale_action, decision=decision, **kwargs)
    assert state.awaiting_user
    assert state.action_request_id == ACTION_ID


@pytest.mark.parametrize(
    "corruption",
    [
        {"document_id": CHAPTER_ID},
        {"document_version_id": VERSION_2},
        {"content_hash": HASH_2},
        {"current_document_id": CHAPTER_ID},
        {"current_document_version_id": VERSION_2},
        {"current_content_hash": HASH_2},
    ],
)
def test_action_target_and_locked_current_binding_must_both_match_exactly(
    corruption: dict[str, str],
) -> None:
    state = author_gate()
    with pytest.raises(ChapterProductionValidationError):
        state.resolve_action(
            action=replace(action(ChapterActionKind.AUTHOR_REVISION), **corruption),
            decision=ChapterActionDecision.CANCEL,
        )


@pytest.mark.parametrize(
    "outcome",
    [ChapterReviewOutcome.WARNING, ChapterReviewOutcome.BLOCKING],
)
def test_review_findings_reject_stale_canonical_action_bindings(
    outcome: ChapterReviewOutcome,
) -> None:
    state = editor_review()
    kind = (
        ChapterActionKind.REVIEW_WARNING
        if outcome is ChapterReviewOutcome.WARNING
        else ChapterActionKind.REVIEW_REVISION
    )
    with pytest.raises(ChapterProductionValidationError, match="stale"):
        state.record_review(
            outcome=outcome,
            review=binding(
                state,
                ChapterReviewStage.EDITOR,
                EDITOR_REPORT_ID,
                passed=outcome is ChapterReviewOutcome.WARNING,
            ),
            action=action(
                kind,
                current_document_version_id=VERSION_2,
                current_content_hash=HASH_2,
            ),
        )
    assert state.status is ChapterProductionStatus.EDITOR_REVIEW
    assert not state.awaiting_user


@pytest.mark.parametrize(
    ("gate_factory", "kind"),
    [
        pytest.param(
            author_gate, ChapterActionKind.AUTHOR_REVISION, id="author-action"
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.CHIEF_EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            id="warning-action-with-reports",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.LORE, ChapterReviewOutcome.BLOCKING
            ),
            ChapterActionKind.REVIEW_REVISION,
            id="blocking-action-with-reports",
        ),
    ],
)
def test_stale_action_reconciliation_adopts_current_version_and_clears_gate_and_reports(
    gate_factory: object, kind: ChapterActionKind
) -> None:
    state = gate_factory()  # type: ignore[operator]
    reconciled = state.reconcile_stale_action(
        action=action(
            kind,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        )
    )
    assert reconciled.status is ChapterProductionStatus.EDITOR_REVIEW
    assert reconciled.document_id == DOCUMENT_ID
    assert reconciled.document_version_id == VERSION_2
    assert reconciled.content_hash == HASH_2
    assert not reconciled.awaiting_user
    assert reconciled.action_request_id is None
    assert reconciled.action_kind is None
    assert reconciled.editor_report_id is None
    assert reconciled.chief_editor_report_id is None
    assert reconciled.lore_report_id is None
    assert ChapterProductionState.from_checkpoint(reconciled.to_checkpoint()) == reconciled
    with pytest.raises(ChapterProductionValidationError):
        reconciled.reconcile_stale_action(
            action=action(
                kind,
                current_document_version_id=VERSION_2,
                current_content_hash=HASH_2,
            )
        )


@pytest.mark.parametrize(
    "candidate",
    [
        action(
            ChapterActionKind.AUTHOR_REVISION,
            action_id=OTHER_ACTION_ID,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
        action(
            ChapterActionKind.AUTHOR_REVISION,
            workflow_run_id=OTHER_ACTION_ID,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
        action(
            ChapterActionKind.AUTHOR_REVISION,
            chapter_id=OTHER_ACTION_ID,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
        action(
            ChapterActionKind.REVIEW_WARNING,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
        action(
            ChapterActionKind.AUTHOR_REVISION,
            status=ActionRequestStatus.APPROVED,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
        action(
            ChapterActionKind.AUTHOR_REVISION,
            pending_count=2,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
        action(
            ChapterActionKind.AUTHOR_REVISION,
            document_version_id=VERSION_2,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
        action(
            ChapterActionKind.AUTHOR_REVISION,
            content_hash=HASH_2,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
        action(
            ChapterActionKind.AUTHOR_REVISION,
            current_document_id=CHAPTER_ID,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        ),
        action(ChapterActionKind.AUTHOR_REVISION),
        action(
            ChapterActionKind.AUTHOR_REVISION,
            current_document_version_id=VERSION_2,
        ),
    ],
)
def test_stale_action_reconciliation_rejects_nonexact_or_unresolved_proof(
    candidate: ChapterActionBinding,
) -> None:
    state = author_gate()
    with pytest.raises(ChapterProductionValidationError):
        state.reconcile_stale_action(action=candidate)
    assert state.awaiting_user
    assert state.action_request_id == ACTION_ID


def test_new_version_invalidates_readiness_and_every_live_report() -> None:
    ready = revision_ready()
    restarted = ready.invalidate_for_new_version(
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_2,
        content_hash=HASH_2,
    )
    assert restarted.status is ChapterProductionStatus.EDITOR_REVIEW
    assert restarted.semantic_ready_key is None
    assert restarted.editor_report_id is None
    assert restarted.chief_editor_report_id is None
    assert restarted.lore_report_id is None


def test_new_version_must_be_a_new_version_of_the_same_canonical_document() -> None:
    ready = revision_ready()
    for document_id, version_id in ((DOCUMENT_ID, VERSION_1), (CHAPTER_ID, VERSION_2)):
        with pytest.raises(ChapterProductionValidationError):
            ready.invalidate_for_new_version(
                document_id=document_id,
                document_version_id=version_id,
                content_hash=HASH_2,
            )


def test_failure_recovery_is_explicit_safe_and_cannot_capture_raw_errors() -> None:
    state = editor_review()
    failed = state.fail(ChapterFailureCode.PROVIDER_UNAVAILABLE)
    assert failed.status is ChapterProductionStatus.FAILED
    assert failed.failure_code is ChapterFailureCode.PROVIDER_UNAVAILABLE
    assert failed.failed_from_status is ChapterProductionStatus.EDITOR_REVIEW
    assert failed.recover() == state
    assert failed.to_checkpoint()["failure_code"] == "provider_unavailable"
    assert failed.to_public_event_payload()["failure_code"] == "provider_unavailable"

    for unsafe in (
        "provider_unavailable",
        "secret_prompt_leak",
        "provider_key_exposed",
        "prompt_snapshot",
    ):
        with pytest.raises(ChapterProductionValidationError):
            state.fail(unsafe)  # type: ignore[arg-type]
    with pytest.raises(ChapterProductionValidationError):
        author_gate().fail(ChapterFailureCode.PROVIDER_UNAVAILABLE)

    payload = failed.to_checkpoint()
    payload["failure_code"] = "secret_prompt_leak"
    with pytest.raises(ChapterProductionValidationError):
        ChapterProductionState.from_checkpoint(payload)


@pytest.mark.parametrize(
    ("failure_code", "recoverable"),
    [
        pytest.param(ChapterFailureCode.PROVIDER_UNAVAILABLE, True, id="provider-unavailable"),
        pytest.param(ChapterFailureCode.PROVIDER_TIMEOUT, True, id="provider-timeout"),
        pytest.param(
            ChapterFailureCode.INVALID_PROVIDER_OUTPUT,
            True,
            id="invalid-provider-output",
        ),
        pytest.param(ChapterFailureCode.ARCHIVE_UNAVAILABLE, True, id="archive-unavailable"),
        pytest.param(
            ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE,
            False,
            id="document-commit-indeterminate",
        ),
        pytest.param(
            ChapterFailureCode.PERSISTENCE_UNAVAILABLE,
            False,
            id="persistence-unavailable",
        ),
        pytest.param(
            ChapterFailureCode.RECONCILIATION_REQUIRED,
            False,
            id="reconciliation-required",
        ),
    ],
)
def test_failure_recovery_matrix_requires_explicit_reconciliation_for_unsafe_codes(
    failure_code: ChapterFailureCode, recoverable: bool
) -> None:
    original = editor_review()
    failed = original.fail(failure_code)
    restored = ChapterProductionState.from_checkpoint(failed.to_checkpoint())
    assert restored == failed
    if recoverable:
        assert restored.recover() == original
    else:
        with pytest.raises(ChapterProductionValidationError, match="reconciliation"):
            restored.recover()
        assert restored.status is ChapterProductionStatus.FAILED
        assert restored.failure_code is failure_code


def test_finalized_reconciliation_failure_stays_failed_after_safe_live_restore() -> None:
    failed = revision_ready().fail(ChapterFailureCode.RECONCILIATION_REQUIRED)
    with pytest.raises(ChapterProductionValidationError):
        ChapterProductionState.from_checkpoint(failed.to_checkpoint())
    restored = restore_finalized_checkpoint(failed)
    assert restored == failed
    with pytest.raises(ChapterProductionValidationError, match="reconciliation"):
        restored.recover()


@pytest.mark.parametrize(
    "failure_code",
    [
        ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE,
        ChapterFailureCode.PERSISTENCE_UNAVAILABLE,
        ChapterFailureCode.RECONCILIATION_REQUIRED,
    ],
)
def test_typed_no_write_reconciliation_restores_each_failed_original_phase(
    failure_code: ChapterFailureCode,
) -> None:
    original = editor_review()
    failed = original.fail(failure_code)
    reconciled = failed.reconcile_failure(
        binding=failure_reconciliation(
            failed,
            ChapterFailureReconciliationOutcome.NO_WRITE_OR_PERSISTENCE_RESTORED,
        )
    )
    assert reconciled == original


@pytest.mark.parametrize(
    "failure_code",
    [
        ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE,
        ChapterFailureCode.PERSISTENCE_UNAVAILABLE,
        ChapterFailureCode.RECONCILIATION_REQUIRED,
    ],
)
def test_typed_committed_version_reconciliation_adopts_new_canonical_version(
    failure_code: ChapterFailureCode,
) -> None:
    failed = review_gate(ChapterReviewStage.LORE).fail(failure_code)
    reconciled = failed.reconcile_failure(
        binding=failure_reconciliation(
            failed,
            ChapterFailureReconciliationOutcome.CANONICAL_VERSION_COMMITTED,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        )
    )
    assert reconciled.status is ChapterProductionStatus.EDITOR_REVIEW
    assert reconciled.document_version_id == VERSION_2
    assert reconciled.content_hash == HASH_2
    assert reconciled.editor_report_id is None
    assert reconciled.chief_editor_report_id is None
    assert reconciled.lore_report_id is None
    assert reconciled.failed_from_status is None
    assert reconciled.failure_code is None


@pytest.mark.parametrize(
    "failure_code",
    [
        ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE,
        ChapterFailureCode.PERSISTENCE_UNAVAILABLE,
        ChapterFailureCode.RECONCILIATION_REQUIRED,
    ],
)
def test_finalized_no_write_reconciliation_requires_complete_live_readiness(
    failure_code: ChapterFailureCode,
) -> None:
    ready = revision_ready()
    failed = ready.fail(failure_code)
    restored = restore_finalized_checkpoint(failed)
    proof = failure_reconciliation(
        restored,
        ChapterFailureReconciliationOutcome.NO_WRITE_OR_PERSISTENCE_RESTORED,
    )
    with pytest.raises(ChapterProductionValidationError):
        restored.reconcile_failure(binding=proof)
    reconciled = restored.reconcile_failure(
        binding=proof,
        editor_report=binding(restored, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
        chief_editor_report=binding(
            restored, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID
        ),
        lore_report=binding(restored, ChapterReviewStage.LORE, LORE_REPORT_ID),
    )
    assert reconciled == ready


def test_failure_reconciliation_rejects_untyped_missing_foreign_or_wrong_version_proof() -> None:
    failed = editor_review().fail(ChapterFailureCode.RECONCILIATION_REQUIRED)
    with pytest.raises(ChapterProductionValidationError):
        failed.reconcile_failure(binding=None)  # type: ignore[arg-type]
    for corruption in (
        {"workflow_run_id": OTHER_ACTION_ID},
        {"chapter_id": OTHER_ACTION_ID},
        {"document_id": CHAPTER_ID},
        {"failure_code": ChapterFailureCode.PERSISTENCE_UNAVAILABLE},
        {"current_document_version_id": VERSION_2},
        {"current_content_hash": HASH_2},
    ):
        proof = failure_reconciliation(
            failed,
            ChapterFailureReconciliationOutcome.NO_WRITE_OR_PERSISTENCE_RESTORED,
        )
        with pytest.raises(ChapterProductionValidationError):
            failed.reconcile_failure(binding=replace(proof, **corruption))
    with pytest.raises(ChapterProductionValidationError):
        replace(
            failure_reconciliation(
                failed,
                ChapterFailureReconciliationOutcome.NO_WRITE_OR_PERSISTENCE_RESTORED,
            ),
            outcome="no_write_or_persistence_restored",
        )
    with pytest.raises(ChapterProductionValidationError):
        replace(
            failure_reconciliation(
                failed,
                ChapterFailureReconciliationOutcome.NO_WRITE_OR_PERSISTENCE_RESTORED,
            ),
            outcome=True,
        )
    provider_failure = editor_review().fail(ChapterFailureCode.PROVIDER_UNAVAILABLE)
    with pytest.raises(ChapterProductionValidationError):
        provider_failure.reconcile_failure(
            binding=ChapterFailureReconciliationBinding(
                workflow_run_id=RUN_ID,
                chapter_id=CHAPTER_ID,
                failure_code=ChapterFailureCode.PROVIDER_UNAVAILABLE,
                outcome=(
                    ChapterFailureReconciliationOutcome.NO_WRITE_OR_PERSISTENCE_RESTORED
                ),
                document_id=DOCUMENT_ID,
                current_document_version_id=VERSION_1,
                current_content_hash=HASH_1,
            )
        )


def test_failed_checkpoint_retains_every_reference_required_by_its_recovery_target() -> None:
    failed_ready = revision_ready().fail(ChapterFailureCode.ARCHIVE_UNAVAILABLE)
    with pytest.raises(ChapterProductionValidationError):
        replace(failed_ready, lore_report_id=None)


def test_terminal_states_reject_every_stateful_operation() -> None:
    ready = revision_ready()
    terminals = (
        ready.begin_archive_update().complete(),
        author_gate().resolve_action(
            action=action(ChapterActionKind.AUTHOR_REVISION),
            decision=ChapterActionDecision.CANCEL,
        ),
    )
    for state in terminals:
        assert state.is_terminal
        for operation in (
            lambda: state.submit_draft(
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_2,
                content_hash=HASH_2,
                action=action(ChapterActionKind.AUTHOR_REVISION),
            ),
            lambda: state.begin_archive_update(),
            lambda: state.fail(ChapterFailureCode.PROVIDER_UNAVAILABLE),
            lambda: state.invalidate_for_new_version(
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_2,
                content_hash=HASH_2,
            ),
        ):
            with pytest.raises(ChapterProductionValidationError):
                operation()


def test_terminal_states_cannot_leave_through_any_public_transition() -> None:
    terminals = (
        revision_ready().begin_archive_update().complete(),
        author_gate().resolve_action(
            action=action(ChapterActionKind.AUTHOR_REVISION),
            decision=ChapterActionDecision.CANCEL,
        ),
    )
    for state in terminals:
        operations = (
            lambda: state.submit_draft(
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_2,
                content_hash=HASH_2,
                action=action(ChapterActionKind.AUTHOR_REVISION),
            ),
            lambda: state.record_review(
                outcome=ChapterReviewOutcome.PASSED,
                review=binding(state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
            ),
            lambda: state.resolve_action(
                action=action(ChapterActionKind.AUTHOR_REVISION),
                decision=ChapterActionDecision.CANCEL,
            ),
            lambda: state.submit_review_revision(
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_2,
                content_hash=HASH_2,
            ),
            lambda: state.invalidate_for_new_version(
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_2,
                content_hash=HASH_2,
            ),
            lambda: state.begin_archive_update(),
            lambda: state.complete(),
            lambda: state.fail(ChapterFailureCode.PROVIDER_UNAVAILABLE),
            lambda: state.recover(),
            lambda: state.finalize_revision_ready(
                document_id=DOCUMENT_ID,
                current_document_version_id=VERSION_1,
                version_content_hash=HASH_1,
                editor_report=binding(
                    state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID
                ),
                chief_editor_report=binding(
                    state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID
                ),
                lore_report=binding(state, ChapterReviewStage.LORE, LORE_REPORT_ID),
            ),
        )
        for operation in operations:
            with pytest.raises(ChapterProductionValidationError):
                operation()


def test_unlisted_review_transitions_fail_closed() -> None:
    states = {
        ChapterProductionStatus.DRAFTING: initial(),
        ChapterProductionStatus.AUTHOR_REVISION: author_gate(),
        ChapterProductionStatus.EDITOR_REVIEW: editor_review(),
        ChapterProductionStatus.CHIEF_FINAL_REVIEW: review_passed(
            editor_review(), ChapterReviewStage.EDITOR, EDITOR_REPORT_ID
        ),
        ChapterProductionStatus.LORE_FINAL_REVIEW: review_passed(
            review_passed(editor_review(), ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
            ChapterReviewStage.CHIEF_EDITOR,
            CHIEF_REPORT_ID,
        ),
    }
    allowed = {
        (ChapterProductionStatus.EDITOR_REVIEW, ChapterReviewStage.EDITOR),
        (ChapterProductionStatus.CHIEF_FINAL_REVIEW, ChapterReviewStage.CHIEF_EDITOR),
        (ChapterProductionStatus.LORE_FINAL_REVIEW, ChapterReviewStage.LORE),
    }
    for status, stage in product(states, ChapterReviewStage):
        if (status, stage) in allowed:
            continue
        with pytest.raises(ChapterProductionValidationError):
            states[status].record_review(
                outcome=ChapterReviewOutcome.PASSED,
                review=binding(states[status], stage, EDITOR_REPORT_ID),
            )


@pytest.mark.parametrize(
    ("case", "state_factory"),
    [
        pytest.param("drafting", initial, id="drafting"),
        pytest.param(
            "drafting-with-version",
            lambda: author_gate().resolve_action(
                action=action(ChapterActionKind.AUTHOR_REVISION),
                decision=ChapterActionDecision.REQUEST_REVISION,
            ),
            id="drafting-with-version",
        ),
        pytest.param("author-pending", author_gate, id="author-pending"),
        pytest.param("editor", editor_review, id="editor"),
        pytest.param(
            "editor-warning-pending",
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            id="editor-warning-pending",
        ),
        pytest.param(
            "review-revision-pending",
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            id="review-revision-pending",
        ),
        pytest.param(
            "review-revision-resolved",
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ).resolve_action(
                action=action(ChapterActionKind.REVIEW_REVISION),
                decision=ChapterActionDecision.REQUEST_REVISION,
            ),
            id="review-revision-resolved",
        ),
        pytest.param(
            "chief",
            lambda: review_gate(ChapterReviewStage.CHIEF_EDITOR),
            id="chief",
        ),
        pytest.param(
            "chief-warning-pending",
            lambda: finding_gate(
                ChapterReviewStage.CHIEF_EDITOR, ChapterReviewOutcome.WARNING
            ),
            id="chief-warning-pending",
        ),
        pytest.param(
            "lore",
            lambda: review_gate(ChapterReviewStage.LORE),
            id="lore",
        ),
        pytest.param("lore-report-complete", lore_pre_ready, id="lore-report-complete"),
        pytest.param(
            "lore-warning-pending",
            lambda: finding_gate(
                ChapterReviewStage.LORE, ChapterReviewOutcome.WARNING
            ),
            id="lore-warning-pending",
        ),
        pytest.param(
            "cancelled",
            lambda: author_gate().resolve_action(
                action=action(ChapterActionKind.AUTHOR_REVISION),
                decision=ChapterActionDecision.CANCEL,
            ),
            id="cancelled",
        ),
        pytest.param(
            "ordinary-failed",
            lambda: review_gate(ChapterReviewStage.LORE).fail(
                ChapterFailureCode.PROVIDER_TIMEOUT
            ),
            id="ordinary-failed",
        ),
    ],
)
def test_every_non_finalized_checkpoint_round_trips_exactly(
    case: str, state_factory: object
) -> None:
    state = state_factory()  # type: ignore[operator]
    restored = ChapterProductionState.from_checkpoint(state.to_checkpoint())
    assert restored == state, case
    assert restored.to_checkpoint() == state.to_checkpoint()


@pytest.mark.parametrize(
    "state_factory",
    [
        pytest.param(revision_ready, id="ready"),
        pytest.param(lambda: revision_ready().begin_archive_update(), id="archive"),
        pytest.param(
            lambda: revision_ready().begin_archive_update().complete(), id="completed"
        ),
        pytest.param(
            lambda: revision_ready().fail(ChapterFailureCode.ARCHIVE_UNAVAILABLE),
            id="failed-from-ready",
        ),
        pytest.param(
            lambda: revision_ready()
            .begin_archive_update()
            .fail(ChapterFailureCode.ARCHIVE_UNAVAILABLE),
            id="failed-from-archive",
        ),
    ],
)
def test_finalized_checkpoint_requires_live_restore_and_round_trips_exactly(
    state_factory: object,
) -> None:
    state = state_factory()  # type: ignore[operator]
    with pytest.raises(ChapterProductionValidationError):
        ChapterProductionState.from_checkpoint(state.to_checkpoint())
    restored = restore_finalized_checkpoint(state)
    assert restored == state
    assert restored.to_checkpoint() == state.to_checkpoint()


@pytest.mark.parametrize(
    ("gate_factory", "kind", "decision", "expected_status"),
    [
        pytest.param(
            author_gate,
            ChapterActionKind.AUTHOR_REVISION,
            ChapterActionDecision.ACCEPT,
            ChapterProductionStatus.EDITOR_REVIEW,
            id="author-accept",
        ),
        pytest.param(
            author_gate,
            ChapterActionKind.AUTHOR_REVISION,
            ChapterActionDecision.REQUEST_REVISION,
            ChapterProductionStatus.DRAFTING,
            id="author-request-revision",
        ),
        pytest.param(
            author_gate,
            ChapterActionKind.AUTHOR_REVISION,
            ChapterActionDecision.SUBMIT_MANUAL_EDIT,
            ChapterProductionStatus.EDITOR_REVIEW,
            id="author-manual-edit",
        ),
        pytest.param(
            author_gate,
            ChapterActionKind.AUTHOR_REVISION,
            ChapterActionDecision.ACCEPT_WARNING,
            None,
            id="author-reject-accept-warning",
        ),
        pytest.param(
            author_gate,
            ChapterActionKind.AUTHOR_REVISION,
            ChapterActionDecision.CANCEL,
            ChapterProductionStatus.CANCELLED,
            id="author-cancel",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            ChapterActionDecision.ACCEPT,
            None,
            id="warning-reject-accept",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            ChapterActionDecision.REQUEST_REVISION,
            ChapterProductionStatus.REVIEW_REVISION,
            id="warning-request-revision",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            ChapterActionDecision.SUBMIT_MANUAL_EDIT,
            ChapterProductionStatus.EDITOR_REVIEW,
            id="warning-manual-edit",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            ChapterActionDecision.ACCEPT_WARNING,
            ChapterProductionStatus.CHIEF_FINAL_REVIEW,
            id="warning-accept-warning",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            ChapterActionKind.REVIEW_WARNING,
            ChapterActionDecision.CANCEL,
            ChapterProductionStatus.CANCELLED,
            id="warning-cancel",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            ChapterActionKind.REVIEW_REVISION,
            ChapterActionDecision.ACCEPT,
            None,
            id="blocking-reject-accept",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            ChapterActionKind.REVIEW_REVISION,
            ChapterActionDecision.REQUEST_REVISION,
            ChapterProductionStatus.REVIEW_REVISION,
            id="blocking-request-revision",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            ChapterActionKind.REVIEW_REVISION,
            ChapterActionDecision.SUBMIT_MANUAL_EDIT,
            ChapterProductionStatus.EDITOR_REVIEW,
            id="blocking-manual-edit",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            ChapterActionKind.REVIEW_REVISION,
            ChapterActionDecision.ACCEPT_WARNING,
            None,
            id="blocking-reject-accept-warning",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            ChapterActionKind.REVIEW_REVISION,
            ChapterActionDecision.CANCEL,
            ChapterProductionStatus.CANCELLED,
            id="blocking-cancel",
        ),
    ],
)
def test_action_decision_transition_matrix(
    gate_factory: object,
    kind: ChapterActionKind,
    decision: ChapterActionDecision,
    expected_status: ChapterProductionStatus | None,
) -> None:
    state = gate_factory()  # type: ignore[operator]
    kwargs = (
        {
            "document_id": DOCUMENT_ID,
            "document_version_id": VERSION_2,
            "content_hash": HASH_2,
        }
        if decision is ChapterActionDecision.SUBMIT_MANUAL_EDIT
        else {}
    )
    if expected_status is None:
        with pytest.raises(ChapterProductionValidationError):
            state.resolve_action(action=action(kind), decision=decision, **kwargs)
        return
    resolved = state.resolve_action(action=action(kind), decision=decision, **kwargs)
    assert resolved.status is expected_status
    assert not resolved.awaiting_user
    if decision is ChapterActionDecision.SUBMIT_MANUAL_EDIT:
        assert resolved.document_version_id == VERSION_2
        assert resolved.content_hash == HASH_2


@pytest.mark.parametrize(
    ("stage", "expected_status"),
    [
        pytest.param(
            ChapterReviewStage.EDITOR,
            ChapterProductionStatus.CHIEF_FINAL_REVIEW,
            id="editor-warning",
        ),
        pytest.param(
            ChapterReviewStage.CHIEF_EDITOR,
            ChapterProductionStatus.LORE_FINAL_REVIEW,
            id="chief-warning",
        ),
        pytest.param(
            ChapterReviewStage.LORE,
            ChapterProductionStatus.LORE_FINAL_REVIEW,
            id="lore-warning",
        ),
    ],
)
def test_accept_warning_advances_each_review_stage_only_to_its_legal_successor(
    stage: ChapterReviewStage, expected_status: ChapterProductionStatus
) -> None:
    state = finding_gate(stage, ChapterReviewOutcome.WARNING)
    resolved = state.resolve_action(
        action=action(ChapterActionKind.REVIEW_WARNING),
        decision=ChapterActionDecision.ACCEPT_WARNING,
    )
    assert resolved.status is expected_status
    assert not resolved.awaiting_user


@pytest.mark.parametrize(
    "state_factory",
    [
        pytest.param(editor_review, id="editor"),
        pytest.param(
            lambda: review_gate(ChapterReviewStage.CHIEF_EDITOR), id="chief"
        ),
        pytest.param(lambda: review_gate(ChapterReviewStage.LORE), id="lore"),
        pytest.param(lore_pre_ready, id="lore-report-complete"),
        pytest.param(revision_ready, id="ready"),
        pytest.param(lambda: revision_ready().begin_archive_update(), id="archive"),
    ],
)
def test_every_allowed_new_version_source_invalidates_all_review_readiness(
    state_factory: object,
) -> None:
    state = state_factory()  # type: ignore[operator]
    restarted = state.invalidate_for_new_version(
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_2,
        content_hash=HASH_2,
    )
    assert restarted.status is ChapterProductionStatus.EDITOR_REVIEW
    assert restarted.document_version_id == VERSION_2
    assert restarted.content_hash == HASH_2
    assert restarted.editor_report_id is None
    assert restarted.chief_editor_report_id is None
    assert restarted.lore_report_id is None
    assert restarted.semantic_ready_key is None


@pytest.mark.parametrize(
    "state_factory",
    [
        pytest.param(initial, id="drafting"),
        pytest.param(author_gate, id="author-pending"),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            id="warning-pending",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            id="blocking-pending",
        ),
        pytest.param(
            lambda: author_gate().resolve_action(
                action=action(ChapterActionKind.AUTHOR_REVISION),
                decision=ChapterActionDecision.CANCEL,
            ),
            id="cancelled",
        ),
        pytest.param(
            lambda: revision_ready().begin_archive_update().complete(), id="completed"
        ),
    ],
)
def test_disallowed_new_version_sources_fail_closed(state_factory: object) -> None:
    state = state_factory()  # type: ignore[operator]
    with pytest.raises(ChapterProductionValidationError):
        state.invalidate_for_new_version(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_2,
            content_hash=HASH_2,
        )


@pytest.mark.parametrize(
    "state_factory",
    [
        pytest.param(initial, id="drafting"),
        pytest.param(editor_review, id="editor"),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ).resolve_action(
                action=action(ChapterActionKind.REVIEW_REVISION),
                decision=ChapterActionDecision.REQUEST_REVISION,
            ),
            id="review-revision",
        ),
        pytest.param(
            lambda: review_gate(ChapterReviewStage.CHIEF_EDITOR), id="chief"
        ),
        pytest.param(lambda: review_gate(ChapterReviewStage.LORE), id="lore"),
        pytest.param(lore_pre_ready, id="lore-report-complete"),
        pytest.param(revision_ready, id="ready"),
        pytest.param(lambda: revision_ready().begin_archive_update(), id="archive"),
    ],
)
def test_every_failure_eligible_nonterminal_state_recovers_exactly(
    state_factory: object,
) -> None:
    state = state_factory()  # type: ignore[operator]
    failed = state.fail(ChapterFailureCode.PROVIDER_UNAVAILABLE)
    if state.status in {
        ChapterProductionStatus.REVISION_READY,
        ChapterProductionStatus.ARCHIVE_UPDATE,
    }:
        with pytest.raises(ChapterProductionValidationError):
            ChapterProductionState.from_checkpoint(failed.to_checkpoint())
        restored_failed = restore_finalized_checkpoint(failed)
    else:
        restored_failed = ChapterProductionState.from_checkpoint(failed.to_checkpoint())
    assert restored_failed == failed
    assert restored_failed.recover() == state


@pytest.mark.parametrize(
    "state_factory",
    [
        pytest.param(author_gate, id="author-pending"),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.WARNING
            ),
            id="warning-pending",
        ),
        pytest.param(
            lambda: finding_gate(
                ChapterReviewStage.EDITOR, ChapterReviewOutcome.BLOCKING
            ),
            id="blocking-pending",
        ),
    ],
)
def test_failure_cannot_hide_any_pending_user_action(state_factory: object) -> None:
    state = state_factory()  # type: ignore[operator]
    with pytest.raises(ChapterProductionValidationError):
        state.fail(ChapterFailureCode.PROVIDER_UNAVAILABLE)


def test_checkpoint_round_trip_is_exact_versioned_and_content_free() -> None:
    state = revision_ready()
    payload = state.to_checkpoint()
    assert payload == {
        "version": 2,
        "chapter_workflow_run_id": RUN_ID,
        "chapter_id": CHAPTER_ID,
        "status": "REVISION_READY",
        "current_node": "REVISION_READY",
        "awaiting_user": False,
        "review_policy_version": POLICY,
        "chief_editor_required": True,
        "document_id": DOCUMENT_ID,
        "document_version_id": VERSION_1,
        "content_hash": HASH_1,
        "editor_report_id": EDITOR_REPORT_ID,
        "chief_editor_report_id": CHIEF_REPORT_ID,
        "lore_report_id": LORE_REPORT_ID,
        "action_request_id": None,
        "action_kind": None,
        "failed_from_status": None,
        "failure_code": None,
    }
    with pytest.raises(ChapterProductionValidationError):
        ChapterProductionState.from_checkpoint(payload)
    assert ChapterProductionState.from_revision_ready_checkpoint(
        payload,
        workflow_run_id=RUN_ID,
        chapter_id=CHAPTER_ID,
        run_workflow_type="chapter_production",
        run_status="REVISION_READY",
        run_current_node="REVISION_READY",
        run_awaiting_user=False,
        checkpoint_workflow_run_id=RUN_ID,
        checkpoint_node_name="REVISION_READY",
        document_id=DOCUMENT_ID,
        current_document_version_id=VERSION_1,
        version_content_hash=HASH_1,
        editor_report=binding(state, ChapterReviewStage.EDITOR, EDITOR_REPORT_ID),
        chief_editor_report=binding(
            state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID
        ),
        lore_report=binding(state, ChapterReviewStage.LORE, LORE_REPORT_ID),
    ) == state
    serialized = str(payload).lower()
    for forbidden in ("chapter prose", "prompt", "feedback", "raw_report", "credential", "endpoint"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("chapter_id"),
        lambda payload: payload.update({"novel_text": "secret"}),
        lambda payload: payload.update({"version": 1}),
        lambda payload: payload.update({"version": True}),
        lambda payload: payload.update({"status": "completed"}),
        lambda payload: payload.update({"current_node": "lore_final_review"}),
        lambda payload: payload.update({"awaiting_user": 0}),
        lambda payload: payload.update({"document_version_id": VERSION_1.upper()}),
        lambda payload: payload.update({"content_hash": "A" * 64}),
        lambda payload: payload.update({"review_policy_version": "client selected policy"}),
        lambda payload: payload.update({"lore_report_id": None}),
    ],
)
def test_checkpoint_rejects_unknown_shapes_types_legacy_values_and_incomplete_readiness(
    mutate: object,
) -> None:
    payload = revision_ready().to_checkpoint()
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ChapterProductionValidationError):
        ChapterProductionState.from_checkpoint(payload)


def test_ready_state_cannot_be_constructed_without_all_policy_required_bindings() -> None:
    ready = revision_ready()
    for field in (
        "document_id",
        "document_version_id",
        "content_hash",
        "editor_report_id",
        "chief_editor_report_id",
        "lore_report_id",
    ):
        with pytest.raises(ChapterProductionValidationError):
            replace(ready, **{field: None})


def test_pre_ready_checkpoint_cannot_be_relabelled_and_reloaded_as_ready() -> None:
    state = review_passed(editor_review(), ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
    state = review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
    pre_ready = review_passed(state, ChapterReviewStage.LORE, LORE_REPORT_ID)
    for status, node in (
        ("REVISION_READY", "REVISION_READY"),
        ("ARCHIVE_UPDATE", "archive_update"),
        ("COMPLETED", "completed"),
    ):
        forged = pre_ready.to_checkpoint()
        forged["status"] = status
        forged["current_node"] = node
        with pytest.raises(ChapterProductionValidationError):
            ChapterProductionState.from_checkpoint(forged)


def test_pre_ready_checkpoint_cannot_reach_ready_through_failure_recovery() -> None:
    state = review_passed(editor_review(), ChapterReviewStage.EDITOR, EDITOR_REPORT_ID)
    state = review_passed(state, ChapterReviewStage.CHIEF_EDITOR, CHIEF_REPORT_ID)
    pre_ready = review_passed(state, ChapterReviewStage.LORE, LORE_REPORT_ID)

    with pytest.raises(ChapterProductionValidationError):
        replace(
            pre_ready,
            status=ChapterProductionStatus.FAILED,
            current_node="failed",
            failed_from_status=ChapterProductionStatus.REVISION_READY,
            failure_code=ChapterFailureCode.RECONCILIATION_REQUIRED,
        )

    forged = pre_ready.to_checkpoint()
    forged.update(
        status="FAILED",
        current_node="failed",
        failed_from_status="REVISION_READY",
        failure_code="reconciliation_required",
    )
    with pytest.raises(ChapterProductionValidationError):
        ChapterProductionState.from_checkpoint(forged)


def test_persisted_run_and_checkpoint_discriminators_must_match_exactly() -> None:
    ready = revision_ready()
    ready.validate_persistence_binding(
        workflow_run_id=RUN_ID,
        chapter_id=CHAPTER_ID,
        run_workflow_type="chapter_production",
        run_status="REVISION_READY",
        run_current_node="REVISION_READY",
        run_awaiting_user=False,
        checkpoint_workflow_run_id=RUN_ID,
        checkpoint_node_name="REVISION_READY",
    )
    corruptions = (
        {"workflow_run_id": OTHER_ACTION_ID},
        {"chapter_id": OTHER_ACTION_ID},
        {"run_workflow_type": "project_creation"},
        {"run_status": "COMPLETED"},
        {"run_current_node": "lore_final_review"},
        {"run_awaiting_user": True},
        {"checkpoint_workflow_run_id": OTHER_ACTION_ID},
        {"checkpoint_node_name": "revision_ready"},
    )
    baseline = {
        "workflow_run_id": RUN_ID,
        "chapter_id": CHAPTER_ID,
        "run_workflow_type": "chapter_production",
        "run_status": "REVISION_READY",
        "run_current_node": "REVISION_READY",
        "run_awaiting_user": False,
        "checkpoint_workflow_run_id": RUN_ID,
        "checkpoint_node_name": "REVISION_READY",
    }
    for corruption in corruptions:
        with pytest.raises(ChapterProductionValidationError):
            ready.validate_persistence_binding(**(baseline | corruption))


def test_ready_state_revalidates_live_version_hash_roles_modes_and_reports() -> None:
    ready = revision_ready()

    def binding(
        report_id: str, stage: ChapterReviewStage, *, version_id: str = VERSION_1
    ) -> ChapterReviewBinding:
        mode, role = {
            ChapterReviewStage.EDITOR: ("chapter_editor", "editor_agent"),
            ChapterReviewStage.CHIEF_EDITOR: (
                "chapter_chief_final",
                "chief_editor_agent",
            ),
            ChapterReviewStage.LORE: ("chapter_final_lore", "lore_agent"),
        }[stage]
        return ChapterReviewBinding(
            report_id=report_id,
            stage=stage,
            workflow_run_id=RUN_ID,
            chapter_id=CHAPTER_ID,
            document_id=DOCUMENT_ID,
            document_version_id=version_id,
            review_mode=mode,
            reviewer_agent_role=role,
            passed=True,
        )

    editor = binding(EDITOR_REPORT_ID, ChapterReviewStage.EDITOR)
    chief = binding(CHIEF_REPORT_ID, ChapterReviewStage.CHIEF_EDITOR)
    lore = binding(LORE_REPORT_ID, ChapterReviewStage.LORE)
    ready.validate_live_readiness(
        document_id=DOCUMENT_ID,
        current_document_version_id=VERSION_1,
        version_content_hash=HASH_1,
        editor_report=editor,
        chief_editor_report=chief,
        lore_report=lore,
    )

    with pytest.raises(ChapterProductionValidationError):
        ready.validate_live_readiness(
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_2,
            version_content_hash=HASH_1,
            editor_report=editor,
            chief_editor_report=chief,
            lore_report=lore,
        )
    with pytest.raises(ChapterProductionValidationError):
        ready.validate_live_readiness(
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_1,
            version_content_hash=HASH_1,
            editor_report=editor,
            chief_editor_report=chief,
            lore_report=binding(LORE_REPORT_ID, ChapterReviewStage.LORE, version_id=VERSION_2),
        )


def test_state_is_frozen_and_public_projection_is_a_fixed_safe_allowlist() -> None:
    state = author_gate()
    with pytest.raises(FrozenInstanceError):
        state.status = ChapterProductionStatus.COMPLETED  # type: ignore[misc]
    assert state.to_public_event_payload() == {
        "status": "AUTHOR_REVISION",
        "current_node": "author_revision",
        "awaiting_user": True,
        "chapter_id": CHAPTER_ID,
        "document_version_id": VERSION_1,
        "action_request_id": ACTION_ID,
        "action_kind": "author_revision",
    }


@pytest.mark.parametrize(
    ("status", "current_node", "awaiting_user"),
    [
        ("awaiting_approval", "approval", True),
        ("completed", "approval", False),
        ("rejected", "approval", False),
    ],
)
def test_legacy_v08_runs_remain_readable_but_never_become_v2_ready(
    status: str, current_node: str, awaiting_user: bool
) -> None:
    legacy = LegacyChapterProductionSnapshot.from_run_projection(
        workflow_run_id=RUN_ID,
        chapter_id=CHAPTER_ID,
        status=status,
        current_node=current_node,
        awaiting_user=awaiting_user,
    )
    assert legacy.status == status
    assert not legacy.is_revision_ready
    with pytest.raises(ChapterProductionValidationError):
        ChapterProductionState.from_checkpoint(
            {
                "version": 1,
                "status": status,
                "current_node": current_node,
                "awaiting_user": awaiting_user,
            }
        )


def test_legacy_projection_rejects_unknown_or_inconsistent_shapes() -> None:
    for status, node, waiting in (
        ("completed", "REVISION_READY", False),
        ("awaiting_approval", "approval", False),
        ("REVISION_READY", "REVISION_READY", False),
    ):
        with pytest.raises(ChapterProductionValidationError):
            LegacyChapterProductionSnapshot.from_run_projection(
                workflow_run_id=RUN_ID,
                chapter_id=CHAPTER_ID,
                status=status,
                current_node=node,
                awaiting_user=waiting,
            )
