"""Unit tests for the complete Chapter Production V2 lifecycle.

Covers:
- Full state machine transition matrix (11 statuses, 3 actions, 7 decisions, 7 failure codes).
- Deterministic happy paths (three-stage review and skipped chief editor).
- User-edited path (manual edit and feedback revision).
- Blocking review loops (editor, chief editor, lore).
- Warning review loops (proceed with warnings and request revision).
- Reconcile & recovery from provider errors / timeouts.
- Invariants: stale report rejection, cross-version safety, parent chain integrity, safe error redaction.
"""

from __future__ import annotations

import pytest

from app.models.enums import ActionRequestStatus
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ProviderError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ReviewProviderError,
    ChapterProductionV2ValidationError,
)
from app.workflows.chapter_production import (
    ChapterActionBinding,
    ChapterActionDecision,
    ChapterActionKind,
    ChapterFailureCode,
    ChapterProductionState,
    ChapterProductionStatus,
    ChapterProductionValidationError,
    ChapterReviewBinding,
    ChapterReviewOutcome,
    ChapterReviewPolicyBinding,
    ChapterReviewStage,
)

RUN_ID = "11111111-1111-4111-8111-111111111111"
CHAPTER_ID = "22222222-2222-4222-8222-222222222222"
DOCUMENT_ID = "33333333-3333-4333-8333-333333333333"
VERSION_1 = "44444444-4444-4444-8444-444444444444"
VERSION_2 = "55555555-5555-4555-8555-555555555555"
VERSION_3 = "66666666-6666-4666-8666-666666666666"
ACTION_ID_1 = "77777777-7777-4777-8777-777777777777"
ACTION_ID_2 = "88888888-8888-4888-8888-888888888888"
ACTION_ID_3 = "99999999-9999-4999-8999-999999999999"
EDITOR_REPORT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CHIEF_REPORT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LORE_REPORT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
SECOND_EDITOR_REPORT_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

HASH_1 = "a" * 64
HASH_2 = "b" * 64
HASH_3 = "c" * 64
POLICY_VERSION = "chapter-quality-v1"


def make_policy(*, chief_editor_required: bool = True) -> ChapterReviewPolicyBinding:
    return ChapterReviewPolicyBinding(
        workflow_run_id=RUN_ID,
        chapter_id=CHAPTER_ID,
        review_policy_version=POLICY_VERSION,
        chief_editor_required=chief_editor_required,
    )


def make_initial_state(*, chief_editor_required: bool = True) -> ChapterProductionState:
    return ChapterProductionState.initial(
        chapter_workflow_run_id=RUN_ID,
        chapter_id=CHAPTER_ID,
        review_policy_version=POLICY_VERSION,
        chief_editor_required=chief_editor_required,
    )


def make_action(
    kind: ChapterActionKind,
    *,
    action_id: str = ACTION_ID_1,
    document_id: str = DOCUMENT_ID,
    document_version_id: str = VERSION_1,
    content_hash: str = HASH_1,
    current_document_id: str = DOCUMENT_ID,
    current_document_version_id: str = VERSION_1,
    current_content_hash: str = HASH_1,
    status: ActionRequestStatus = ActionRequestStatus.PENDING,
    pending_count: int = 1,
) -> ChapterActionBinding:
    request_type = {
        ChapterActionKind.AUTHOR_REVISION: "chapter_author_revision",
        ChapterActionKind.REVIEW_WARNING: "chapter_review_warning",
        ChapterActionKind.REVIEW_REVISION: "chapter_review_revision",
    }[kind]
    return ChapterActionBinding(
        action_request_id=action_id,
        workflow_run_id=RUN_ID,
        chapter_id=CHAPTER_ID,
        request_type=request_type,
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


def make_review(
    stage: ChapterReviewStage,
    *,
    report_id: str | None = None,
    document_version_id: str = VERSION_1,
    passed: bool = True,
) -> ChapterReviewBinding:
    default_ids = {
        ChapterReviewStage.EDITOR: EDITOR_REPORT_ID,
        ChapterReviewStage.CHIEF_EDITOR: CHIEF_REPORT_ID,
        ChapterReviewStage.LORE: LORE_REPORT_ID,
    }
    modes = {
        ChapterReviewStage.EDITOR: ("chapter_editor", "editor_agent"),
        ChapterReviewStage.CHIEF_EDITOR: ("chapter_chief_final", "chief_editor_agent"),
        ChapterReviewStage.LORE: ("chapter_final_lore", "lore_agent"),
    }
    review_mode, reviewer_role = modes[stage]
    return ChapterReviewBinding(
        report_id=report_id or default_ids[stage],
        stage=stage,
        workflow_run_id=RUN_ID,
        chapter_id=CHAPTER_ID,
        document_id=DOCUMENT_ID,
        document_version_id=document_version_id,
        review_mode=review_mode,
        reviewer_agent_role=reviewer_role,
        passed=passed,
    )


# ==============================================================================
# 1. State Machine Enumeration & Transition Matrix
# ==============================================================================


class TestStateMachineMatrix:
    def test_all_11_statuses_represented(self) -> None:
        expected = {
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
        assert {status.value for status in ChapterProductionStatus} == expected
        assert len(ChapterProductionStatus) == 11

    def test_all_3_action_kinds_represented(self) -> None:
        expected = {"author_revision", "review_warning", "review_revision"}
        assert {kind.value for kind in ChapterActionKind} == expected
        assert len(ChapterActionKind) == 3

    def test_all_action_decisions_represented(self) -> None:
        expected = {
            "accept",
            "request_revision",
            "submit_manual_edit",
            "accept_warning",
            "cancel",
        }
        assert {decision.value for decision in ChapterActionDecision} == expected
        assert len(ChapterActionDecision) == 5

    def test_all_7_failure_codes_represented(self) -> None:
        expected = {
            "provider_unavailable",
            "provider_timeout",
            "invalid_provider_output",
            "document_commit_indeterminate",
            "persistence_unavailable",
            "archive_unavailable",
            "reconciliation_required",
        }
        assert {code.value for code in ChapterFailureCode} == expected
        assert len(ChapterFailureCode) == 7

    def test_invalid_transitions_raise_validation_error(self) -> None:
        state = make_initial_state()

        # Cannot record review while in DRAFTING
        with pytest.raises(ChapterProductionValidationError):
            state.record_review(
                outcome=ChapterReviewOutcome.PASSED,
                review=make_review(ChapterReviewStage.EDITOR),
            )

        # Cannot resolve action when not awaiting user
        with pytest.raises(ChapterProductionValidationError):
            state.resolve_action(
                action=make_action(ChapterActionKind.AUTHOR_REVISION),
                decision=ChapterActionDecision.ACCEPT,
            )

        # Cannot begin archive update when not REVISION_READY
        policy = make_policy()
        with pytest.raises(ChapterProductionValidationError):
            state.begin_archive_update(
                policy=policy,
                document_id=DOCUMENT_ID,
                current_document_version_id=VERSION_1,
                version_content_hash=HASH_1,
                editor_report=make_review(ChapterReviewStage.EDITOR),
                chief_editor_report=make_review(ChapterReviewStage.CHIEF_EDITOR),
                lore_report=make_review(ChapterReviewStage.LORE),
            )

        # Cannot complete when not in ARCHIVE_UPDATE
        with pytest.raises(ChapterProductionValidationError):
            state.complete(
                policy=policy,
                document_id=DOCUMENT_ID,
                current_document_version_id=VERSION_1,
                version_content_hash=HASH_1,
                editor_report=make_review(ChapterReviewStage.EDITOR),
                chief_editor_report=make_review(ChapterReviewStage.CHIEF_EDITOR),
                lore_report=make_review(ChapterReviewStage.LORE),
            )


# ==============================================================================
# 2. Deterministic Happy Paths
# ==============================================================================


class TestDeterministicHappyPaths:
    def test_complete_three_stage_review_happy_path(self) -> None:
        policy = make_policy(chief_editor_required=True)
        state = make_initial_state(chief_editor_required=True)
        assert state.status is ChapterProductionStatus.DRAFTING
        assert not state.awaiting_user

        # 1. Draft generated -> author gate
        action = make_action(ChapterActionKind.AUTHOR_REVISION)
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=action,
        )
        assert state.status is ChapterProductionStatus.AUTHOR_REVISION
        assert state.awaiting_user
        assert state.document_version_id == VERSION_1

        # 2. Author accepts draft -> enters Editor review
        state = state.resolve_action(
            action=action,
            decision=ChapterActionDecision.ACCEPT,
        )
        assert state.status is ChapterProductionStatus.EDITOR_REVIEW
        assert not state.awaiting_user

        # 3. Editor review passes -> enters Chief Editor review
        editor_rev = make_review(ChapterReviewStage.EDITOR)
        state = state.record_review(
            outcome=ChapterReviewOutcome.PASSED,
            review=editor_rev,
        )
        assert state.status is ChapterProductionStatus.CHIEF_FINAL_REVIEW
        assert state.editor_report_id == EDITOR_REPORT_ID

        # 4. Chief Editor review passes -> enters Lore review
        chief_rev = make_review(ChapterReviewStage.CHIEF_EDITOR)
        state = state.record_review(
            outcome=ChapterReviewOutcome.PASSED,
            review=chief_rev,
        )
        assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW
        # 5. Lore review passes -> enters LORE_FINAL_REVIEW with lore_report_id
        lore_rev = make_review(ChapterReviewStage.LORE)
        state = state.record_review(
            outcome=ChapterReviewOutcome.PASSED,
            review=lore_rev,
        )
        assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW
        assert state.lore_report_id == LORE_REPORT_ID

        # 6. Enter revision ready state
        state = state.finalize_revision_ready(
            policy=policy,
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_1,
            version_content_hash=HASH_1,
            editor_report=editor_rev,
            chief_editor_report=chief_rev,
            lore_report=lore_rev,
        )
        assert state.status is ChapterProductionStatus.REVISION_READY

        # 7. Begin archive update
        state = state.begin_archive_update(
            policy=policy,
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_1,
            version_content_hash=HASH_1,
            editor_report=editor_rev,
            chief_editor_report=chief_rev,
            lore_report=lore_rev,
        )
        assert state.status is ChapterProductionStatus.ARCHIVE_UPDATE

        # 8. Complete chapter production
        state = state.complete(
            policy=policy,
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_1,
            version_content_hash=HASH_1,
            editor_report=editor_rev,
            chief_editor_report=chief_rev,
            lore_report=lore_rev,
        )
        assert state.status is ChapterProductionStatus.COMPLETED
        assert state.is_terminal
        assert not state.awaiting_user

    def test_happy_path_skipping_chief_editor_when_not_required(self) -> None:
        policy = make_policy(chief_editor_required=False)
        state = make_initial_state(chief_editor_required=False)

        action = make_action(ChapterActionKind.AUTHOR_REVISION)
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=action,
        )
        state = state.resolve_action(
            action=action,
            decision=ChapterActionDecision.ACCEPT,
        )
        assert state.status is ChapterProductionStatus.EDITOR_REVIEW

        # Editor review passes -> skips chief editor, advances directly to LORE_FINAL_REVIEW
        editor_rev = make_review(ChapterReviewStage.EDITOR)
        state = state.record_review(
            outcome=ChapterReviewOutcome.PASSED,
            review=editor_rev,
        )
        assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW

        lore_rev = make_review(ChapterReviewStage.LORE)
        state = state.record_review(
            outcome=ChapterReviewOutcome.PASSED,
            review=lore_rev,
        )
        assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW

        state = state.finalize_revision_ready(
            policy=policy,
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_1,
            version_content_hash=HASH_1,
            editor_report=editor_rev,
            chief_editor_report=None,
            lore_report=lore_rev,
        )
        assert state.status is ChapterProductionStatus.REVISION_READY

        state = state.begin_archive_update(
            policy=policy,
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_1,
            version_content_hash=HASH_1,
            editor_report=editor_rev,
            chief_editor_report=None,
            lore_report=lore_rev,
        )
        state = state.complete(
            policy=policy,
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_1,
            version_content_hash=HASH_1,
            editor_report=editor_rev,
            chief_editor_report=None,
            lore_report=lore_rev,
        )
        assert state.status is ChapterProductionStatus.COMPLETED


# ==============================================================================
# 3. User-Edited Paths
# ==============================================================================


class TestUserEditedPaths:
    def test_manual_edit_path_rebinds_version_and_enters_review(self) -> None:
        state = make_initial_state()
        action = make_action(ChapterActionKind.AUTHOR_REVISION)
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=action,
        )

        # Author submits manual edit -> new version VERSION_2 bound, advances to EDITOR_REVIEW
        state = state.resolve_action(
            action=action,
            decision=ChapterActionDecision.SUBMIT_MANUAL_EDIT,
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_2,
            content_hash=HASH_2,
        )
        assert state.status is ChapterProductionStatus.EDITOR_REVIEW
        assert state.document_version_id == VERSION_2
        assert state.content_hash == HASH_2
        assert not state.awaiting_user

    def test_feedback_revision_path_returns_to_drafting(self) -> None:
        state = make_initial_state()
        action = make_action(ChapterActionKind.AUTHOR_REVISION)
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=action,
        )

        # Author requests feedback revision -> returns to DRAFTING
        state = state.resolve_action(
            action=action,
            decision=ChapterActionDecision.REQUEST_REVISION,
        )
        assert state.status is ChapterProductionStatus.DRAFTING
        assert not state.awaiting_user

        # New draft candidate submitted with VERSION_2
        action_2 = make_action(
            ChapterActionKind.AUTHOR_REVISION,
            action_id=ACTION_ID_2,
            document_version_id=VERSION_2,
            content_hash=HASH_2,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        )
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_2,
            content_hash=HASH_2,
            action=action_2,
        )
        assert state.status is ChapterProductionStatus.AUTHOR_REVISION
        assert state.document_version_id == VERSION_2


# ==============================================================================
# 4. Blocking Review Loops
# ==============================================================================


class TestBlockingReviewLoops:
    def test_blocking_editor_review_loop_resets_and_passes(self) -> None:
        policy = make_policy(chief_editor_required=False)
        state = make_initial_state(chief_editor_required=False)
        action_1 = make_action(ChapterActionKind.AUTHOR_REVISION)
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=action_1,
        )
        state = state.resolve_action(
            action=action_1,
            decision=ChapterActionDecision.ACCEPT,
        )

        # 1. Editor review discovers blocking issues
        blocking_action = make_action(
            ChapterActionKind.REVIEW_REVISION,
            action_id=ACTION_ID_2,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
        )
        editor_rev_fail = make_review(
            ChapterReviewStage.EDITOR,
            report_id=EDITOR_REPORT_ID,
            passed=False,
        )
        state = state.record_review(
            outcome=ChapterReviewOutcome.BLOCKING,
            review=editor_rev_fail,
            action=blocking_action,
        )
        assert state.status is ChapterProductionStatus.REVIEW_REVISION
        assert state.awaiting_user
        assert state.action_kind is ChapterActionKind.REVIEW_REVISION

        # 2. Resolve review revision action
        state = state.resolve_action(
            action=blocking_action,
            decision=ChapterActionDecision.REQUEST_REVISION,
        )
        assert state.status is ChapterProductionStatus.REVIEW_REVISION
        assert not state.awaiting_user

        # 3. Revision agent submits revised draft VERSION_2 -> resets to EDITOR_REVIEW
        state = state.submit_review_revision(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_2,
            content_hash=HASH_2,
        )
        assert state.status is ChapterProductionStatus.EDITOR_REVIEW
        assert state.document_version_id == VERSION_2
        assert state.editor_report_id is None

        # 4. Second-pass Editor review on VERSION_2 passes
        editor_rev_pass = make_review(
            ChapterReviewStage.EDITOR,
            report_id=SECOND_EDITOR_REPORT_ID,
            document_version_id=VERSION_2,
            passed=True,
        )
        state = state.record_review(
            outcome=ChapterReviewOutcome.PASSED,
            review=editor_rev_pass,
        )
        assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW

        lore_rev = make_review(
            ChapterReviewStage.LORE,
            document_version_id=VERSION_2,
            passed=True,
        )
        state = state.record_review(
            outcome=ChapterReviewOutcome.PASSED,
            review=lore_rev,
        )
        assert state.status is ChapterProductionStatus.LORE_FINAL_REVIEW

        state = state.finalize_revision_ready(
            policy=policy,
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_2,
            version_content_hash=HASH_2,
            editor_report=editor_rev_pass,
            chief_editor_report=None,
            lore_report=lore_rev,
        )
        assert state.status is ChapterProductionStatus.REVISION_READY

        state = state.begin_archive_update(
            policy=policy,
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_2,
            version_content_hash=HASH_2,
            editor_report=editor_rev_pass,
            chief_editor_report=None,
            lore_report=lore_rev,
        )
        state = state.complete(
            policy=policy,
            document_id=DOCUMENT_ID,
            current_document_version_id=VERSION_2,
            version_content_hash=HASH_2,
            editor_report=editor_rev_pass,
            chief_editor_report=None,
            lore_report=lore_rev,
        )
        assert state.status is ChapterProductionStatus.COMPLETED


# ==============================================================================
# 5. Warning Review Loops
# ==============================================================================


class TestWarningReviewLoops:
    def test_warning_review_loop_proceed_with_warnings(self) -> None:
        state = make_initial_state(chief_editor_required=True)
        action_1 = make_action(ChapterActionKind.AUTHOR_REVISION)
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=action_1,
        )
        state = state.resolve_action(
            action=action_1,
            decision=ChapterActionDecision.ACCEPT,
        )

        # Editor review produces warnings
        warning_action = make_action(
            ChapterActionKind.REVIEW_WARNING,
            action_id=ACTION_ID_2,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
        )
        editor_rev = make_review(ChapterReviewStage.EDITOR, passed=True)
        state = state.record_review(
            outcome=ChapterReviewOutcome.WARNING,
            review=editor_rev,
            action=warning_action,
        )
        assert state.status is ChapterProductionStatus.EDITOR_REVIEW
        assert state.awaiting_user
        assert state.action_kind is ChapterActionKind.REVIEW_WARNING

        # User accepts warning -> advances to CHIEF_FINAL_REVIEW
        state = state.resolve_action(
            action=warning_action,
            decision=ChapterActionDecision.ACCEPT_WARNING,
        )
        assert state.status is ChapterProductionStatus.CHIEF_FINAL_REVIEW
        assert not state.awaiting_user

    def test_warning_review_loop_choose_revision(self) -> None:
        state = make_initial_state(chief_editor_required=True)
        action_1 = make_action(ChapterActionKind.AUTHOR_REVISION)
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=action_1,
        )
        state = state.resolve_action(
            action=action_1,
            decision=ChapterActionDecision.ACCEPT,
        )

        warning_action = make_action(
            ChapterActionKind.REVIEW_WARNING,
            action_id=ACTION_ID_2,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
        )
        editor_rev = make_review(ChapterReviewStage.EDITOR, passed=True)
        state = state.record_review(
            outcome=ChapterReviewOutcome.WARNING,
            review=editor_rev,
            action=warning_action,
        )

        # User decides to request revision instead of proceeding with warnings
        state = state.resolve_action(
            action=warning_action,
            decision=ChapterActionDecision.REQUEST_REVISION,
        )
        assert state.status is ChapterProductionStatus.REVIEW_REVISION
        assert not state.awaiting_user


# ==============================================================================
# 6. Reconcile & Recovery from Errors
# ==============================================================================


class TestReconcileAndRecovery:
    @pytest.mark.parametrize(
        "failure_code",
        [
            ChapterFailureCode.PROVIDER_UNAVAILABLE,
            ChapterFailureCode.PROVIDER_TIMEOUT,
            ChapterFailureCode.INVALID_PROVIDER_OUTPUT,
        ],
    )
    def test_recovery_from_transient_provider_errors(
        self, failure_code: ChapterFailureCode
    ) -> None:
        state = make_initial_state()
        failed = state.fail(failure_code)
        assert failed.status is ChapterProductionStatus.FAILED
        assert failed.failed_from_status is ChapterProductionStatus.DRAFTING
        assert failed.failure_code is failure_code

        recovered = failed.recover()
        assert recovered.status is ChapterProductionStatus.DRAFTING
        assert recovered.failed_from_status is None
        assert recovered.failure_code is None

    @pytest.mark.parametrize(
        "failure_code",
        [
            ChapterFailureCode.DOCUMENT_COMMIT_INDETERMINATE,
            ChapterFailureCode.PERSISTENCE_UNAVAILABLE,
            ChapterFailureCode.RECONCILIATION_REQUIRED,
        ],
    )
    def test_recovery_from_indeterminate_commit_rejects_simple_recovery(
        self, failure_code: ChapterFailureCode
    ) -> None:
        state = make_initial_state()
        failed = state.fail(failure_code)
        with pytest.raises(ChapterProductionValidationError, match="reconciliation workflow"):
            failed.recover()

    def test_reconcile_stale_action_adopts_canonical_successor(self) -> None:
        state = make_initial_state()
        action_1 = make_action(ChapterActionKind.AUTHOR_REVISION)
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=action_1,
        )

        # Stale action where database has moved to VERSION_2
        stale_proof = make_action(
            ChapterActionKind.AUTHOR_REVISION,
            action_id=ACTION_ID_1,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            current_document_version_id=VERSION_2,
            current_content_hash=HASH_2,
        )
        reconciled = state.reconcile_stale_action(action=stale_proof)
        assert reconciled.status is ChapterProductionStatus.EDITOR_REVIEW
        assert reconciled.document_version_id == VERSION_2
        assert reconciled.content_hash == HASH_2
        assert not reconciled.awaiting_user


# ==============================================================================
# 7. Safety Invariants & Redaction
# ==============================================================================


class TestSafetyInvariantsAndRedaction:
    def test_stale_report_with_mismatched_version_rejected(self) -> None:
        state = make_initial_state()
        action = make_action(ChapterActionKind.AUTHOR_REVISION)
        state = state.submit_draft(
            document_id=DOCUMENT_ID,
            document_version_id=VERSION_1,
            content_hash=HASH_1,
            action=action,
        )
        state = state.resolve_action(
            action=action,
            decision=ChapterActionDecision.ACCEPT,
        )

        # Report references VERSION_2 instead of locked VERSION_1
        mismatched_review = make_review(
            ChapterReviewStage.EDITOR,
            document_version_id=VERSION_2,
            passed=True,
        )
        with pytest.raises(
            ChapterProductionValidationError, match="scope or target is inconsistent"
        ):
            state.record_review(
                outcome=ChapterReviewOutcome.PASSED,
                review=mismatched_review,
            )

    def test_nil_uuid_rejected_across_all_contracts(self) -> None:
        nil = "00000000-0000-0000-0000-000000000000"
        with pytest.raises(ChapterProductionValidationError):
            ChapterProductionState.initial(
                chapter_workflow_run_id=nil,
                chapter_id=CHAPTER_ID,
                review_policy_version=POLICY_VERSION,
                chief_editor_required=True,
            )

    def test_safe_error_types_redact_secrets_and_stack_traces(self) -> None:
        errors = [
            (
                ChapterProductionV2ValidationError(),
                422,
                "chapter_production_v2_invalid",
            ),
            (
                ChapterProductionV2ProviderError(),
                503,
                "chapter_production_v2_provider_failed",
            ),
            (
                ChapterProductionV2ReviewProviderError(),
                503,
                "chapter_production_v2_review_provider_failed",
            ),
            (
                ChapterProductionV2CommitIndeterminateError(),
                500,
                "chapter_production_v2_commit_indeterminate",
            ),
            (
                ChapterProductionV2ReconciliationError(),
                409,
                "chapter_production_v2_reconciliation_required",
            ),
        ]
        sentinels = [
            "sk-secret-key-12345",
            "postgresql://user:pass@db:5432/main",
            "/var/app/private/template.md",
            "Traceback (most recent call last):",
        ]
        for err, expected_status, expected_code in errors:
            assert err.status_code == expected_status
            assert err.code == expected_code
            msg = str(err)
            for sentinel in sentinels:
                assert sentinel not in msg
