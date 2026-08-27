"""Domain service orchestrating version-bound Reader Panel initialization and cold-reading collection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any
from uuid import UUID, uuid4

from fastapi import status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.reader_panel_agents import build_blind_ballot_request, build_cold_read_request
from app.agents.reader_panel_contracts import (
    ActionableRecommendationItem,
    ConsensusClass as ContractConsensusClass,
    DiscussionNovelty as ContractDiscussionNovelty,
    DiscussionStance as ContractDiscussionStance,
    EditorialDecision as ContractEditorialDecision,
    EvidenceRef,
    ExtractedIssueItem,
    ModeratorDiscussionSummaryOutput,
    ModeratorDiscussionSummaryRequest,
    ModeratorIssueExtractionOutput,
    ModeratorIssueExtractionRequest,
    ModeratorReportSynthesisOutput,
    ModeratorReportSynthesisRequest,
    ReaderBallotOutput,
    ReaderDiscussionTurnOutput,
    ReaderDiscussionTurnRequest,
    ReaderFinalBallotOutput,
    ReaderFinalBallotRequest,
    ReaderInitialReadingOutput,
    validate_reader_panel_text,
)
from app.agents.reader_panel_fakes import (
    DeterministicReaderPanelProvider,
    ReaderPanelFakeScenario,
)
from app.core.errors import AppError
from app.models.core import (
    Chapter,
    Document,
    DocumentVersion,
    Project,
    ReviewReport,
    WorkflowEvent,
    WorkflowRun,
)
from app.models.reader_panel import (
    ReaderInitialReport,
    ReaderPanelBallot,
    ReaderPanelIssue,
    ReaderPanelInvocation,
    ReaderPanelMessage,
    ReaderPanelSession,
    ReaderRun,
)
from app.services.revision_readiness_store import RevisionReadyPair
from app.workflows.reader_panel import (
    BallotVote,
    Confidence,
    EditorHandoffDecision,
    PanelMode,
    ReaderPanelConfig,
    ReaderPanelInvocationPhase,
    ReaderPanelInvocationStatus,
    ReaderPanelSafeError,
    ReaderPanelStatus,
    RiskFlag,
    Severity,
    SuggestedAction,
    classify_issue_consensus,
    get_mode_preset_config,
    is_mode_off,
)
from app.llm.errors import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class ReaderPanelServiceError(AppError):
    """Base error for reader panel service failures."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "reader_panel_service_error"
    default_message = "Reader panel service error."


class ReaderPanelNotFoundError(ReaderPanelServiceError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "reader_panel_not_found"
    default_message = "The requested reader panel session was not found."


class ReaderPanelInvalidStateError(ReaderPanelServiceError):
    status_code = status.HTTP_409_CONFLICT
    code = "reader_panel_invalid_state"
    default_message = "The reader panel session is in an incompatible state."


class ReaderPanelQuorumError(ReaderPanelServiceError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "reader_panel_quorum_failed"
    default_message = "Reader panel failed to collect the required minimum quorum of valid reports."


class ReaderPanelStaleVersionError(ReaderPanelServiceError):
    status_code = status.HTTP_409_CONFLICT
    code = "reader_panel_stale_version"
    default_message = (
        "The manuscript version has been updated since the reader panel session started."
    )


class ReaderPanelBudgetExhaustedError(ReaderPanelServiceError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "reader_panel_budget_exhausted"
    default_message = "The reader panel hard budget is exhausted."


class _ReaderPanelWorkInProgressError(ReaderPanelInvalidStateError):
    pass


class _ReaderPanelWorkAlreadyCompleted(ReaderPanelInvalidStateError):
    pass


class _ReaderPanelPermanentWorkError(ReaderPanelInvalidStateError):
    def __init__(self, error_code: str = ReaderPanelSafeError.UNKNOWN.value) -> None:
        super().__init__()
        self.error_code = error_code


_TERMINAL_PANEL_STATUSES = {
    ReaderPanelStatus.COMPLETED.value,
    ReaderPanelStatus.DEGRADED_COMPLETED.value,
    ReaderPanelStatus.FAILED.value,
    ReaderPanelStatus.CANCELLED.value,
}


@dataclass(frozen=True)
class ReaderPanelSessionResult:
    session_id: UUID | None
    workflow_run_id: UUID | None
    status: str
    mode: str
    project_id: UUID | None = None
    chapter_id: UUID | None = None
    document_id: UUID | None = None
    document_version_id: UUID | None = None
    source_hash: str | None = None
    is_noop: bool = False
    planned_readers: int = 0
    completed_readers: int = 0
    initial_reports_locked: bool = False
    issue_count: int = 0
    initial_ballot_count: int = 0
    initial_ballots_locked: bool = False
    discussion_message_count: int = 0
    discussed_issue_count: int = 0
    final_ballot_count: int = 0
    final_ballots_locked: bool = False
    review_report_id: UUID | None = None
    stale: bool = False
    degradation_reason: str | None = None
    failure_reason: str | None = None
    reports: list[dict] = field(default_factory=list)
    message: str | None = None


class ReaderPanelService:
    """Service managing reader panel lifecycle, immutable version binding, and cold-reading sample collection."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @staticmethod
    def _safe_provider_error(exc: BaseException) -> tuple[str, bool]:
        if isinstance(exc, _ReaderPanelPermanentWorkError):
            return exc.error_code, False
        if isinstance(exc, (ProviderTimeoutError, TimeoutError)):
            return ReaderPanelSafeError.TIMEOUT.value, True
        if isinstance(exc, ProviderRateLimitedError):
            return ReaderPanelSafeError.RATE_LIMITED.value, True
        if isinstance(exc, ProviderUnavailableError):
            return ReaderPanelSafeError.UNAVAILABLE.value, True
        if isinstance(exc, (ProviderInvalidOutputError, TypeError, ValueError)):
            return ReaderPanelSafeError.INVALID_OUTPUT.value, True
        if isinstance(exc, ProviderConfigurationError):
            return ReaderPanelSafeError.CONFIGURATION.value, False
        if isinstance(exc, ReaderPanelBudgetExhaustedError):
            return ReaderPanelSafeError.BUDGET_EXHAUSTED.value, False
        return ReaderPanelSafeError.UNKNOWN.value, False

    async def _locked_session(self, session_id: UUID) -> ReaderPanelSession:
        stmt = (
            select(ReaderPanelSession)
            .where(ReaderPanelSession.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        session = (await self._db.execute(stmt)).scalars().first()
        if session is None:
            raise ReaderPanelNotFoundError()
        await self._refresh_stale_flag(session)
        return session

    async def _refresh_stale_flag(self, session: ReaderPanelSession) -> None:
        if session.document_id is not None:
            document = await self._db.get(Document, session.document_id, populate_existing=True)
            if document is not None and document.current_version_id != session.document_version_id:
                session.stale = True

    async def _settle_terminal(
        self, panel_session: ReaderPanelSession, terminal_status: ReaderPanelStatus, reason: str
    ) -> None:
        now = datetime.now(timezone.utc)
        panel_session.status = terminal_status.value
        panel_session.current_step = terminal_status.value
        panel_session.failure_reason = reason
        if panel_session.completed_at is None:
            panel_session.completed_at = now
        workflow_run = await self._db.get(
            WorkflowRun, panel_session.workflow_run_id, populate_existing=True
        )
        if workflow_run is not None:
            workflow_run.status = terminal_status.value
            workflow_run.current_node = terminal_status.value
            workflow_run.next_node = None
            workflow_run.awaiting_user = False
            if workflow_run.completed_at is None:
                workflow_run.completed_at = panel_session.completed_at

    async def _settle_failed(self, panel_session: ReaderPanelSession, reason: str) -> None:
        await self._settle_terminal(panel_session, ReaderPanelStatus.FAILED, reason)
        await self._terminate_started_invocations(panel_session.id)
        events = (
            (
                await self._db.execute(
                    select(WorkflowEvent).where(
                        WorkflowEvent.workflow_run_id == panel_session.workflow_run_id,
                        WorkflowEvent.event_type == "reader_panel.failed",
                    )
                )
            )
            .scalars()
            .all()
        )
        events = [
            event
            for event in events
            if event.workflow_run_id == panel_session.workflow_run_id
            and event.event_type == "reader_panel.failed"
        ]
        if not events:
            self._db.add(
                WorkflowEvent(
                    workflow_run_id=panel_session.workflow_run_id,
                    event_type="reader_panel.failed",
                    node_name="failed",
                    payload={
                        "session_id": str(panel_session.id),
                        "status": panel_session.status,
                        "reason_code": reason,
                    },
                    event_sequence=None,
                )
            )

    async def _terminate_started_invocations(self, session_id: UUID) -> None:
        invocations = (
            (
                await self._db.execute(
                    select(ReaderPanelInvocation)
                    .where(ReaderPanelInvocation.session_id == session_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        for invocation in invocations:
            if (
                invocation.session_id == session_id
                and invocation.status == ReaderPanelInvocationStatus.STARTED.value
            ):
                invocation.status = ReaderPanelInvocationStatus.CANCELLED.value
                invocation.error_code = ReaderPanelSafeError.CANCELLED.value
                invocation.completed_at = now

    async def _eliminate_reader(
        self,
        panel_session: ReaderPanelSession,
        reader_run_id: UUID,
        error_code: str,
        phase_reason: str,
    ) -> bool:
        run = await self._db.get(ReaderRun, reader_run_id, populate_existing=True)
        if run is None or run.session_id != panel_session.id:
            raise ReaderPanelInvalidStateError()
        run.status = "failed"
        run.error_code = error_code
        run.error_message = None
        runs = (
            (
                await self._db.execute(
                    select(ReaderRun)
                    .where(ReaderRun.session_id == panel_session.id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        completed = sum(
            candidate.session_id == panel_session.id and candidate.status == "completed"
            for candidate in runs
            if candidate.id != run.id
        )
        minimum = self._required_budget(
            self._validate_config_snapshot(panel_session), "min_valid_readers"
        )
        if completed < minimum:
            await self._settle_failed(panel_session, phase_reason)
            await self._db.commit()
            return False
        panel_session.degradation_reason = "reader_sample_degraded"
        await self._db.commit()
        return True

    async def _settle_cancellation(self, panel_session: ReaderPanelSession) -> None:
        await self._settle_terminal(panel_session, ReaderPanelStatus.CANCELLED, "cancelled_by_user")
        await self._terminate_started_invocations(panel_session.id)
        events = (
            (
                await self._db.execute(
                    select(WorkflowEvent).where(
                        WorkflowEvent.workflow_run_id == panel_session.workflow_run_id,
                        WorkflowEvent.event_type == "reader_panel.cancelled",
                    )
                )
            )
            .scalars()
            .all()
        )
        if not any(
            event.workflow_run_id == panel_session.workflow_run_id
            and event.event_type == "reader_panel.cancelled"
            for event in events
        ):
            self._db.add(
                WorkflowEvent(
                    workflow_run_id=panel_session.workflow_run_id,
                    event_type="reader_panel.cancelled",
                    node_name="cancelled",
                    payload={
                        "session_id": str(panel_session.id),
                        "status": panel_session.status,
                        "reason_code": "cancelled_by_user",
                    },
                    event_sequence=None,
                )
            )

    async def _settle_cancelled_attempt(self, session_id: UUID, attempt_id: UUID) -> None:
        panel_session = await self._locked_session(session_id)
        attempt = await self._db.get(ReaderPanelInvocation, attempt_id, populate_existing=True)
        if (
            attempt is not None
            and attempt.session_id == session_id
            and attempt.status == ReaderPanelInvocationStatus.STARTED.value
        ):
            attempt.status = ReaderPanelInvocationStatus.CANCELLED.value
            attempt.error_code = ReaderPanelSafeError.CANCELLED.value
            attempt.completed_at = datetime.now(timezone.utc)
        if panel_session.status not in {
            ReaderPanelStatus.COMPLETED.value,
            ReaderPanelStatus.DEGRADED_COMPLETED.value,
            ReaderPanelStatus.FAILED.value,
        }:
            await self._settle_cancellation(panel_session)
        await self._db.commit()

    @staticmethod
    def _required_budget(snapshot: dict[str, Any], name: str) -> int:
        value = snapshot.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ReaderPanelInvalidStateError()
        return value

    @classmethod
    def _validate_config_snapshot(cls, session: ReaderPanelSession) -> dict[str, Any]:
        snapshot = session.config_snapshot
        required = {
            "mode",
            "reader_count",
            "reader_profile_ids",
            "min_valid_readers",
            "max_ballot_issues",
            "max_discussion_issues",
            "max_rounds_per_issue",
            "max_total_model_calls",
            "max_model_calls_per_phase",
            "max_total_input_tokens",
            "max_total_output_tokens",
            "max_input_tokens_per_call",
            "max_output_tokens_per_call",
            "max_messages",
            "max_provider_attempts",
            "max_invalid_output_repairs",
            "max_execution_seconds",
        }
        if not isinstance(snapshot, dict) or set(snapshot) != required:
            raise ReaderPanelInvalidStateError()
        profiles = snapshot["reader_profile_ids"]
        reader_count = snapshot["reader_count"]
        min_readers = snapshot["min_valid_readers"]
        if (
            snapshot["mode"] != session.mode
            or snapshot["mode"] not in {mode.value for mode in PanelMode if mode != PanelMode.OFF}
            or not isinstance(profiles, list)
            or not all(isinstance(item, str) and item for item in profiles)
            or len(profiles) != len(set(profiles))
            or type(reader_count) is not int
            or reader_count != len(profiles)
            or type(min_readers) is not int
            or not 1 <= min_readers <= reader_count
            or snapshot["max_invalid_output_repairs"] != 1
        ):
            raise ReaderPanelInvalidStateError()
        for name in {
            "reader_count",
            "max_total_model_calls",
            "max_model_calls_per_phase",
            "max_total_input_tokens",
            "max_total_output_tokens",
            "max_input_tokens_per_call",
            "max_output_tokens_per_call",
            "max_messages",
            "max_provider_attempts",
            "max_invalid_output_repairs",
            "max_execution_seconds",
        }:
            cls._required_budget(snapshot, name)
        if any(
            type(snapshot[name]) is not int or snapshot[name] < 0
            for name in {"max_ballot_issues", "max_discussion_issues", "max_rounds_per_issue"}
        ):
            raise ReaderPanelInvalidStateError()
        if (
            snapshot["max_discussion_issues"] > snapshot["max_ballot_issues"]
            or snapshot["max_model_calls_per_phase"] > snapshot["max_total_model_calls"]
            or snapshot["max_input_tokens_per_call"] > snapshot["max_total_input_tokens"]
            or snapshot["max_output_tokens_per_call"] > snapshot["max_total_output_tokens"]
        ):
            raise ReaderPanelInvalidStateError()
        return snapshot

    async def _invoke_provider(
        self,
        *,
        session_id: UUID,
        phase: ReaderPanelInvocationPhase,
        work_key: str,
        request: Any,
        invoke: Any,
        expected_type: type,
        validate: Any = None,
    ) -> Any:
        """Invoke once-per-attempt outside a transaction with durable safe accounting."""
        if not re.fullmatch(r"[A-Za-z0-9:_-]{1,160}", work_key):
            raise ReaderPanelInvalidStateError()
        request_json = (
            request.model_dump_json() if hasattr(request, "model_dump_json") else repr(request)
        )
        # One charged token per UTF-8 byte is a provider-neutral upper bound;
        # persisted accounting is reserved/charged usage, never claimed actual usage.
        input_reservation = max(1, len(request_json.encode("utf-8")))
        active_waits = 0

        while True:
            session = await self._locked_session(session_id)
            if session.status in _TERMINAL_PANEL_STATUSES:
                await self._db.commit()
                raise ReaderPanelInvalidStateError()
            snapshot = self._validate_config_snapshot(session)
            max_attempts = self._required_budget(snapshot, "max_provider_attempts")
            max_repairs = self._required_budget(snapshot, "max_invalid_output_repairs")
            max_calls = self._required_budget(snapshot, "max_total_model_calls")
            max_phase_calls = self._required_budget(snapshot, "max_model_calls_per_phase")
            max_input = self._required_budget(snapshot, "max_total_input_tokens")
            max_output = self._required_budget(snapshot, "max_total_output_tokens")
            max_input_call = self._required_budget(snapshot, "max_input_tokens_per_call")
            output_reservation = self._required_budget(snapshot, "max_output_tokens_per_call")
            max_elapsed = self._required_budget(snapshot, "max_execution_seconds")
            if input_reservation > max_input_call:
                await self._db.commit()
                raise ReaderPanelBudgetExhaustedError()
            if isinstance(session.created_at, datetime):
                created = session.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - created).total_seconds() >= max_elapsed:
                    await self._db.commit()
                    raise ReaderPanelBudgetExhaustedError()

            rows = (
                (
                    await self._db.execute(
                        select(ReaderPanelInvocation)
                        .where(ReaderPanelInvocation.session_id == session_id)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            rows = [row for row in rows if row.session_id == session_id]
            reader_runs = (
                (
                    await self._db.execute(
                        select(ReaderRun)
                        .where(ReaderRun.session_id == session_id)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            reader_runs = [row for row in reader_runs if row.session_id == session_id]
            if len(reader_runs) != snapshot["reader_count"] or {
                run.reader_profile_id for run in reader_runs
            } != set(snapshot["reader_profile_ids"]):
                await self._db.commit()
                raise ReaderPanelInvalidStateError()
            issues = (
                (
                    await self._db.execute(
                        select(ReaderPanelIssue)
                        .where(ReaderPanelIssue.session_id == session_id)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            messages = (
                (
                    await self._db.execute(
                        select(ReaderPanelMessage)
                        .where(ReaderPanelMessage.session_id == session_id)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            if (
                len([item for item in issues if item.session_id == session_id])
                > snapshot["max_ballot_issues"]
                or (
                    phase
                    in {
                        ReaderPanelInvocationPhase.DISCUSSION_TURN,
                        ReaderPanelInvocationPhase.DISCUSSION_SUMMARY,
                    }
                    and len([item for item in messages if item.session_id == session_id])
                    >= snapshot["max_messages"]
                )
                or any(
                    item.session_id == session_id
                    and item.round_number > snapshot["max_rounds_per_issue"]
                    for item in messages
                )
            ):
                await self._db.commit()
                raise ReaderPanelBudgetExhaustedError()
            work_rows = [
                row for row in rows if row.phase == phase.value and row.work_key == work_key
            ]
            if any(row.status == ReaderPanelInvocationStatus.SUCCEEDED.value for row in work_rows):
                await self._db.commit()
                raise _ReaderPanelWorkAlreadyCompleted()
            if any(
                row.status == ReaderPanelInvocationStatus.UNKNOWN_COMMIT.value for row in work_rows
            ):
                await self._db.commit()
                raise _ReaderPanelPermanentWorkError(ReaderPanelSafeError.UNKNOWN_COMMIT.value)
            if any(
                row.error_code
                in {
                    ReaderPanelSafeError.CONFIGURATION.value,
                    ReaderPanelSafeError.UNKNOWN.value,
                }
                for row in work_rows
            ):
                await self._db.commit()
                error_code = next(
                    row.error_code
                    for row in reversed(work_rows)
                    if row.error_code
                    in {
                        ReaderPanelSafeError.CONFIGURATION.value,
                        ReaderPanelSafeError.UNKNOWN.value,
                    }
                )
                raise _ReaderPanelPermanentWorkError(error_code)
            started = next(
                (
                    row
                    for row in work_rows
                    if row.status == ReaderPanelInvocationStatus.STARTED.value
                ),
                None,
            )
            if started is not None:
                if isinstance(started.started_at, datetime):
                    began = started.started_at
                    if began.tzinfo is None:
                        began = began.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - began).total_seconds() < max_elapsed:
                        await self._db.commit()
                        if active_waits >= 10:
                            raise _ReaderPanelWorkInProgressError()
                        active_waits += 1
                        await asyncio.sleep(0.05)
                        continue
                started.status = ReaderPanelInvocationStatus.UNKNOWN_COMMIT.value
                started.error_code = ReaderPanelSafeError.UNKNOWN_COMMIT.value
                started.completed_at = datetime.now(timezone.utc)
                await self._db.commit()
                raise _ReaderPanelPermanentWorkError(ReaderPanelSafeError.UNKNOWN_COMMIT.value)
            if (
                sum(
                    row.error_code == ReaderPanelSafeError.INVALID_OUTPUT.value for row in work_rows
                )
                >= max_repairs + 1
            ):
                await self._db.commit()
                raise _ReaderPanelPermanentWorkError(ReaderPanelSafeError.INVALID_OUTPUT.value)
            calls = sum(row.provider_calls for row in rows)
            phase_calls = sum(row.provider_calls for row in rows if row.phase == phase.value)
            input_used = sum(row.input_tokens for row in rows)
            output_used = sum(row.output_tokens for row in rows)
            if (
                calls >= max_calls
                or phase_calls >= max_phase_calls
                or input_used + input_reservation > max_input
                or output_used + output_reservation > max_output
                or len(work_rows) >= max_attempts
            ):
                await self._db.commit()
                raise ReaderPanelBudgetExhaustedError()

            attempt = ReaderPanelInvocation(
                id=uuid4(),
                session_id=session_id,
                phase=phase.value,
                work_key=work_key,
                attempt=len(work_rows) + 1,
                status=ReaderPanelInvocationStatus.STARTED.value,
                error_code=None,
                provider_calls=1,
                input_tokens=input_reservation,
                output_tokens=output_reservation,
                started_at=datetime.now(timezone.utc),
            )
            self._db.add(attempt)
            await self._db.commit()

            try:
                output = await asyncio.to_thread(invoke, request)
                if type(output) is not expected_type:
                    raise ProviderInvalidOutputError()
                if validate is not None:
                    output = validate(output)
                output_json = (
                    output.model_dump_json() if hasattr(output, "model_dump_json") else repr(output)
                )
                output_charge = max(1, len(output_json.encode("utf-8")))
            except asyncio.CancelledError:
                await asyncio.shield(self._settle_cancelled_attempt(session_id, attempt.id))
                raise
            except Exception as exc:
                code, retryable = self._safe_provider_error(exc)
                try:
                    await self._locked_session(session_id)
                    fresh_attempt = await self._db.get(
                        ReaderPanelInvocation, attempt.id, populate_existing=True
                    )
                except asyncio.CancelledError:
                    await asyncio.shield(self._settle_cancelled_attempt(session_id, attempt.id))
                    raise
                if (
                    fresh_attempt is None
                    or fresh_attempt.session_id != session_id
                    or fresh_attempt.status != ReaderPanelInvocationStatus.STARTED.value
                ):
                    await self._db.commit()
                    raise ReaderPanelInvalidStateError() from None
                fresh_attempt.status = ReaderPanelInvocationStatus.FAILED.value
                fresh_attempt.error_code = code
                fresh_attempt.completed_at = datetime.now(timezone.utc)
                await self._db.commit()
                invalid_count = sum(
                    row.error_code == ReaderPanelSafeError.INVALID_OUTPUT.value for row in work_rows
                ) + (code == ReaderPanelSafeError.INVALID_OUTPUT.value)
                if (
                    retryable
                    and len(work_rows) + 1 < max_attempts
                    and (
                        code != ReaderPanelSafeError.INVALID_OUTPUT.value
                        or invalid_count <= max_repairs
                    )
                ):
                    continue
                raise

            try:
                await asyncio.sleep(0)
                fresh = await self._locked_session(session_id)
                fresh_attempt = await self._db.get(
                    ReaderPanelInvocation, attempt.id, populate_existing=True
                )
            except asyncio.CancelledError:
                await asyncio.shield(self._settle_cancelled_attempt(session_id, attempt.id))
                raise
            if (
                fresh_attempt is None
                or fresh_attempt.session_id != session_id
                or fresh_attempt.status != ReaderPanelInvocationStatus.STARTED.value
            ):
                await self._db.commit()
                raise ReaderPanelInvalidStateError()
            if fresh.status in _TERMINAL_PANEL_STATUSES:
                fresh_attempt.status = ReaderPanelInvocationStatus.CANCELLED.value
                fresh_attempt.error_code = ReaderPanelSafeError.CANCELLED.value
                fresh_attempt.completed_at = datetime.now(timezone.utc)
                await self._db.commit()
                raise ReaderPanelInvalidStateError()
            if isinstance(fresh.created_at, datetime):
                created = fresh.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - created).total_seconds() >= max_elapsed:
                    fresh_attempt.status = ReaderPanelInvocationStatus.FAILED.value
                    fresh_attempt.error_code = ReaderPanelSafeError.BUDGET_EXHAUSTED.value
                    fresh_attempt.completed_at = datetime.now(timezone.utc)
                    await self._db.commit()
                    raise ReaderPanelBudgetExhaustedError()
            if output_charge > output_reservation:
                fresh_attempt.status = ReaderPanelInvocationStatus.FAILED.value
                fresh_attempt.error_code = ReaderPanelSafeError.BUDGET_EXHAUSTED.value
                fresh_attempt.completed_at = datetime.now(timezone.utc)
                await self._db.commit()
                raise ReaderPanelBudgetExhaustedError()
            fresh_attempt.status = ReaderPanelInvocationStatus.SUCCEEDED.value
            fresh_attempt.completed_at = datetime.now(timezone.utc)
            return output

    async def _reject_report_synthesis_output(self, session_id: UUID) -> None:
        """Turn a semantically invalid synthesis success into a repairable failure."""
        session = await self._locked_session(session_id)
        snapshot = self._validate_config_snapshot(session)
        attempts = (
            (
                await self._db.execute(
                    select(ReaderPanelInvocation)
                    .where(
                        ReaderPanelInvocation.session_id == session_id,
                        ReaderPanelInvocation.phase
                        == ReaderPanelInvocationPhase.REPORT_SYNTHESIS.value,
                        ReaderPanelInvocation.work_key == "editor_handoff",
                    )
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        attempts = [
            item
            for item in attempts
            if item.session_id == session_id
            and item.phase == ReaderPanelInvocationPhase.REPORT_SYNTHESIS.value
            and item.work_key == "editor_handoff"
        ]
        succeeded = next(
            (
                item
                for item in reversed(sorted(attempts, key=lambda value: value.attempt))
                if item.status == ReaderPanelInvocationStatus.SUCCEEDED.value
            ),
            None,
        )
        if succeeded is None:
            return
        succeeded.status = ReaderPanelInvocationStatus.FAILED.value
        succeeded.error_code = ReaderPanelSafeError.INVALID_OUTPUT.value
        succeeded.completed_at = datetime.now(timezone.utc)
        if (
            sum(item.error_code == ReaderPanelSafeError.INVALID_OUTPUT.value for item in attempts)
            > snapshot["max_invalid_output_repairs"] + 1
        ):
            raise ReaderPanelInvalidStateError()

    async def _initialization_result(
        self, panel_session: ReaderPanelSession, *, commit: bool
    ) -> ReaderPanelSessionResult:
        runs = (
            await self._db.execute(
                select(ReaderRun).where(ReaderRun.session_id == panel_session.id)
            )
        ).scalars().all()
        result = ReaderPanelSessionResult(
            session_id=panel_session.id,
            workflow_run_id=panel_session.workflow_run_id,
            status=panel_session.status,
            mode=panel_session.mode,
            project_id=panel_session.project_id,
            chapter_id=panel_session.chapter_id,
            document_id=panel_session.document_id,
            document_version_id=panel_session.document_version_id,
            source_hash=panel_session.source_hash,
            planned_readers=len(runs),
            completed_readers=sum(run.status == "completed" for run in runs),
            initial_reports_locked=panel_session.initial_reports_locked_at is not None,
            review_report_id=panel_session.review_report_id,
            stale=panel_session.stale,
            degradation_reason=panel_session.degradation_reason,
            failure_reason=panel_session.failure_reason,
        )
        if commit:
            await self._db.commit()
        else:
            await self._db.flush()
        return result

    async def initialize_from_revision_ready(
        self,
        *,
        chapter_workflow_run: WorkflowRun,
        ready_pair: RevisionReadyPair,
        mode: PanelMode | str,
    ) -> ReaderPanelSessionResult:
        """Create or reuse the Panel bound to one authoritative READY capability."""

        try:
            panel_mode = PanelMode(mode.lower()) if isinstance(mode, str) else mode
            state = ready_pair.state
            document_id = UUID(state.document_id)
            version_id = UUID(state.document_version_id)
            chapter_run_id = chapter_workflow_run.id
            project_id = chapter_workflow_run.project_id
            chapter_id = chapter_workflow_run.chapter_id
            key = (
                str(chapter_run_id),
                str(version_id),
                state.review_policy_version,
            )
            status_value = getattr(state.status, "value", state.status)
        except (AttributeError, TypeError, ValueError):
            raise ReaderPanelInvalidStateError() from None
        if (
            panel_mode is PanelMode.OFF
            or project_id is None
            or chapter_id is None
            or chapter_workflow_run.workflow_type != "chapter_production_v2"
            or status_value != "REVISION_READY"
            or state.current_node != "REVISION_READY"
            or tuple(state.semantic_ready_key) != key
            or ready_pair.checkpoint.workflow_run_id != chapter_run_id
            or ready_pair.checkpoint.node_name != "REVISION_READY"
            or ready_pair.event.node_name != "REVISION_READY"
            or type(state.chief_editor_required) is not bool
        ):
            raise ReaderPanelInvalidStateError()

        locked_run = await self._db.get(
            WorkflowRun,
            chapter_run_id,
            populate_existing=True,
            with_for_update=True,
        )
        document = await self._db.get(
            Document, document_id, populate_existing=True, with_for_update=True
        )
        version = await self._db.get(
            DocumentVersion, version_id, populate_existing=True, with_for_update=True
        )
        if (
            locked_run is None
            or locked_run.workflow_type != "chapter_production_v2"
            or locked_run.project_id != project_id
            or locked_run.chapter_id != chapter_id
            or locked_run.status != "REVISION_READY"
            or locked_run.current_node != "REVISION_READY"
            or locked_run.awaiting_user
            or document is None
            or document.project_id != project_id
            or document.chapter_id != chapter_id
            or document.current_version_id != version_id
            or version is None
            or version.document_id != document_id
            or version.content_hash != state.content_hash
        ):
            raise ReaderPanelInvalidStateError()

        binding = {
            "chapter_workflow_run_id": str(chapter_run_id),
            "project_id": str(project_id),
            "chapter_id": str(chapter_id),
            "document_id": str(document_id),
            "document_version_id": str(version_id),
            "source_hash": version.content_hash,
            "review_policy_version": state.review_policy_version,
            "chief_editor_required": state.chief_editor_required,
            "editor_report_id": state.editor_report_id,
            "chief_editor_report_id": state.chief_editor_report_id,
            "lore_report_id": state.lore_report_id,
        }
        try:
            UUID(binding["editor_report_id"])
            UUID(binding["lore_report_id"])
            if binding["chief_editor_report_id"] is not None:
                UUID(binding["chief_editor_report_id"])
        except (TypeError, ValueError):
            raise ReaderPanelInvalidStateError() from None

        key_snapshot = list(key)
        panel_runs = (
            (
                await self._db.execute(
                    select(WorkflowRun)
                    .where(
                        or_(
                            WorkflowRun.workflow_type == "reader_panel",
                            WorkflowRun.metadata_["reader_panel_revision_ready_key"]
                            .as_string()
                            .is_not(None),
                            WorkflowRun.metadata_["reader_panel_revision_ready_binding"]
                            .as_string()
                            .is_not(None),
                            WorkflowRun.metadata_["reader_panel_request"]
                            .as_string()
                            .is_not(None),
                        )
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        matches = []

        def current_claim(value: object) -> tuple[bool, bool]:
            if isinstance(value, dict):
                run_id = value.get("chapter_workflow_run_id")
                ready_version_id = value.get("document_version_id")
                policy = value.get("review_policy_version")
            elif isinstance(value, (list, tuple)) and len(value) >= 2:
                run_id, ready_version_id = value[:2]
                policy = value[2] if len(value) >= 3 else None
            else:
                return False, False
            if (run_id, ready_version_id) != key[:2]:
                return False, False
            if not isinstance(policy, str) or not policy:
                return False, True
            return policy == key[2], False

        for candidate in panel_runs:
            metadata = candidate.metadata_ if isinstance(candidate.metadata_, dict) else {}
            stored_key = metadata.get("reader_panel_revision_ready_key")
            stored_binding = metadata.get("reader_panel_revision_ready_binding")
            request = metadata.get("reader_panel_request")
            request_binding = (
                request.get("reader_panel_revision_ready_binding")
                if isinstance(request, dict)
                else None
            )
            claims = [
                current_claim(stored_key),
                current_claim(stored_binding),
                current_claim(request_binding),
            ]
            if any(corrupt for _, corrupt in claims):
                raise ReaderPanelInvalidStateError()
            if not any(claimed for claimed, _ in claims):
                continue
            if (
                candidate.workflow_type != "reader_panel"
                or stored_key != key_snapshot
                or stored_binding != binding
                or request_binding != binding
                or candidate.project_id != project_id
                or candidate.chapter_id != chapter_id
            ):
                raise ReaderPanelInvalidStateError()
            matches.append(candidate)
        if len(matches) > 1:
            raise ReaderPanelInvalidStateError()
        if matches:
            sessions = (
                (
                    await self._db.execute(
                        select(ReaderPanelSession).where(
                            ReaderPanelSession.workflow_run_id == matches[0].id
                        )
                    )
                )
                .scalars()
                .all()
            )
            sessions = [item for item in sessions if item.workflow_run_id == matches[0].id]
            if len(sessions) != 1:
                raise ReaderPanelInvalidStateError()
            panel_session = sessions[0]
            try:
                config_snapshot = self._validate_config_snapshot(panel_session)
                target_audience = list(panel_session.target_audience)
                test_goals = list(panel_session.test_goals)
            except (TypeError, ValueError):
                raise ReaderPanelInvalidStateError() from None
            metadata = matches[0].metadata_
            expected_request = {
                "document_id": str(document_id),
                "document_version_id": str(version_id),
                "source_hash": version.content_hash,
                "mode": panel_session.mode,
                "config": dict(config_snapshot),
                "target_audience": target_audience,
                "test_goals": test_goals,
                "reader_panel_revision_ready_binding": dict(binding),
            }
            expected_metadata = {
                "chapter_id": str(chapter_id),
                "document_id": str(document_id),
                "document_version_id": str(version_id),
                "source_hash": version.content_hash,
                "mode": panel_session.mode,
                "reader_panel_request": expected_request,
                "reader_panel_revision_ready_key": key_snapshot,
                "reader_panel_revision_ready_binding": binding,
            }
            reader_runs = (
                (
                    await self._db.execute(
                        select(ReaderRun).where(ReaderRun.session_id == panel_session.id)
                    )
                )
                .scalars()
                .all()
            )
            reader_runs = [item for item in reader_runs if item.session_id == panel_session.id]
            panel_statuses = {item.value for item in ReaderPanelStatus}
            terminal = panel_session.status in _TERMINAL_PANEL_STATUSES
            completed = panel_session.status in {
                ReaderPanelStatus.COMPLETED.value,
                ReaderPanelStatus.DEGRADED_COMPLETED.value,
            }
            if (
                panel_session.workflow_run_id != matches[0].id
                or panel_session.project_id != project_id
                or panel_session.chapter_id != chapter_id
                or panel_session.document_id != document_id
                or panel_session.document_version_id != version_id
                or panel_session.source_hash != version.content_hash
                or panel_session.status not in panel_statuses
                or metadata != expected_metadata
                or len(reader_runs) != config_snapshot["reader_count"]
                or len({item.id for item in reader_runs}) != len(reader_runs)
                or {item.reader_profile_id for item in reader_runs}
                != set(config_snapshot["reader_profile_ids"])
                or any(item.status not in {"pending", "completed", "failed"} for item in reader_runs)
                or matches[0].awaiting_user
                or matches[0].next_node is not None
                or (
                    terminal
                    and (
                        matches[0].status != panel_session.status
                        or matches[0].completed_at is None
                        or panel_session.completed_at is None
                        or matches[0].completed_at != panel_session.completed_at
                        or matches[0].current_node
                        != ("completed" if completed else panel_session.status)
                        or panel_session.current_step
                        != ("completed" if completed else panel_session.status)
                        or (completed and panel_session.review_report_id is None)
                    )
                )
                or (
                    not terminal
                    and (
                        matches[0].status != "running"
                        or matches[0].current_node is not None
                        or matches[0].completed_at is not None
                        or panel_session.completed_at is not None
                    )
                )
            ):
                raise ReaderPanelInvalidStateError()
            return await self._initialization_result(panel_session, commit=False)

        return await self._initialize_session(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document_id,
            document_version_id=version_id,
            mode=panel_mode,
            idempotency_key=None,
            ready_binding=binding,
            commit=False,
        )

    async def initialize_session(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        document_id: UUID | None = None,
        document_version_id: UUID | None = None,
        mode: PanelMode | str = PanelMode.STANDARD,
        config: ReaderPanelConfig | None = None,
        test_goals: list[str] | None = None,
        target_audience: list[str] | None = None,
        custom_profile_ids: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> ReaderPanelSessionResult:
        """Initializes a version-bound Reader Panel session and its individual ReaderRun slots."""
        return await self._initialize_session(
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=document_id,
            document_version_id=document_version_id,
            mode=mode,
            config=config,
            test_goals=test_goals,
            target_audience=target_audience,
            custom_profile_ids=custom_profile_ids,
            idempotency_key=idempotency_key,
            ready_binding=None,
            commit=True,
        )

    async def _initialize_session(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        document_id: UUID | None,
        document_version_id: UUID | None,
        mode: PanelMode | str,
        config: ReaderPanelConfig | None = None,
        test_goals: list[str] | None = None,
        target_audience: list[str] | None = None,
        custom_profile_ids: list[str] | None = None,
        idempotency_key: str | None,
        ready_binding: dict[str, object] | None,
        commit: bool,
    ) -> ReaderPanelSessionResult:
        panel_mode = PanelMode(mode.lower()) if isinstance(mode, str) else mode
        panel_config = config or get_mode_preset_config(panel_mode)
        if idempotency_key is not None and not re.fullmatch(
            r"[A-Za-z0-9:_-]{1,128}", idempotency_key
        ):
            raise ReaderPanelInvalidStateError()
        for values in (test_goals, target_audience):
            if values is not None and (
                not isinstance(values, list)
                or len(values) > 16
                or any(
                    not isinstance(value, str) or not 1 <= len(value.strip()) <= 256
                    for value in values
                )
            ):
                raise ReaderPanelInvalidStateError()
        profile_ids = custom_profile_ids or list(panel_config.reader_profile_ids)
        if (
            panel_config.mode != panel_mode
            or len(profile_ids) != panel_config.reader_count
            or len(profile_ids) < panel_config.min_valid_readers
            or len(profile_ids) != len(set(profile_ids))
            or any(not isinstance(item, str) or not item for item in profile_ids)
        ):
            raise ReaderPanelInvalidStateError()

        # 1. Resolve project, chapter, document, and immutable version
        project = await self._db.get(Project, project_id)
        if project is None:
            raise ReaderPanelNotFoundError()

        chapter = await self._db.get(Chapter, chapter_id)
        if chapter is None or chapter.project_id != project_id:
            raise ReaderPanelNotFoundError()

        # Serialize API starts within a chapter without changing the persistence schema.
        await self._db.execute(select(Chapter.id).where(Chapter.id == chapter_id).with_for_update())

        if document_id is not None:
            doc = await self._db.get(Document, document_id, populate_existing=True)
            if doc is None or doc.project_id != project_id or doc.chapter_id != chapter_id:
                raise ReaderPanelNotFoundError()
        else:
            doc_stmt = (
                select(Document)
                .where(
                    Document.project_id == project_id,
                    Document.chapter_id == chapter_id,
                )
                .order_by(Document.created_at.desc())
            )
            documents = (await self._db.execute(doc_stmt)).scalars().all()
            doc = next(
                (
                    candidate
                    for candidate in documents
                    if candidate.project_id == project_id and candidate.chapter_id == chapter_id
                ),
                None,
            )
        if doc is None or doc.current_version_id is None:
            raise ReaderPanelInvalidStateError()

        bound_version_id = document_version_id or doc.current_version_id
        doc_version = await self._db.get(DocumentVersion, bound_version_id, populate_existing=True)
        if doc_version is None or doc_version.document_id != doc.id:
            raise ReaderPanelNotFoundError()
        if not doc_version.content_hash:
            raise ReaderPanelInvalidStateError()

        if is_mode_off(panel_mode):
            result = ReaderPanelSessionResult(
                session_id=None,
                workflow_run_id=None,
                status="off",
                mode="off",
                project_id=project_id,
                chapter_id=chapter_id,
                document_id=doc.id,
                document_version_id=doc_version.id,
                source_hash=doc_version.content_hash,
                is_noop=True,
                stale=doc.current_version_id != doc_version.id,
                message="Reader panel is disabled (mode=off)",
            )
            if commit:
                await self._db.commit()
            else:
                await self._db.flush()
            return result

        project_meta = project.metadata_ if isinstance(project.metadata_, dict) else {}
        final_target_audience = (
            target_audience
            if target_audience is not None
            else list(project_meta.get("target_audience", []))
        )
        final_test_goals = test_goals or []
        config_snapshot = {
            "mode": panel_mode.value,
            "reader_count": len(profile_ids),
            "reader_profile_ids": list(profile_ids),
            "min_valid_readers": panel_config.min_valid_readers,
            "max_ballot_issues": panel_config.max_ballot_issues,
            "max_discussion_issues": panel_config.max_discussion_issues,
            "max_rounds_per_issue": panel_config.max_rounds_per_issue,
            "max_total_model_calls": panel_config.max_total_model_calls,
            "max_model_calls_per_phase": panel_config.max_model_calls_per_phase,
            "max_total_input_tokens": panel_config.max_total_input_tokens,
            "max_total_output_tokens": panel_config.max_total_output_tokens,
            "max_input_tokens_per_call": panel_config.max_input_tokens_per_call,
            "max_output_tokens_per_call": panel_config.max_output_tokens_per_call,
            "max_messages": panel_config.max_messages,
            "max_provider_attempts": panel_config.max_provider_attempts,
            "max_invalid_output_repairs": panel_config.max_invalid_output_repairs,
            "max_execution_seconds": panel_config.max_execution_seconds,
        }
        request_snapshot = {
            "document_id": str(doc.id),
            "document_version_id": str(doc_version.id),
            "source_hash": doc_version.content_hash,
            "mode": panel_mode.value,
            "config": dict(config_snapshot),
            "target_audience": list(final_target_audience),
            "test_goals": list(final_test_goals),
        }
        if ready_binding is not None:
            request_snapshot["reader_panel_revision_ready_binding"] = dict(ready_binding)

        # 2. Manual starts retain their existing request/idempotency replay behavior.
        existing_session = None
        if ready_binding is None:
            sessions = (
                (
                    await self._db.execute(
                        select(ReaderPanelSession)
                        .where(
                            ReaderPanelSession.project_id == project_id,
                            ReaderPanelSession.chapter_id == chapter_id,
                        )
                        .order_by(
                            ReaderPanelSession.created_at.desc(),
                            ReaderPanelSession.id.desc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            sessions = [
                candidate
                for candidate in sessions
                if candidate.project_id == project_id and candidate.chapter_id == chapter_id
            ]
            for candidate in sessions:
                run = await self._db.get(
                    WorkflowRun, candidate.workflow_run_id, populate_existing=True
                )
                metadata = run.metadata_ if run is not None and isinstance(run.metadata_, dict) else {}
                if idempotency_key is not None:
                    if metadata.get("reader_panel_idempotency_key") != idempotency_key:
                        continue
                    if metadata.get("reader_panel_request") != request_snapshot:
                        raise ReaderPanelInvalidStateError()
                    existing_session = candidate
                    break
                if metadata.get(
                    "reader_panel_request"
                ) == request_snapshot and candidate.status not in {
                    ReaderPanelStatus.CANCELLED.value,
                    ReaderPanelStatus.FAILED.value,
                }:
                    existing_session = candidate
                    break
        if existing_session is not None:
            return await self._initialization_result(existing_session, commit=commit)

        # 3. Create chapter-scoped workflow run
        workflow_metadata = {
            "chapter_id": str(chapter_id),
            "document_id": str(doc.id),
            "document_version_id": str(doc_version.id),
            "source_hash": doc_version.content_hash,
            "mode": panel_mode.value,
            "reader_panel_request": request_snapshot,
        }
        if ready_binding is not None:
            workflow_metadata["reader_panel_revision_ready_key"] = [
                str(ready_binding["chapter_workflow_run_id"]),
                str(ready_binding["document_version_id"]),
                str(ready_binding["review_policy_version"]),
            ]
            workflow_metadata["reader_panel_revision_ready_binding"] = dict(ready_binding)
        else:
            workflow_metadata["reader_panel_idempotency_key"] = idempotency_key
        workflow_run = WorkflowRun(
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_type="reader_panel",
            status="running",
            metadata_=workflow_metadata,
        )
        self._db.add(workflow_run)
        await self._db.flush()

        # 4. Resolve audience and goals
        # 5. Create ReaderPanelSession
        session_id = uuid4()
        panel_session = ReaderPanelSession(
            id=session_id,
            project_id=project_id,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run.id,
            document_id=doc.id,
            document_version_id=doc_version.id,
            source_hash=doc_version.content_hash,
            mode=panel_mode.value,
            status=ReaderPanelStatus.INDEPENDENT_READING.value,
            stale=doc.current_version_id != doc_version.id,
            config_snapshot=dict(config_snapshot),
            model_snapshot={"provider": "fake", "model": "deterministic-reader-panel-v1"},
            prompt_snapshot={"version": "v1"},
            target_audience=list(final_target_audience),
            test_goals=list(final_test_goals),
        )
        self._db.add(panel_session)

        # 6. Create ReaderRun slots for all planned reader profiles
        for pid in profile_ids:
            reader_run = ReaderRun(
                id=uuid4(),
                session_id=session_id,
                reader_profile_id=pid,
                status="pending",
                is_target_audience=pid in ("general_immersive", "genre_experienced"),
                retry_count=0,
            )
            self._db.add(reader_run)

        if commit:
            await self._db.commit()
        else:
            await self._db.flush()

        return ReaderPanelSessionResult(
            session_id=session_id,
            workflow_run_id=workflow_run.id,
            status=ReaderPanelStatus.INDEPENDENT_READING.value,
            mode=panel_mode.value,
            project_id=project_id,
            chapter_id=chapter_id,
            document_id=doc.id,
            document_version_id=doc_version.id,
            source_hash=doc_version.content_hash,
            is_noop=False,
            planned_readers=len(profile_ids),
            completed_readers=0,
            initial_reports_locked=False,
            stale=doc.current_version_id != doc_version.id,
        )

    async def _scoped_panel(
        self, *, project_id: UUID, chapter_id: UUID, session_id: UUID
    ) -> ReaderPanelSession:
        panels = (
            (
                await self._db.execute(
                    select(ReaderPanelSession)
                    .where(
                        ReaderPanelSession.id == session_id,
                        ReaderPanelSession.project_id == project_id,
                        ReaderPanelSession.chapter_id == chapter_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        panel = next(
            (
                candidate
                for candidate in panels
                if candidate.id == session_id
                and candidate.project_id == project_id
                and candidate.chapter_id == chapter_id
            ),
            None,
        )
        if panel is None or panel.project_id != project_id or panel.chapter_id != chapter_id:
            raise ReaderPanelNotFoundError()
        await self._refresh_stale_flag(panel)
        return panel

    async def _api_projection(
        self,
        panel: ReaderPanelSession,
        *,
        include_initial_reports: bool = False,
        include_transcript: bool = False,
        data_limit: int = 50,
    ) -> dict[str, Any]:
        if not 1 <= data_limit <= 200:
            raise ReaderPanelInvalidStateError()

        async def rows_for(model: type) -> list[Any]:
            return (
                (
                    await self._db.execute(
                        select(model)
                        .where(model.session_id == panel.id)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )

        runs = [row for row in await rows_for(ReaderRun) if row.session_id == panel.id]
        issues = [row for row in await rows_for(ReaderPanelIssue) if row.session_id == panel.id]
        ballots = [row for row in await rows_for(ReaderPanelBallot) if row.session_id == panel.id]
        messages = [row for row in await rows_for(ReaderPanelMessage) if row.session_id == panel.id]

        review_projection = None
        if panel.review_report_id is not None:
            report = await self._db.get(
                ReviewReport, panel.review_report_id, populate_existing=True
            )
            if (
                report is None
                or report.project_id != panel.project_id
                or report.chapter_id != panel.chapter_id
                or report.workflow_run_id != panel.workflow_run_id
                or report.target_document_id != panel.document_id
                or report.target_version_id != panel.document_version_id
                or report.review_mode != "reader_panel"
                or report.reviewer_agent_role != "moderator_agent"
            ):
                raise ReaderPanelInvalidStateError()
            try:
                if (
                    not isinstance(report.blocking_issues, list)
                    or len(report.blocking_issues) > 16
                    or not isinstance(report.warnings, list)
                    or len(report.warnings) > 32
                    or not isinstance(report.notes, list)
                    or len(report.notes) > 16
                    or not isinstance(report.suggested_actions, list)
                    or len(report.suggested_actions) > 16
                ):
                    raise ValueError
                blocking_issues = []
                for item in report.blocking_issues:
                    if (
                        not isinstance(item, dict)
                        or set(item) != {"issue_number", "title"}
                        or type(item["issue_number"]) is not int
                        or item["issue_number"] < 1
                        or not isinstance(item["title"], str)
                        or len(item["title"]) > 256
                    ):
                        raise ValueError
                    blocking_issues.append(
                        {
                            "issue_number": item["issue_number"],
                            "title": validate_reader_panel_text(
                                item["title"], "issue_title", max_bytes=1000
                            ),
                        }
                    )
                suggested_actions = []
                for item in report.suggested_actions:
                    action = ActionableRecommendationItem.model_validate(item)
                    if any(
                        not isinstance(segment_id, str)
                        or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", segment_id) is None
                        for segment_id in action.target_segment_ids
                    ):
                        raise ValueError
                    suggested_actions.append(action.model_dump(mode="json"))
                review_projection = {
                    "summary": validate_reader_panel_text(
                        report.summary, "summary", max_bytes=8000
                    ),
                    "blocking_issues": blocking_issues,
                    "warnings": [
                        validate_reader_panel_text(item, "warning", max_bytes=2000)
                        for item in report.warnings
                    ],
                    "notes": [
                        validate_reader_panel_text(item, "note", max_bytes=2000)
                        for item in report.notes
                    ],
                    "suggested_actions": suggested_actions,
                }
            except Exception:
                raise ReaderPanelInvalidStateError() from None

        if len(issues) > 16:
            raise ReaderPanelInvalidStateError()
        issue_projection = []
        try:
            for issue in sorted(issues, key=lambda item: (item.issue_number, str(item.id))):
                item = ExtractedIssueItem(
                    issue_number=issue.issue_number,
                    title=issue.title,
                    category=issue.category,
                    symptom=issue.symptom,
                    root_cause_hypotheses=issue.root_cause_hypotheses,
                    evidence=issue.evidence,
                    source_reader_ids=[],
                    target_audience_relevance=issue.target_audience_relevance,
                    minority_risk=issue.minority_risk,
                    discussion_status=issue.discussion_status,
                ).model_dump(mode="json", exclude={"source_reader_ids"})
                item["consensus_class"] = (
                    ContractConsensusClass(issue.consensus_class).value
                    if issue.consensus_class is not None
                    else None
                )
                item["recommended_priority"] = (
                    ContractEditorialDecision(issue.recommended_priority).value
                    if issue.recommended_priority is not None
                    else None
                )
                issue_projection.append(item)
        except Exception:
            raise ReaderPanelInvalidStateError() from None

        terminal = panel.status in _TERMINAL_PANEL_STATUSES
        result: dict[str, Any] = {
            "session_id": panel.id,
            "workflow_run_id": panel.workflow_run_id,
            "project_id": panel.project_id,
            "chapter_id": panel.chapter_id,
            "document_id": panel.document_id,
            "document_version_id": panel.document_version_id,
            "source_hash": panel.source_hash,
            "mode": panel.mode,
            "status": panel.status,
            "is_noop": False,
            "stale": panel.stale,
            "degradation_reason": panel.degradation_reason,
            "failure_reason": panel.failure_reason,
            "planned_readers": len(runs),
            "completed_readers": sum(row.status == "completed" for row in runs),
            "failed_readers": sum(row.status == "failed" for row in runs),
            "issue_count": len(issues),
            "initial_ballot_count": sum(row.phase == "initial" for row in ballots),
            "final_ballot_count": sum(row.phase == "final" for row in ballots),
            "discussion_message_count": len(messages),
            "created_at": getattr(panel, "created_at", None),
            "updated_at": getattr(panel, "updated_at", None),
            "completed_at": panel.completed_at,
            "review_report": review_projection,
            "issues": issue_projection,
            "permitted_operations": [] if terminal else ["cancel", "resume"],
        }

        if include_initial_reports:
            reports = [
                row for row in await rows_for(ReaderInitialReport) if row.session_id == panel.id
            ]
            reports.sort(
                key=lambda row: (getattr(row, "created_at", None) or datetime.min, str(row.id))
            )
            try:
                result["initial_reports"] = [
                    ReaderInitialReadingOutput(
                        overall_reaction=row.overall_reaction,
                        continue_reading=row.continue_reading,
                        confidence=row.confidence,
                        strengths=row.strengths,
                        reactions=row.reactions,
                        concerns=row.concerns,
                    ).model_dump(mode="json")
                    for row in reports[:data_limit]
                ]
            except Exception:
                raise ReaderPanelInvalidStateError() from None
        if include_transcript:
            messages.sort(key=lambda row: (row.round_number, row.turn_number, str(row.id)))
            try:
                transcript = []
                for row in messages[:data_limit]:
                    if (
                        row.speaker_type not in {"reader", "moderator"}
                        or type(row.round_number) is not int
                        or not 1 <= row.round_number <= 10
                        or type(row.turn_number) is not int
                        or not 1 <= row.turn_number <= 50
                        or not isinstance(row.claim, str)
                        or len(row.claim) > 2000
                        or not isinstance(row.evidence, list)
                        or len(row.evidence) > 8
                    ):
                        raise ValueError
                    if row.speaker_type == "reader":
                        stance = ContractDiscussionStance(row.stance).value
                    elif row.stance is not None:
                        raise ValueError
                    else:
                        stance = None
                    optional_text = {}
                    for name, max_chars in (("concession", 1000), ("proposed_action", 500)):
                        value = getattr(row, name)
                        if value is not None and (
                            not isinstance(value, str) or len(value) > max_chars
                        ):
                            raise ValueError
                        optional_text[name] = (
                            validate_reader_panel_text(value, name, max_bytes=2000)
                            if value is not None
                            else None
                        )
                    transcript.append(
                        {
                            "issue_id": row.issue_id,
                            "round_number": row.round_number,
                            "turn_number": row.turn_number,
                            "speaker_type": row.speaker_type,
                            "stance": stance,
                            "claim": validate_reader_panel_text(row.claim, "claim", max_bytes=4000),
                            "evidence": [
                                EvidenceRef.model_validate(item).model_dump(mode="json")
                                for item in row.evidence
                            ],
                            **optional_text,
                            "novelty": ContractDiscussionNovelty(row.novelty).value,
                            "created_at": getattr(row, "created_at", None),
                        }
                    )
                result["discussion_transcript"] = transcript
            except Exception:
                raise ReaderPanelInvalidStateError() from None
        return result

    async def get_scoped_session(
        self,
        project_id: UUID,
        chapter_id: UUID,
        session_id: UUID,
        *,
        include_initial_reports: bool = False,
        include_transcript: bool = False,
        data_limit: int = 50,
    ) -> dict[str, Any]:
        """Return a content-bounded API projection after exact scope validation."""
        panel = await self._scoped_panel(
            project_id=project_id, chapter_id=chapter_id, session_id=session_id
        )
        await self._db.flush()
        await self._db.refresh(panel, attribute_names=["created_at", "updated_at", "completed_at"])
        result = await self._api_projection(
            panel,
            include_initial_reports=include_initial_reports,
            include_transcript=include_transcript,
            data_limit=data_limit,
        )
        await self._db.commit()
        return result

    async def list_scoped_sessions(
        self,
        project_id: UUID,
        chapter_id: UUID,
        *,
        offset: int = 0,
        limit: int = 20,
        include_initial_reports: bool = False,
        include_transcript: bool = False,
        data_limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List exact-scope panels newest first with bounded pagination."""
        if not 0 <= offset <= 10000 or not 1 <= limit <= 100:
            raise ReaderPanelInvalidStateError()
        project = await self._db.get(Project, project_id, populate_existing=True)
        chapter = await self._db.get(Chapter, chapter_id, populate_existing=True)
        if project is None or chapter is None or chapter.project_id != project_id:
            raise ReaderPanelNotFoundError()
        rows = (
            (
                await self._db.execute(
                    select(ReaderPanelSession)
                    .where(
                        ReaderPanelSession.project_id == project_id,
                        ReaderPanelSession.chapter_id == chapter_id,
                    )
                    .order_by(
                        ReaderPanelSession.created_at.desc(),
                        ReaderPanelSession.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        panels = [
            row for row in rows if row.project_id == project_id and row.chapter_id == chapter_id
        ]
        panels.sort(
            key=lambda row: (
                getattr(row, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
                str(row.id),
            ),
            reverse=True,
        )
        page = panels[:limit]
        for panel in page:
            await self._refresh_stale_flag(panel)
        await self._db.flush()
        for panel in page:
            await self._db.refresh(
                panel, attribute_names=["created_at", "updated_at", "completed_at"]
            )
        result = [
            await self._api_projection(
                panel,
                include_initial_reports=include_initial_reports,
                include_transcript=include_transcript,
                data_limit=data_limit,
            )
            for panel in page
        ]
        await self._db.commit()
        return result

    async def cancel_scoped_session(
        self, project_id: UUID, chapter_id: UUID, session_id: UUID
    ) -> dict[str, Any]:
        await self._scoped_panel(
            project_id=project_id, chapter_id=chapter_id, session_id=session_id
        )
        await self._db.commit()
        await self.cancel_session(session_id=session_id)
        return await self.get_scoped_session(
            project_id=project_id, chapter_id=chapter_id, session_id=session_id
        )

    async def resume_scoped_session(
        self,
        project_id: UUID,
        chapter_id: UUID,
        session_id: UUID,
        *,
        provider: Any = None,
    ) -> dict[str, Any]:
        await self._scoped_panel(
            project_id=project_id, chapter_id=chapter_id, session_id=session_id
        )
        await self._db.commit()
        await self.resume_session(session_id=session_id, provider=provider)
        return await self.get_scoped_session(
            project_id=project_id, chapter_id=chapter_id, session_id=session_id
        )

    async def collect_initial_reports(
        self,
        *,
        session_id: UUID,
        provider: Any = None,
    ) -> ReaderPanelSessionResult:
        """Executes isolated cold reading across reader runs and locks initial reports upon quorum."""
        panel_provider = provider or DeterministicReaderPanelProvider(
            scenario=ReaderPanelFakeScenario.CLEAN
        )

        # 1. Fetch ReaderPanelSession and verify state
        stmt = (
            select(ReaderPanelSession)
            .options(
                selectinload(ReaderPanelSession.reader_runs).selectinload(ReaderRun.initial_report),
                selectinload(ReaderPanelSession.initial_reports),
                selectinload(ReaderPanelSession.document),
            )
            .where(ReaderPanelSession.id == session_id)
        )
        panel_session = (await self._db.execute(stmt)).scalars().first()
        if panel_session is None:
            raise ReaderPanelNotFoundError()
        reader_runs = list(panel_session.reader_runs)

        # 2. Check staleness against live document version
        await self._refresh_stale_flag(panel_session)

        # If already locked, return existing reports idempotently
        if panel_session.initial_reports_locked_at is not None:
            reports_data = [
                {
                    "reader_profile_id": r.reader_profile_id,
                    "overall_reaction": r.initial_report.overall_reaction
                    if r.initial_report
                    else "",
                    "continue_reading": r.initial_report.continue_reading
                    if r.initial_report
                    else "maybe",
                    "confidence": r.initial_report.confidence if r.initial_report else "medium",
                }
                for r in reader_runs
                if r.initial_report is not None
            ]
            return ReaderPanelSessionResult(
                session_id=panel_session.id,
                workflow_run_id=panel_session.workflow_run_id,
                status=panel_session.status,
                mode=panel_session.mode,
                is_noop=False,
                planned_readers=len(reader_runs),
                completed_readers=len(reports_data),
                initial_reports_locked=True,
                stale=panel_session.stale,
                degradation_reason=panel_session.degradation_reason,
                failure_reason=panel_session.failure_reason,
                reports=reports_data,
            )

        # 3. Retrieve manuscript segments
        doc_version = await self._db.get(DocumentVersion, panel_session.document_version_id)
        if doc_version is None:
            raise ReaderPanelInvalidStateError()

        segments: dict[str, str] = {}
        if isinstance(doc_version.metadata_, dict) and "segments" in doc_version.metadata_:
            segments = doc_version.metadata_["segments"]
        else:
            segments = {"S001": f"Chapter content (hash={panel_session.source_hash[:8]})"}

        project = await self._db.get(Project, panel_session.project_id)
        genre = project.genre if project and project.genre else "fantasy"

        # 4. Concurrently or sequentially run reader agents in mutual isolation
        collected_reports = []
        for run in reader_runs:
            if run.status == "completed" and run.initial_report is not None:
                collected_reports.append(run.initial_report)
                continue

            req = build_cold_read_request(
                project_id=panel_session.project_id,
                chapter_id=panel_session.chapter_id or uuid4(),
                workflow_run_id=panel_session.workflow_run_id,
                reader_profile_id=run.reader_profile_id,
                genre=genre,
                target_audience=list(panel_session.target_audience or []),
                manuscript_segments=segments,
                test_goals=list(panel_session.test_goals or []),
            )

            try:
                output: ReaderInitialReadingOutput = await self._invoke_provider(
                    session_id=panel_session.id,
                    phase=ReaderPanelInvocationPhase.INITIAL_READING,
                    work_key=f"reader:{run.id}",
                    request=req,
                    invoke=panel_provider.generate_initial_reading,
                    expected_type=ReaderInitialReadingOutput,
                )

                # Persist initial report
                init_report = ReaderInitialReport(
                    id=uuid4(),
                    reader_run_id=run.id,
                    session_id=panel_session.id,
                    overall_reaction=output.overall_reaction,
                    continue_reading=output.continue_reading.value,
                    confidence=output.confidence.value,
                    strengths=[s.model_dump() for s in output.strengths],
                    reactions=[r.model_dump() for r in output.reactions],
                    concerns=[c.model_dump() for c in output.concerns],
                    locked=True,
                    locked_at=datetime.now(timezone.utc),
                )
                self._db.add(init_report)
                run.initial_report = init_report
                run.status = "completed"
                run.completed_at = datetime.now(timezone.utc)
                collected_reports.append(init_report)
                await self._db.commit()
            except _ReaderPanelWorkAlreadyCompleted:
                return await self.collect_initial_reports(
                    session_id=session_id, provider=panel_provider
                )
            except _ReaderPanelPermanentWorkError as exc:
                if exc.error_code == ReaderPanelSafeError.UNKNOWN_COMMIT.value:
                    locked = await self._locked_session(panel_session.id)
                    await self._settle_failed(locked, "provider_outcome_unknown")
                    await self._db.commit()
                    raise ReaderPanelInvalidStateError() from None
                run.status = "failed"
                run.error_code = exc.error_code
                run.error_message = None
                await self._db.commit()
            except ReaderPanelInvalidStateError:
                raise
            except Exception as exc:
                error_code, _ = self._safe_provider_error(exc)
                run.status = "failed"
                run.error_code = error_code
                run.error_message = None
                await self._db.commit()

        # 5. Evaluate Quorum
        valid_count = len(collected_reports)
        min_valid = panel_session.config_snapshot.get("min_valid_readers", 1)
        planned_count = len(reader_runs)

        if valid_count >= min_valid:
            panel_session.status = ReaderPanelStatus.INITIAL_REPORTS_LOCKED.value
            panel_session.initial_reports_locked_at = datetime.now(timezone.utc)
            if valid_count < planned_count:
                panel_session.degradation_reason = "reader_sample_degraded"
            await self._db.commit()

            refreshed_session = (
                (
                    await self._db.execute(
                        select(ReaderPanelSession)
                        .options(
                            selectinload(ReaderPanelSession.reader_runs).selectinload(
                                ReaderRun.initial_report
                            )
                        )
                        .where(ReaderPanelSession.id == session_id)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .first()
            )
            if refreshed_session is None:
                raise ReaderPanelNotFoundError()
            refreshed_runs = list(refreshed_session.reader_runs)
            reports_data = [
                {
                    "reader_profile_id": r.reader_profile_id,
                    "overall_reaction": r.initial_report.overall_reaction,
                    "continue_reading": r.initial_report.continue_reading,
                    "confidence": r.initial_report.confidence,
                }
                for r in refreshed_runs
                if r.initial_report is not None
                and r.initial_report.session_id == refreshed_session.id
                and r.initial_report.reader_run_id == r.id
            ]
            return ReaderPanelSessionResult(
                session_id=refreshed_session.id,
                workflow_run_id=refreshed_session.workflow_run_id,
                status=refreshed_session.status,
                mode=refreshed_session.mode,
                is_noop=False,
                planned_readers=planned_count,
                completed_readers=valid_count,
                initial_reports_locked=True,
                stale=refreshed_session.stale,
                degradation_reason=refreshed_session.degradation_reason,
                reports=reports_data,
            )
        else:
            await self._settle_failed(panel_session, "reader_sample_below_minimum")
            await self._db.commit()
            raise ReaderPanelQuorumError()

    async def cancel_session(self, *, session_id: UUID) -> ReaderPanelSessionResult:
        """Cancel unfinished work while preserving all committed panel history."""
        panel_session = await self._locked_session(session_id)
        terminal = {
            ReaderPanelStatus.COMPLETED.value,
            ReaderPanelStatus.DEGRADED_COMPLETED.value,
            ReaderPanelStatus.FAILED.value,
        }
        if panel_session.status in terminal:
            await self._db.commit()
            raise ReaderPanelInvalidStateError()
        await self._settle_cancellation(panel_session)
        await self._db.commit()
        return ReaderPanelSessionResult(
            session_id=panel_session.id,
            workflow_run_id=panel_session.workflow_run_id,
            status=panel_session.status,
            mode=panel_session.mode,
            stale=panel_session.stale,
            failure_reason=panel_session.failure_reason,
        )

    async def resume_session(
        self, *, session_id: UUID, provider: Any = None
    ) -> ReaderPanelSessionResult:
        """Resume from the durable phase boundary without replaying completed work."""
        panel_session = await self._locked_session(session_id)
        status_value = panel_session.status
        if status_value in {
            ReaderPanelStatus.CREATED.value,
            ReaderPanelStatus.PREPARING.value,
        }:
            panel_session.status = ReaderPanelStatus.INDEPENDENT_READING.value
            await self._db.commit()
            status_value = panel_session.status
        else:
            await self._db.commit()

        if status_value == ReaderPanelStatus.INDEPENDENT_READING.value:
            return await self.collect_initial_reports(session_id=session_id, provider=provider)
        if status_value in {
            ReaderPanelStatus.INITIAL_REPORTS_LOCKED.value,
            ReaderPanelStatus.ISSUE_EXTRACTION.value,
            ReaderPanelStatus.INITIAL_BALLOTING.value,
        }:
            return await self.collect_initial_ballots(session_id=session_id, provider=provider)
        if status_value in {
            ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value,
            ReaderPanelStatus.DISCUSSING.value,
            ReaderPanelStatus.FINAL_BALLOTING.value,
            ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value,
        }:
            if status_value == ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value:
                return await self.generate_editor_handoff_report(
                    session_id=session_id, provider=provider
                )
            return await self.run_discussion_and_final_ballots(
                session_id=session_id, provider=provider
            )
        if status_value == ReaderPanelStatus.REPORT_GENERATING.value:
            return await self.generate_editor_handoff_report(
                session_id=session_id, provider=provider
            )
        if status_value in {
            ReaderPanelStatus.COMPLETED.value,
            ReaderPanelStatus.DEGRADED_COMPLETED.value,
            ReaderPanelStatus.FAILED.value,
            ReaderPanelStatus.CANCELLED.value,
        }:
            return ReaderPanelSessionResult(
                session_id=panel_session.id,
                workflow_run_id=panel_session.workflow_run_id,
                status=panel_session.status,
                mode=panel_session.mode,
                review_report_id=panel_session.review_report_id,
                stale=panel_session.stale,
                degradation_reason=panel_session.degradation_reason,
                failure_reason=panel_session.failure_reason,
            )
        raise ReaderPanelInvalidStateError()

    async def collect_initial_ballots(
        self,
        *,
        session_id: UUID,
        provider: Any = None,
    ) -> ReaderPanelSessionResult:
        """Extracts server-owned issues and collects isolated initial ballots."""
        panel_provider = provider or DeterministicReaderPanelProvider(
            scenario=ReaderPanelFakeScenario.CLEAN
        )
        compatible_statuses = {
            ReaderPanelStatus.INITIAL_REPORTS_LOCKED.value,
            ReaderPanelStatus.ISSUE_EXTRACTION.value,
            ReaderPanelStatus.INITIAL_BALLOTING.value,
            ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value,
        }

        async def lock_session() -> ReaderPanelSession:
            stmt = (
                select(ReaderPanelSession)
                .options(
                    selectinload(ReaderPanelSession.reader_runs).selectinload(
                        ReaderRun.initial_report
                    )
                )
                .where(ReaderPanelSession.id == session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            locked = (await self._db.execute(stmt)).scalars().first()
            if (
                locked is None
                or locked.initial_reports_locked_at is None
                or locked.status not in compatible_statuses
                or locked.chapter_id is None
            ):
                raise ReaderPanelInvalidStateError()
            await self._refresh_stale_flag(locked)
            return locked

        def eligible(session: ReaderPanelSession) -> list[ReaderRun]:
            return [
                run
                for run in session.reader_runs
                if run.status == "completed"
                and run.initial_report is not None
                and run.initial_report.locked
                and run.initial_report.locked_at is not None
                and run.initial_report.session_id == session.id
                and run.initial_report.reader_run_id == run.id
            ]

        def source_snapshot(
            session: ReaderPanelSession, _eligible_runs: list[ReaderRun]
        ) -> tuple[Any, ...]:
            bound_runs = [
                run
                for run in session.reader_runs
                if run.initial_report is not None
                and run.initial_report.locked
                and run.initial_report.locked_at is not None
            ]
            return (
                session.document_version_id,
                session.source_hash,
                session.initial_reports_locked_at,
                tuple(
                    sorted(
                        (
                            str(run.id),
                            str(run.initial_report.id),
                            run.reader_profile_id,
                            str(run.initial_report.locked_at),
                        )
                        for run in bound_runs
                    )
                ),
            )

        panel_session = await lock_session()
        runs = list(panel_session.reader_runs)
        eligible_runs = eligible(panel_session)
        if not eligible_runs:
            raise ReaderPanelInvalidStateError()
        initial_source_snapshot = source_snapshot(panel_session, eligible_runs)
        doc_version = await self._db.get(DocumentVersion, panel_session.document_version_id)
        segments = (
            doc_version.metadata_.get("segments")
            if doc_version is not None and isinstance(doc_version.metadata_, dict)
            else None
        )
        if (
            not isinstance(segments, dict)
            or not segments
            or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in segments.items()
            )
        ):
            raise ReaderPanelInvalidStateError()
        segment_ids = set(segments)
        issues = (
            (
                await self._db.execute(
                    select(ReaderPanelIssue)
                    .where(ReaderPanelIssue.session_id == panel_session.id)
                    .order_by(ReaderPanelIssue.issue_number)
                )
            )
            .scalars()
            .all()
        )
        needs_extraction = not issues and panel_session.status in {
            ReaderPanelStatus.INITIAL_REPORTS_LOCKED.value,
            ReaderPanelStatus.ISSUE_EXTRACTION.value,
        }
        if needs_extraction:
            report_payload = {
                str(run.initial_report.id): {
                    "reader_profile_id": run.reader_profile_id,
                    "overall_reaction": run.initial_report.overall_reaction,
                    "continue_reading": run.initial_report.continue_reading,
                    "confidence": run.initial_report.confidence,
                    "strengths": run.initial_report.strengths,
                    "reactions": run.initial_report.reactions,
                    "concerns": run.initial_report.concerns,
                }
                for run in eligible_runs
            }
            request = ModeratorIssueExtractionRequest(
                project_id=panel_session.project_id,
                chapter_id=panel_session.chapter_id,
                workflow_run_id=panel_session.workflow_run_id,
                reader_initial_reports=report_payload,
                manuscript_segments=segments,
                max_ballot_issues=panel_session.config_snapshot.get("max_ballot_issues", 8),
            )
            profile_to_report_ids: dict[str, set[str]] = {}
            uuid_to_report_ids: dict[str, set[str]] = {}
            for run in eligible_runs:
                report_id = str(run.initial_report.id)
                profile_to_report_ids.setdefault(run.reader_profile_id, set()).add(report_id)
                uuid_to_report_ids.setdefault(str(run.id), set()).add(report_id)
                uuid_to_report_ids.setdefault(report_id, set()).add(report_id)
            if any(len(report_ids) != 1 for report_ids in profile_to_report_ids.values()):
                raise ReaderPanelInvalidStateError()
            profile_to_report_id = {
                profile: next(iter(report_ids))
                for profile, report_ids in profile_to_report_ids.items()
            }
            profile_patterns = tuple(
                re.compile(rf"(?<![\w-]){re.escape(profile)}(?![\w-])", re.IGNORECASE)
                for profile in profile_to_report_id
            )
            uuid_tokens = set(uuid_to_report_ids)
            max_issues = request.max_ballot_issues
            normalized: list[ExtractedIssueItem] | None = None
            extraction_failure_reason = "invalid_issue_extraction"

            def normalize_extraction(
                output: ModeratorIssueExtractionOutput,
            ) -> list[ExtractedIssueItem]:
                if len(output.issues) > max_issues:
                    raise ValueError
                issue_numbers = [issue.issue_number for issue in output.issues]
                if len(issue_numbers) != len(set(issue_numbers)):
                    raise ValueError
                deduped: list[ExtractedIssueItem] = []
                dedupe_indexes: dict[tuple[Any, ...], int] = {}
                for extracted in output.issues:
                    text_fields = [
                        extracted.title,
                        extracted.category,
                        extracted.symptom,
                        *extracted.root_cause_hypotheses,
                        *(ref.note for ref in extracted.evidence),
                    ]
                    if (
                        not extracted.evidence
                        or not extracted.source_reader_ids
                        or any(
                            not set(ref.segment_ids).issubset(segment_ids)
                            for ref in extracted.evidence
                        )
                        or any(
                            pattern.search(value)
                            for pattern in profile_patterns
                            for value in text_fields
                        )
                        or any(
                            token in value.casefold()
                            for token in uuid_tokens
                            for value in text_fields
                        )
                    ):
                        raise ValueError
                    source_ids = []
                    for source in extracted.source_reader_ids:
                        candidates = set()
                        if source in profile_to_report_id:
                            candidates.add(profile_to_report_id[source])
                        try:
                            canonical_uuid = str(UUID(source))
                        except ValueError:
                            canonical_uuid = None
                        if canonical_uuid is not None:
                            candidates.update(uuid_to_report_ids.get(canonical_uuid, set()))
                        if len(candidates) != 1:
                            raise ValueError
                        source_ids.append(next(iter(candidates)))
                    title = " ".join(extracted.title.split())
                    category = " ".join(extracted.category.split()).lower()
                    symptom = " ".join(extracted.symptom.split())
                    locations = tuple(
                        sorted(
                            {
                                segment_id
                                for ref in extracted.evidence
                                for segment_id in ref.segment_ids
                            }
                        )
                    )
                    key = (title.casefold(), category, symptom.casefold(), locations)
                    if key in dedupe_indexes:
                        index = dedupe_indexes[key]
                        prior = deduped[index]
                        deduped[index] = prior.model_copy(
                            update={
                                "source_reader_ids": list(
                                    dict.fromkeys(prior.source_reader_ids + source_ids)
                                )
                            }
                        )
                        continue
                    dedupe_indexes[key] = len(deduped)
                    deduped.append(
                        extracted.model_copy(
                            update={
                                "issue_number": len(deduped) + 1,
                                "title": title,
                                "category": category,
                                "symptom": symptom,
                                "root_cause_hypotheses": [
                                    " ".join(value.split())
                                    for value in extracted.root_cause_hypotheses
                                ],
                                "source_reader_ids": list(dict.fromkeys(source_ids)),
                            }
                        )
                    )
                return deduped

            if panel_session.status == ReaderPanelStatus.INITIAL_REPORTS_LOCKED.value:
                panel_session.status = ReaderPanelStatus.ISSUE_EXTRACTION.value
                self._db.add(
                    WorkflowEvent(
                        workflow_run_id=panel_session.workflow_run_id,
                        event_type="reader_panel.issue_extraction_started",
                        node_name="issue_extraction",
                        payload={
                            "session_id": str(panel_session.id),
                            "status": panel_session.status,
                        },
                        event_sequence=None,
                    )
                )
            await self._db.commit()
            try:
                normalized = await self._invoke_provider(
                    session_id=panel_session.id,
                    phase=ReaderPanelInvocationPhase.ISSUE_EXTRACTION,
                    work_key="issues",
                    request=request,
                    invoke=panel_provider.extract_issues,
                    expected_type=ModeratorIssueExtractionOutput,
                    validate=normalize_extraction,
                )
            except _ReaderPanelWorkAlreadyCompleted:
                return await self.collect_initial_ballots(
                    session_id=session_id, provider=panel_provider
                )
            except (asyncio.CancelledError, _ReaderPanelWorkInProgressError):
                raise
            except Exception as exc:
                error_code, _ = self._safe_provider_error(exc)
                if error_code != ReaderPanelSafeError.INVALID_OUTPUT.value:
                    extraction_failure_reason = "issue_extraction_failed"
                normalized = None

            panel_session = await lock_session()
            fresh_runs = eligible(panel_session)
            if source_snapshot(panel_session, fresh_runs) != initial_source_snapshot:
                await self._db.commit()
                raise ReaderPanelInvalidStateError()
            issues = (
                (
                    await self._db.execute(
                        select(ReaderPanelIssue)
                        .where(ReaderPanelIssue.session_id == panel_session.id)
                        .order_by(ReaderPanelIssue.issue_number)
                    )
                )
                .scalars()
                .all()
            )
            if not issues and panel_session.status == ReaderPanelStatus.ISSUE_EXTRACTION.value:
                if normalized is None:
                    await self._settle_failed(panel_session, extraction_failure_reason)
                    await self._db.commit()
                    raise ReaderPanelInvalidStateError()
                for extracted in normalized:
                    issue = ReaderPanelIssue(
                        id=uuid4(),
                        session_id=panel_session.id,
                        issue_number=extracted.issue_number,
                        title=extracted.title,
                        category=extracted.category,
                        symptom=extracted.symptom,
                        root_cause_hypotheses=extracted.root_cause_hypotheses,
                        evidence=[ref.model_dump() for ref in extracted.evidence],
                        source_reader_ids=extracted.source_reader_ids,
                        target_audience_relevance=extracted.target_audience_relevance.value,
                        minority_risk=extracted.minority_risk,
                        discussion_status=extracted.discussion_status.value,
                    )
                    self._db.add(issue)
                    issues.append(issue)
                panel_session.status = ReaderPanelStatus.INITIAL_BALLOTING.value
                self._db.add(
                    WorkflowEvent(
                        workflow_run_id=panel_session.workflow_run_id,
                        event_type="reader_panel.issues_extracted",
                        node_name="initial_balloting",
                        payload={
                            "session_id": str(panel_session.id),
                            "status": panel_session.status,
                            "issue_count": len(issues),
                        },
                        event_sequence=None,
                    )
                )
            await self._db.commit()

        panel_session = await lock_session()
        eligible_runs = eligible(panel_session)
        if source_snapshot(panel_session, eligible_runs) != initial_source_snapshot:
            await self._db.commit()
            raise ReaderPanelInvalidStateError()
        if panel_session.status not in {
            ReaderPanelStatus.INITIAL_BALLOTING.value,
            ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value,
        } or (panel_session.initial_ballots_locked_at is not None) != (
            panel_session.status == ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value
        ):
            await self._db.commit()
            raise ReaderPanelInvalidStateError()
        runs = list(panel_session.reader_runs)
        issues = (
            (
                await self._db.execute(
                    select(ReaderPanelIssue)
                    .where(ReaderPanelIssue.session_id == panel_session.id)
                    .order_by(ReaderPanelIssue.issue_number)
                )
            )
            .scalars()
            .all()
        )
        ballots = (
            (
                await self._db.execute(
                    select(ReaderPanelBallot).where(
                        ReaderPanelBallot.session_id == panel_session.id,
                        ReaderPanelBallot.phase == "initial",
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_pairs = {(ballot.reader_run_id, ballot.issue_id) for ballot in ballots}
        await self._db.commit()
        for run in eligible_runs:
            for issue in issues:
                if (run.id, issue.id) in existing_pairs:
                    continue
                extracted = ExtractedIssueItem(
                    issue_number=issue.issue_number,
                    title=issue.title,
                    category=issue.category,
                    symptom=issue.symptom,
                    root_cause_hypotheses=issue.root_cause_hypotheses,
                    evidence=issue.evidence,
                    source_reader_ids=issue.source_reader_ids,
                    target_audience_relevance=issue.target_audience_relevance,
                    minority_risk=issue.minority_risk,
                    discussion_status=issue.discussion_status,
                )
                request = build_blind_ballot_request(
                    project_id=panel_session.project_id,
                    chapter_id=panel_session.chapter_id,
                    workflow_run_id=panel_session.workflow_run_id,
                    reader_profile_id=run.reader_profile_id,
                    issue=extracted,
                    manuscript_segments=segments,
                )
                valid_output = None

                def validate_ballot(output: ReaderBallotOutput) -> ReaderBallotOutput:
                    if (
                        output.issue_number != issue.issue_number
                        or not output.evidence
                        or any(
                            not set(ref.segment_ids).issubset(segment_ids)
                            for ref in output.evidence
                        )
                    ):
                        raise ValueError
                    return output

                try:
                    valid_output = await self._invoke_provider(
                        session_id=panel_session.id,
                        phase=ReaderPanelInvocationPhase.INITIAL_BALLOT,
                        work_key=f"reader:{run.id}:issue:{issue.issue_number}",
                        request=request,
                        invoke=panel_provider.generate_blind_ballot,
                        expected_type=ReaderBallotOutput,
                        validate=validate_ballot,
                    )
                except _ReaderPanelWorkAlreadyCompleted:
                    return await self.collect_initial_ballots(
                        session_id=session_id, provider=panel_provider
                    )
                except (asyncio.CancelledError, _ReaderPanelWorkInProgressError):
                    raise
                except Exception as exc:
                    error_code, _ = self._safe_provider_error(exc)
                    locked = await lock_session()
                    if error_code == ReaderPanelSafeError.UNKNOWN_COMMIT.value:
                        await self._settle_failed(locked, "provider_outcome_unknown")
                        await self._db.commit()
                        raise ReaderPanelInvalidStateError() from None
                    if not await self._eliminate_reader(
                        locked,
                        run.id,
                        error_code,
                        "initial_ballot_sample_below_minimum",
                    ):
                        raise ReaderPanelInvalidStateError() from None
                    break
                if valid_output is not None:
                    locked = await lock_session()
                    fresh_ballots = (
                        (
                            await self._db.execute(
                                select(ReaderPanelBallot).where(
                                    ReaderPanelBallot.session_id == locked.id,
                                    ReaderPanelBallot.phase == "initial",
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if not any(
                        ballot.reader_run_id == run.id and ballot.issue_id == issue.id
                        for ballot in fresh_ballots
                    ):
                        self._db.add(
                            ReaderPanelBallot(
                                id=uuid4(),
                                session_id=locked.id,
                                reader_run_id=run.id,
                                issue_id=issue.id,
                                phase="initial",
                                severity=valid_output.severity.value,
                                suggested_action=valid_output.suggested_action.value,
                                confidence=valid_output.confidence.value,
                                evidence=[ref.model_dump() for ref in valid_output.evidence],
                                position_changed=False,
                                change_reason=None,
                                remaining_disagreement=None,
                            )
                        )
                    await self._db.commit()

        panel_session = await lock_session()
        eligible_runs = eligible(panel_session)
        if source_snapshot(panel_session, eligible_runs) != initial_source_snapshot:
            await self._db.commit()
            raise ReaderPanelInvalidStateError()
        if panel_session.status not in {
            ReaderPanelStatus.INITIAL_BALLOTING.value,
            ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value,
        } or (panel_session.initial_ballots_locked_at is not None) != (
            panel_session.status == ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value
        ):
            await self._db.commit()
            raise ReaderPanelInvalidStateError()
        issues = (
            (
                await self._db.execute(
                    select(ReaderPanelIssue)
                    .where(ReaderPanelIssue.session_id == panel_session.id)
                    .order_by(ReaderPanelIssue.issue_number)
                )
            )
            .scalars()
            .all()
        )
        ballots = (
            (
                await self._db.execute(
                    select(ReaderPanelBallot).where(
                        ReaderPanelBallot.session_id == panel_session.id,
                        ReaderPanelBallot.phase == "initial",
                    )
                )
            )
            .scalars()
            .all()
        )
        actual_pairs = {(ballot.reader_run_id, ballot.issue_id) for ballot in ballots}
        expected_pairs = {(run.id, issue.id) for run in eligible_runs for issue in issues}
        complete = expected_pairs.issubset(actual_pairs)
        if complete and panel_session.initial_ballots_locked_at is None:
            panel_session.initial_ballots_locked_at = datetime.now(timezone.utc)
            panel_session.status = ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value
            self._db.add(
                WorkflowEvent(
                    workflow_run_id=panel_session.workflow_run_id,
                    event_type="reader_panel.initial_ballots_locked",
                    node_name="initial_ballots_locked",
                    payload={
                        "session_id": str(panel_session.id),
                        "status": panel_session.status,
                        "issue_count": len(issues),
                        "ballot_count": len(actual_pairs & expected_pairs),
                    },
                    event_sequence=None,
                )
            )
        elif (
            not complete and panel_session.status != ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value
        ):
            panel_session.status = ReaderPanelStatus.INITIAL_BALLOTING.value
            panel_session.degradation_reason = "initial_ballot_incomplete"
        await self._db.commit()
        return ReaderPanelSessionResult(
            session_id=panel_session.id,
            workflow_run_id=panel_session.workflow_run_id,
            status=panel_session.status,
            mode=panel_session.mode,
            planned_readers=len(runs),
            completed_readers=len(eligible_runs),
            initial_reports_locked=True,
            issue_count=len(issues),
            initial_ballot_count=len(actual_pairs & expected_pairs),
            initial_ballots_locked=complete,
            stale=panel_session.stale,
            degradation_reason=panel_session.degradation_reason,
            failure_reason=panel_session.failure_reason,
        )

    async def run_discussion_and_final_ballots(
        self,
        *,
        session_id: UUID,
        provider: Any = None,
    ) -> ReaderPanelSessionResult:
        """Runs issue-scoped discussion and immutable final ballots."""
        panel_provider = provider or DeterministicReaderPanelProvider(
            scenario=ReaderPanelFakeScenario.CLEAN
        )
        active_statuses = {
            ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value,
            ReaderPanelStatus.DISCUSSING.value,
            ReaderPanelStatus.FINAL_BALLOTING.value,
            ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value,
        }

        async def lock_session() -> ReaderPanelSession:
            stmt = (
                select(ReaderPanelSession)
                .where(ReaderPanelSession.id == session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            locked = (await self._db.execute(stmt)).scalars().first()
            if locked is None:
                raise ReaderPanelNotFoundError()
            await self._refresh_stale_flag(locked)
            return locked

        async def load_issues() -> list[ReaderPanelIssue]:
            rows = (
                (
                    await self._db.execute(
                        select(ReaderPanelIssue)
                        .where(ReaderPanelIssue.session_id == session_id)
                        .order_by(ReaderPanelIssue.issue_number)
                    )
                )
                .scalars()
                .all()
            )
            return [issue for issue in rows if issue.session_id == session_id]

        async def load_ballots() -> list[ReaderPanelBallot]:
            rows = (
                (
                    await self._db.execute(
                        select(ReaderPanelBallot).where(ReaderPanelBallot.session_id == session_id)
                    )
                )
                .scalars()
                .all()
            )
            return [ballot for ballot in rows if ballot.session_id == session_id]

        async def load_messages() -> list[ReaderPanelMessage]:
            rows = (
                (
                    await self._db.execute(
                        select(ReaderPanelMessage).where(
                            ReaderPanelMessage.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [message for message in rows if message.session_id == session_id]

        def as_issue_contract(issue: ReaderPanelIssue) -> ExtractedIssueItem:
            return ExtractedIssueItem(
                issue_number=issue.issue_number,
                title=issue.title,
                category=issue.category,
                symptom=issue.symptom,
                root_cause_hypotheses=issue.root_cause_hypotheses,
                evidence=issue.evidence,
                source_reader_ids=[],
                target_audience_relevance=issue.target_audience_relevance,
                minority_risk=issue.minority_risk,
                discussion_status=issue.discussion_status,
            )

        def references_are_scoped(
            texts: list[str | None],
            *,
            issue: ReaderPanelIssue,
            allowed_segment_ids: set[str],
            round_number: int,
            allowed_uuids: set[str],
            forbidden_profiles: set[str],
        ) -> bool:
            joined = "\n".join(text for text in texts if text)
            if any(
                match.group(0).lower() not in allowed_uuids
                for match in re.finditer(
                    r"(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])",
                    joined,
                    re.IGNORECASE,
                )
            ):
                return False
            if any(
                int(match.group(1)) != issue.issue_number
                for match in re.finditer(r"\bISSUE-(\d+)\b", joined, re.IGNORECASE)
            ):
                return False
            allowed_segments = {value.upper() for value in allowed_segment_ids}
            if any(
                match.group(0).upper() not in allowed_segments
                for match in re.finditer(r"(?<![\w-])S\d+(?![\w-])", joined, re.IGNORECASE)
            ):
                return False
            if any(
                match.group(1).upper() not in allowed_segments
                and match.group(1).lower() not in allowed_uuids
                and not re.fullmatch(r"(?:ISSUE|ROUND)-\d+", match.group(1), re.IGNORECASE)
                for match in re.finditer(r"\[([A-Za-z0-9_-]{1,64})\]", joined)
            ):
                return False
            if any(
                int(match.group(1)) not in {max(1, round_number - 1), round_number}
                for match in re.finditer(r"\bROUND-(\d+)\b", joined, re.IGNORECASE)
            ):
                return False
            return not any(
                re.search(
                    rf"(?<![\w-]){re.escape(profile)}(?![\w-])",
                    joined,
                    re.IGNORECASE,
                )
                for profile in forbidden_profiles
            )

        async def reserve_call(
            step: str,
            *,
            issue_id: UUID,
            round_number: int | None = None,
            turn_number: int | None = None,
            reader_run_id: UUID | None = None,
        ) -> str | None:
            locked = await lock_session()
            if locked.status == ReaderPanelStatus.CANCELLED.value:
                await self._db.commit()
                return "cancellation"
            messages = await load_messages()
            if round_number is not None and turn_number is not None:
                issues = await load_issues()
                runs = (
                    (
                        await self._db.execute(
                            select(ReaderRun).where(ReaderRun.session_id == session_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                if (
                    locked.status != ReaderPanelStatus.DISCUSSING.value
                    or not any(
                        issue.id == issue_id
                        and issue.session_id == session_id
                        and issue.discussion_status == "discussing"
                        for issue in issues
                    )
                    or (
                        reader_run_id is not None
                        and not any(
                            run.id == reader_run_id
                            and run.session_id == session_id
                            and run.status == "completed"
                            for run in runs
                        )
                    )
                    or any(
                        message.issue_id == issue_id
                        and message.round_number == round_number
                        and message.turn_number == turn_number
                        for message in messages
                    )
                ):
                    await self._db.commit()
                    return "work_complete"
            elif reader_run_id is not None:
                ballots = await load_ballots()
                issues = await load_issues()
                runs = (
                    (
                        await self._db.execute(
                            select(ReaderRun).where(ReaderRun.session_id == session_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                if (
                    locked.status != ReaderPanelStatus.FINAL_BALLOTING.value
                    or not any(
                        issue.id == issue_id
                        and issue.session_id == session_id
                        and issue.discussion_status in {"closed", "skipped"}
                        for issue in issues
                    )
                    or not any(
                        run.id == reader_run_id
                        and run.session_id == session_id
                        and run.status == "completed"
                        for run in runs
                    )
                    or any(
                        ballot.issue_id == issue_id
                        and ballot.reader_run_id == reader_run_id
                        and ballot.phase == "final"
                        for ballot in ballots
                    )
                ):
                    await self._db.commit()
                    return "work_complete"
            step_counter = int(locked.step_counter or 0)
            locked.step_counter = step_counter + 1
            locked.current_step = step
            await self._db.commit()
            return None

        async def close_issue(issue_id: UUID, issue_number: int, reason: str) -> None:
            locked = await lock_session()
            if locked.status == ReaderPanelStatus.CANCELLED.value:
                await self._db.commit()
                return
            issues = await load_issues()
            issue = next((item for item in issues if item.id == issue_id), None)
            if issue is not None and issue.discussion_status == "discussing":
                issue.discussion_status = "closed"
                self._db.add(
                    WorkflowEvent(
                        workflow_run_id=locked.workflow_run_id,
                        event_type="reader_panel.discussion_round_completed",
                        node_name="discussing",
                        payload={
                            "session_id": str(locked.id),
                            "status": locked.status,
                            "issue_number": issue_number,
                            "round_number": min(
                                int(locked.config_snapshot.get("max_rounds_per_issue", 1)),
                                max(
                                    (
                                        message.round_number
                                        for message in await load_messages()
                                        if message.issue_id == issue_id
                                    ),
                                    default=1,
                                ),
                            ),
                            "stop_reason": reason,
                        },
                        event_sequence=None,
                    )
                )
            await self._db.commit()

        panel_session = await lock_session()
        if panel_session.status == ReaderPanelStatus.CANCELLED.value:
            await self._db.commit()
            return ReaderPanelSessionResult(
                session_id=panel_session.id,
                workflow_run_id=panel_session.workflow_run_id,
                status=panel_session.status,
                mode=panel_session.mode,
            )
        if (
            panel_session.status not in active_statuses
            or panel_session.initial_ballots_locked_at is None
        ):
            await self._db.commit()
            raise ReaderPanelInvalidStateError()

        runs = (
            (await self._db.execute(select(ReaderRun).where(ReaderRun.session_id == session_id)))
            .scalars()
            .all()
        )
        eligible_runs = sorted(
            [run for run in runs if run.session_id == session_id and run.status == "completed"],
            key=lambda run: (run.reader_profile_id, str(run.id)),
        )
        issues = await load_issues()
        ballots = await load_ballots()
        initial_ballots = [ballot for ballot in ballots if ballot.phase == "initial"]
        doc_version = await self._db.get(DocumentVersion, panel_session.document_version_id)
        all_segments = (
            doc_version.metadata_.get("segments")
            if doc_version is not None and isinstance(doc_version.metadata_, dict)
            else None
        )
        if not isinstance(all_segments, dict):
            await self._db.commit()
            raise ReaderPanelInvalidStateError()

        issue_ids = {issue.id for issue in issues}
        run_ids = {run.id for run in eligible_runs}
        all_run_ids = {run.id for run in runs if run.session_id == session_id}
        for issue in issues:
            evidence = issue.evidence
            if not isinstance(evidence, list) or not evidence:
                await self._db.commit()
                raise ReaderPanelInvalidStateError()
            for ref in evidence:
                ref_ids = ref.get("segment_ids") if isinstance(ref, dict) else None
                if (
                    not isinstance(ref_ids, list)
                    or not ref_ids
                    or not set(ref_ids).issubset(all_segments)
                ):
                    await self._db.commit()
                    raise ReaderPanelInvalidStateError()

        expected_initial_pairs = {(run.id, issue.id) for run in eligible_runs for issue in issues}
        if any(
            ballot.session_id != session_id
            or ballot.reader_run_id not in all_run_ids
            or ballot.issue_id not in issue_ids
            or ballot.phase != "initial"
            for ballot in initial_ballots
        ):
            await self._db.commit()
            raise ReaderPanelInvalidStateError()
        for pair in expected_initial_pairs:
            matches = [
                ballot
                for ballot in initial_ballots
                if (ballot.reader_run_id, ballot.issue_id) == pair
            ]
            if len(matches) != 1:
                await self._db.commit()
                raise ReaderPanelInvalidStateError()
            evidence = matches[0].evidence
            if not isinstance(evidence, list) or not evidence:
                await self._db.commit()
                raise ReaderPanelInvalidStateError()
            for ref in evidence:
                ref_ids = ref.get("segment_ids") if isinstance(ref, dict) else None
                if (
                    not isinstance(ref_ids, list)
                    or not ref_ids
                    or not set(ref_ids).issubset(all_segments)
                ):
                    await self._db.commit()
                    raise ReaderPanelInvalidStateError()
        initial_by_pair = {
            (ballot.reader_run_id, ballot.issue_id): ballot for ballot in initial_ballots
        }
        turn_by_run = {
            run.id: turn
            for turn, run in enumerate(
                sorted(runs, key=lambda item: (item.reader_profile_id, str(item.id))), start=1
            )
        }
        severity_rank = {
            "critical": 4,
            "significant": 3,
            "minor": 2,
            "none": 1,
            "abstain": 0,
        }
        relevance_rank = {"high": 2, "medium": 1, "low": 0}
        ranked_issues = sorted(
            issues,
            key=lambda issue: (
                -int(issue.minority_risk),
                -max(
                    (
                        severity_rank.get(ballot.severity, 0)
                        for ballot in initial_ballots
                        if ballot.issue_id == issue.id
                    ),
                    default=0,
                ),
                -relevance_rank.get(issue.target_audience_relevance, 0),
                -len(set(issue.source_reader_ids)),
                -len(
                    {
                        ballot.suggested_action
                        for ballot in initial_ballots
                        if ballot.issue_id == issue.id and ballot.severity != "abstain"
                    }
                ),
                issue.issue_number,
                str(issue.id),
            ),
        )
        max_rounds = int(panel_session.config_snapshot.get("max_rounds_per_issue", 1))
        agenda = (
            ranked_issues[
                : max(0, int(panel_session.config_snapshot.get("max_discussion_issues", 0)))
            ]
            if max_rounds > 0
            else []
        )
        agenda_ids = {issue.id for issue in agenda}

        if panel_session.status == ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value:
            if agenda:
                panel_session.status = ReaderPanelStatus.DISCUSSING.value
                for issue in issues:
                    issue.discussion_status = "discussing" if issue.id in agenda_ids else "skipped"
                self._db.add(
                    WorkflowEvent(
                        workflow_run_id=panel_session.workflow_run_id,
                        event_type="reader_panel.discussion_started",
                        node_name="discussing",
                        payload={
                            "session_id": str(panel_session.id),
                            "status": panel_session.status,
                            "discussed_issue_count": len(agenda),
                        },
                        event_sequence=None,
                    )
                )
            else:
                panel_session.status = ReaderPanelStatus.FINAL_BALLOTING.value
                for issue in issues:
                    issue.discussion_status = "skipped"
                self._db.add(
                    WorkflowEvent(
                        workflow_run_id=panel_session.workflow_run_id,
                        event_type="reader_panel.discussion_completed",
                        node_name="final_balloting",
                        payload={
                            "session_id": str(panel_session.id),
                            "status": panel_session.status,
                            "discussed_issue_count": 0,
                        },
                        event_sequence=None,
                    )
                )
            await self._db.commit()
        else:
            await self._db.commit()

        for agenda_issue in agenda:
            if agenda_issue.discussion_status in {"closed", "skipped"}:
                continue
            allowed_segment_ids = {
                segment_id
                for evidence in agenda_issue.evidence
                for segment_id in evidence.get("segment_ids", [])
            }
            if not allowed_segment_ids or not allowed_segment_ids.issubset(all_segments):
                await close_issue(
                    agenda_issue.id, agenda_issue.issue_number, "invalid_issue_evidence"
                )
                continue
            issue_segments = {
                segment_id: all_segments[segment_id] for segment_id in sorted(allowed_segment_ids)
            }
            issue_contract = as_issue_contract(agenda_issue)
            for round_number in range(1, max_rounds + 1):
                fresh_issues = await load_issues()
                fresh_issue = next(
                    (issue for issue in fresh_issues if issue.id == agenda_issue.id), None
                )
                if fresh_issue is None or fresh_issue.discussion_status != "discussing":
                    await self._db.commit()
                    break
                messages = await load_messages()
                issue_messages = [
                    message for message in messages if message.issue_id == agenda_issue.id
                ]
                await self._db.commit()
                round_reader_messages: list[ReaderPanelMessage] = [
                    message
                    for message in issue_messages
                    if message.round_number == round_number
                    and message.speaker_type == "reader"
                    and message.reader_run_id in run_ids
                ]
                existing_turns = {message.turn_number for message in round_reader_messages}
                discussion_incomplete = False
                for run in eligible_runs:
                    turn_number = turn_by_run[run.id]
                    if turn_number in existing_turns:
                        continue
                    initial = initial_by_pair[(run.id, agenda_issue.id)]
                    scoped_initial_evidence = [
                        evidence
                        for evidence in initial.evidence
                        if set(evidence.get("segment_ids", [])).issubset(allowed_segment_ids)
                    ]
                    previous_summary = next(
                        (
                            message
                            for message in reversed(issue_messages)
                            if message.round_number == round_number - 1
                            and message.speaker_type == "moderator"
                        ),
                        None,
                    )
                    context_messages = (
                        [previous_summary] if previous_summary is not None else []
                    ) + sorted(
                        [
                            message
                            for message in issue_messages
                            if message.round_number == round_number
                            and message.speaker_type == "reader"
                        ],
                        key=lambda message: message.turn_number,
                    )
                    prior_payload = [
                        {
                            "round_number": message.round_number,
                            "turn_number": message.turn_number,
                            "speaker_type": message.speaker_type,
                            "stance": message.stance,
                            "claim": message.claim,
                            "evidence": message.evidence,
                            "concession": message.concession,
                            "proposed_action": message.proposed_action,
                            "novelty": message.novelty,
                        }
                        for message in context_messages
                    ]
                    request = ReaderDiscussionTurnRequest(
                        project_id=panel_session.project_id,
                        chapter_id=panel_session.chapter_id,
                        workflow_run_id=panel_session.workflow_run_id,
                        reader_profile_id=run.reader_profile_id,
                        issue=issue_contract,
                        round_number=round_number,
                        turn_number=turn_number,
                        prior_messages=prior_payload,
                        prior_ballot={
                            "issue_number": agenda_issue.issue_number,
                            "severity": initial.severity,
                            "suggested_action": initial.suggested_action,
                            "confidence": initial.confidence,
                            "evidence": scoped_initial_evidence,
                        },
                        manuscript_segments=issue_segments,
                    )
                    valid_output = None
                    stop_reason = await reserve_call(
                        f"discussion:{agenda_issue.issue_number}:{round_number}:{turn_number}",
                        issue_id=agenda_issue.id,
                        round_number=round_number,
                        turn_number=turn_number,
                        reader_run_id=run.id,
                    )
                    allowed_uuids = {
                        str(panel_session.project_id).lower(),
                        str(panel_session.chapter_id).lower(),
                        str(panel_session.workflow_run_id).lower(),
                        str(panel_session.id).lower(),
                        str(agenda_issue.id).lower(),
                    }

                    def validate_turn(
                        output: ReaderDiscussionTurnOutput,
                    ) -> ReaderDiscussionTurnOutput:
                        if (
                            not output.evidence
                            or any(
                                not set(ref.segment_ids).issubset(allowed_segment_ids)
                                for ref in output.evidence
                            )
                            or not references_are_scoped(
                                [
                                    output.claim,
                                    output.concession,
                                    output.proposed_action,
                                    *(ref.note for ref in output.evidence),
                                ],
                                issue=agenda_issue,
                                allowed_segment_ids=allowed_segment_ids,
                                round_number=round_number,
                                allowed_uuids=allowed_uuids,
                                forbidden_profiles={
                                    other.reader_profile_id for other in eligible_runs
                                },
                            )
                        ):
                            raise ValueError
                        return output

                    if stop_reason is None:
                        try:
                            valid_output = await self._invoke_provider(
                                session_id=panel_session.id,
                                phase=ReaderPanelInvocationPhase.DISCUSSION_TURN,
                                work_key=(
                                    f"issue:{agenda_issue.issue_number}:round:{round_number}:"
                                    f"reader:{run.id}"
                                ),
                                request=request,
                                invoke=panel_provider.generate_discussion_turn,
                                expected_type=ReaderDiscussionTurnOutput,
                                validate=validate_turn,
                            )
                        except _ReaderPanelWorkAlreadyCompleted:
                            return await self.run_discussion_and_final_ballots(
                                session_id=session_id, provider=panel_provider
                            )
                        except (asyncio.CancelledError, _ReaderPanelWorkInProgressError):
                            raise
                        except Exception as exc:
                            error_code, _ = self._safe_provider_error(exc)
                            locked = await lock_session()
                            if error_code == ReaderPanelSafeError.UNKNOWN_COMMIT.value:
                                await self._settle_failed(locked, "provider_outcome_unknown")
                                await self._db.commit()
                                raise ReaderPanelInvalidStateError() from None
                            if not await self._eliminate_reader(
                                locked,
                                run.id,
                                error_code,
                                "discussion_sample_below_minimum",
                            ):
                                raise ReaderPanelInvalidStateError() from None
                            return await self.run_discussion_and_final_ballots(
                                session_id=session_id, provider=panel_provider
                            )
                    if stop_reason == "work_complete":
                        continue
                    if stop_reason is not None:
                        await close_issue(agenda_issue.id, agenda_issue.issue_number, stop_reason)
                        discussion_incomplete = True
                        break
                    if valid_output is None:
                        discussion_incomplete = True
                        break
                    locked = await lock_session()
                    fresh_messages = await load_messages()
                    fresh_issues = await load_issues()
                    fresh_runs = (
                        (
                            await self._db.execute(
                                select(ReaderRun).where(ReaderRun.session_id == session_id)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    fresh_issue = next(
                        (issue for issue in fresh_issues if issue.id == agenda_issue.id),
                        None,
                    )
                    fresh_run = next((item for item in fresh_runs if item.id == run.id), None)
                    if (
                        locked.status == ReaderPanelStatus.DISCUSSING.value
                        and fresh_issue is not None
                        and fresh_issue.session_id == locked.id
                        and fresh_issue.discussion_status == "discussing"
                        and fresh_run is not None
                        and fresh_run.session_id == locked.id
                        and fresh_run.status == "completed"
                        and not any(
                            message.issue_id == agenda_issue.id
                            and message.round_number == round_number
                            and message.turn_number == turn_number
                            for message in fresh_messages
                        )
                    ):
                        self._db.add(
                            ReaderPanelMessage(
                                id=uuid4(),
                                session_id=locked.id,
                                issue_id=agenda_issue.id,
                                round_number=round_number,
                                turn_number=turn_number,
                                speaker_type="reader",
                                reader_run_id=run.id,
                                stance=valid_output.stance.value,
                                claim=valid_output.claim,
                                evidence=[ref.model_dump() for ref in valid_output.evidence],
                                concession=valid_output.concession,
                                proposed_action=valid_output.proposed_action,
                                novelty=valid_output.novelty.value,
                                idempotency_key=(
                                    f"reader-panel:{locked.id}:{agenda_issue.id}:"
                                    f"{round_number}:{turn_number}"
                                ),
                            )
                        )
                    await self._db.commit()
                    issue_messages = await load_messages()
                    issue_messages = [
                        message for message in issue_messages if message.issue_id == agenda_issue.id
                    ]
                    await self._db.commit()
                if discussion_incomplete:
                    break

                messages = await load_messages()
                round_reader_messages = sorted(
                    [
                        message
                        for message in messages
                        if message.issue_id == agenda_issue.id
                        and message.round_number == round_number
                        and message.speaker_type == "reader"
                        and message.reader_run_id in run_ids
                    ],
                    key=lambda message: message.turn_number,
                )
                summary_turn = len(runs) + 1
                existing_summary = next(
                    (
                        message
                        for message in messages
                        if message.issue_id == agenda_issue.id
                        and message.round_number == round_number
                        and message.turn_number == summary_turn
                    ),
                    None,
                )
                await self._db.commit()
                if len(round_reader_messages) != len(eligible_runs):
                    break
                if existing_summary is None:
                    summary_request = ModeratorDiscussionSummaryRequest(
                        project_id=panel_session.project_id,
                        chapter_id=panel_session.chapter_id,
                        workflow_run_id=panel_session.workflow_run_id,
                        issue=issue_contract,
                        round_number=round_number,
                        round_messages=[
                            {
                                "turn_number": message.turn_number,
                                "speaker_type": "reader",
                                "stance": message.stance,
                                "claim": message.claim,
                                "evidence": message.evidence,
                                "concession": message.concession,
                                "proposed_action": message.proposed_action,
                                "novelty": message.novelty,
                            }
                            for message in round_reader_messages
                        ],
                    )
                    summary_output = None
                    stop_reason = await reserve_call(
                        f"summary:{agenda_issue.issue_number}:{round_number}",
                        issue_id=agenda_issue.id,
                        round_number=round_number,
                        turn_number=summary_turn,
                    )

                    def validate_summary(
                        output: ModeratorDiscussionSummaryOutput,
                    ) -> ModeratorDiscussionSummaryOutput:
                        if not references_are_scoped(
                            [
                                output.round_summary,
                                *output.remaining_disagreements,
                                output.suggested_focus,
                            ],
                            issue=agenda_issue,
                            allowed_segment_ids=allowed_segment_ids,
                            round_number=round_number,
                            allowed_uuids={
                                str(panel_session.project_id).lower(),
                                str(panel_session.chapter_id).lower(),
                                str(panel_session.workflow_run_id).lower(),
                                str(panel_session.id).lower(),
                                str(agenda_issue.id).lower(),
                            },
                            forbidden_profiles={run.reader_profile_id for run in eligible_runs},
                        ):
                            raise ValueError
                        return output

                    if stop_reason is None:
                        try:
                            summary_output = await self._invoke_provider(
                                session_id=panel_session.id,
                                phase=ReaderPanelInvocationPhase.DISCUSSION_SUMMARY,
                                work_key=(
                                    f"issue:{agenda_issue.issue_number}:round:{round_number}"
                                ),
                                request=summary_request,
                                invoke=panel_provider.summarize_discussion,
                                expected_type=ModeratorDiscussionSummaryOutput,
                                validate=validate_summary,
                            )
                        except _ReaderPanelWorkAlreadyCompleted:
                            return await self.run_discussion_and_final_ballots(
                                session_id=session_id, provider=panel_provider
                            )
                        except (asyncio.CancelledError, _ReaderPanelWorkInProgressError):
                            raise
                        except Exception:
                            locked = await lock_session()
                            await self._settle_failed(locked, "discussion_summary_failed")
                            await self._db.commit()
                            raise ReaderPanelInvalidStateError() from None
                    if stop_reason == "work_complete":
                        break
                    if stop_reason is not None:
                        await close_issue(agenda_issue.id, agenda_issue.issue_number, stop_reason)
                        break
                    if summary_output is None:
                        break
                    if summary_output.is_consensus_reached:
                        round_stop = "convergence"
                    elif all(
                        message.novelty in {"repetition", "procedural"}
                        for message in round_reader_messages
                    ):
                        round_stop = "no_new_information"
                    elif round_number == max_rounds:
                        round_stop = "round_exhaustion"
                    else:
                        round_stop = "continue"
                    locked = await lock_session()
                    fresh_messages = await load_messages()
                    fresh_issues = await load_issues()
                    fresh_issue = next(
                        (issue for issue in fresh_issues if issue.id == agenda_issue.id), None
                    )
                    if (
                        locked.status == ReaderPanelStatus.DISCUSSING.value
                        and fresh_issue is not None
                        and fresh_issue.session_id == locked.id
                        and fresh_issue.discussion_status == "discussing"
                        and not any(
                            message.issue_id == agenda_issue.id
                            and message.round_number == round_number
                            and message.turn_number == summary_turn
                            for message in fresh_messages
                        )
                    ):
                        self._db.add(
                            ReaderPanelMessage(
                                id=uuid4(),
                                session_id=locked.id,
                                issue_id=agenda_issue.id,
                                round_number=round_number,
                                turn_number=summary_turn,
                                speaker_type="moderator",
                                reader_run_id=None,
                                stance=None,
                                claim=summary_output.round_summary,
                                evidence=[],
                                concession="; ".join(summary_output.remaining_disagreements)
                                or None,
                                proposed_action=summary_output.suggested_focus,
                                novelty="procedural",
                                idempotency_key=(
                                    f"reader-panel:{locked.id}:{agenda_issue.id}:"
                                    f"{round_number}:{summary_turn}"
                                ),
                            )
                        )
                        if round_stop != "continue":
                            fresh_issue.discussion_status = "closed"
                        self._db.add(
                            WorkflowEvent(
                                workflow_run_id=locked.workflow_run_id,
                                event_type="reader_panel.discussion_round_completed",
                                node_name="discussing",
                                payload={
                                    "session_id": str(locked.id),
                                    "status": locked.status,
                                    "issue_number": agenda_issue.issue_number,
                                    "round_number": round_number,
                                    "stop_reason": round_stop,
                                },
                                event_sequence=None,
                            )
                        )
                    await self._db.commit()
                    if round_stop != "continue":
                        break

        panel_session = await lock_session()
        issues = await load_issues()
        agenda_fresh = [issue for issue in issues if issue.id in agenda_ids]
        if panel_session.status == ReaderPanelStatus.DISCUSSING.value and all(
            issue.discussion_status in {"closed", "skipped"} for issue in agenda_fresh
        ):
            panel_session.status = ReaderPanelStatus.FINAL_BALLOTING.value
            self._db.add(
                WorkflowEvent(
                    workflow_run_id=panel_session.workflow_run_id,
                    event_type="reader_panel.discussion_completed",
                    node_name="final_balloting",
                    payload={
                        "session_id": str(panel_session.id),
                        "status": panel_session.status,
                        "discussed_issue_count": len(agenda_fresh),
                    },
                    event_sequence=None,
                )
            )
        await self._db.commit()

        if panel_session.status == ReaderPanelStatus.FINAL_BALLOTING.value:
            for agenda_issue in issues:
                allowed_segment_ids = {
                    segment_id
                    for evidence in agenda_issue.evidence
                    for segment_id in evidence.get("segment_ids", [])
                }
                issue_segments = {
                    segment_id: all_segments[segment_id]
                    for segment_id in sorted(allowed_segment_ids)
                }
                issue_contract = as_issue_contract(agenda_issue)
                for run in eligible_runs:
                    ballots = await load_ballots()
                    if any(
                        ballot.reader_run_id == run.id
                        and ballot.issue_id == agenda_issue.id
                        and ballot.phase == "final"
                        for ballot in ballots
                    ):
                        await self._db.commit()
                        continue
                    messages = await load_messages()
                    round_summaries = [
                        message.claim
                        for message in sorted(
                            messages,
                            key=lambda message: (
                                message.round_number,
                                message.turn_number,
                            ),
                        )
                        if message.issue_id == agenda_issue.id
                        and message.speaker_type == "moderator"
                    ][:10]
                    initial = initial_by_pair[(run.id, agenda_issue.id)]
                    scoped_initial_evidence = [
                        evidence
                        for evidence in initial.evidence
                        if set(evidence.get("segment_ids", [])).issubset(allowed_segment_ids)
                    ]
                    await self._db.commit()
                    request = ReaderFinalBallotRequest(
                        project_id=panel_session.project_id,
                        chapter_id=panel_session.chapter_id,
                        workflow_run_id=panel_session.workflow_run_id,
                        reader_profile_id=run.reader_profile_id,
                        issue=issue_contract,
                        round_summaries=round_summaries,
                        initial_ballot={
                            "issue_number": agenda_issue.issue_number,
                            "severity": initial.severity,
                            "suggested_action": initial.suggested_action,
                            "confidence": initial.confidence,
                            "evidence": scoped_initial_evidence,
                        },
                        manuscript_segments=issue_segments,
                    )
                    valid_output = None
                    stop_reason = await reserve_call(
                        f"final:{agenda_issue.issue_number}:{run.id}",
                        issue_id=agenda_issue.id,
                        reader_run_id=run.id,
                    )

                    def validate_final(
                        output: ReaderFinalBallotOutput,
                    ) -> ReaderFinalBallotOutput:
                        if (
                            output.issue_number != agenda_issue.issue_number
                            or not output.evidence
                            or any(
                                not set(ref.segment_ids).issubset(allowed_segment_ids)
                                for ref in output.evidence
                            )
                            or not references_are_scoped(
                                [
                                    output.change_reason,
                                    output.remaining_disagreement,
                                    *(ref.note for ref in output.evidence),
                                ],
                                issue=agenda_issue,
                                allowed_segment_ids=allowed_segment_ids,
                                round_number=max(
                                    (
                                        message.round_number
                                        for message in messages
                                        if message.issue_id == agenda_issue.id
                                    ),
                                    default=1,
                                ),
                                allowed_uuids={
                                    str(panel_session.project_id).lower(),
                                    str(panel_session.chapter_id).lower(),
                                    str(panel_session.workflow_run_id).lower(),
                                    str(panel_session.id).lower(),
                                    str(agenda_issue.id).lower(),
                                },
                                forbidden_profiles={
                                    other.reader_profile_id for other in eligible_runs
                                },
                            )
                        ):
                            raise ValueError
                        return output

                    if stop_reason is None:
                        try:
                            valid_output = await self._invoke_provider(
                                session_id=panel_session.id,
                                phase=ReaderPanelInvocationPhase.FINAL_BALLOT,
                                work_key=(f"issue:{agenda_issue.issue_number}:reader:{run.id}"),
                                request=request,
                                invoke=panel_provider.generate_final_ballot,
                                expected_type=ReaderFinalBallotOutput,
                                validate=validate_final,
                            )
                        except _ReaderPanelWorkAlreadyCompleted:
                            return await self.run_discussion_and_final_ballots(
                                session_id=session_id, provider=panel_provider
                            )
                        except (asyncio.CancelledError, _ReaderPanelWorkInProgressError):
                            raise
                        except Exception as exc:
                            error_code, _ = self._safe_provider_error(exc)
                            locked = await lock_session()
                            if error_code == ReaderPanelSafeError.UNKNOWN_COMMIT.value:
                                await self._settle_failed(locked, "provider_outcome_unknown")
                                await self._db.commit()
                                raise ReaderPanelInvalidStateError() from None
                            if not await self._eliminate_reader(
                                locked,
                                run.id,
                                error_code,
                                "final_ballot_sample_below_minimum",
                            ):
                                raise ReaderPanelInvalidStateError() from None
                            return await self.run_discussion_and_final_ballots(
                                session_id=session_id, provider=panel_provider
                            )
                    if valid_output is None:
                        continue
                    locked = await lock_session()
                    fresh_ballots = await load_ballots()
                    fresh_issues = await load_issues()
                    fresh_runs = (
                        (
                            await self._db.execute(
                                select(ReaderRun).where(ReaderRun.session_id == session_id)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    fresh_issue = next(
                        (item for item in fresh_issues if item.id == agenda_issue.id), None
                    )
                    fresh_run = next((item for item in fresh_runs if item.id == run.id), None)
                    if (
                        locked.status == ReaderPanelStatus.FINAL_BALLOTING.value
                        and fresh_issue is not None
                        and fresh_issue.session_id == locked.id
                        and fresh_issue.discussion_status in {"closed", "skipped"}
                        and fresh_run is not None
                        and fresh_run.session_id == locked.id
                        and fresh_run.status == "completed"
                        and (fresh_run.id, fresh_issue.id) in expected_initial_pairs
                        and not any(
                            ballot.reader_run_id == run.id
                            and ballot.issue_id == agenda_issue.id
                            and ballot.phase == "final"
                            for ballot in fresh_ballots
                        )
                    ):
                        self._db.add(
                            ReaderPanelBallot(
                                id=uuid4(),
                                session_id=locked.id,
                                reader_run_id=run.id,
                                issue_id=agenda_issue.id,
                                phase="final",
                                severity=valid_output.severity.value,
                                suggested_action=valid_output.suggested_action.value,
                                confidence=valid_output.confidence.value,
                                evidence=[ref.model_dump() for ref in valid_output.evidence],
                                position_changed=valid_output.position_changed,
                                change_reason=valid_output.change_reason,
                                remaining_disagreement=(valid_output.remaining_disagreement),
                            )
                        )
                    await self._db.commit()

        panel_session = await lock_session()
        ballots = await load_ballots()
        messages = await load_messages()
        final_ballots = [ballot for ballot in ballots if ballot.phase == "final"]
        expected_final_pairs = {(run.id, issue.id) for run in eligible_runs for issue in issues}
        actual_final_pairs = {(ballot.reader_run_id, ballot.issue_id) for ballot in final_ballots}
        final_complete = expected_final_pairs.issubset(actual_final_pairs)
        if (
            panel_session.status == ReaderPanelStatus.FINAL_BALLOTING.value
            and final_complete
            and panel_session.final_ballots_locked_at is None
        ):
            panel_session.status = ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value
            panel_session.final_ballots_locked_at = datetime.now(timezone.utc)
            panel_session.current_step = "final_ballots_locked"
            self._db.add(
                WorkflowEvent(
                    workflow_run_id=panel_session.workflow_run_id,
                    event_type="reader_panel.final_ballots_locked",
                    node_name="final_ballots_locked",
                    payload={
                        "session_id": str(panel_session.id),
                        "status": panel_session.status,
                        "issue_count": len(issues),
                        "ballot_count": len(actual_final_pairs & expected_final_pairs),
                    },
                    event_sequence=None,
                )
            )
        await self._db.commit()
        return ReaderPanelSessionResult(
            session_id=panel_session.id,
            workflow_run_id=panel_session.workflow_run_id,
            status=panel_session.status,
            mode=panel_session.mode,
            planned_readers=len(eligible_runs),
            completed_readers=len(eligible_runs),
            initial_reports_locked=True,
            issue_count=len(issues),
            initial_ballot_count=len(initial_ballots),
            initial_ballots_locked=True,
            discussion_message_count=len(messages),
            discussed_issue_count=len(agenda),
            final_ballot_count=len(actual_final_pairs & expected_final_pairs),
            final_ballots_locked=panel_session.final_ballots_locked_at is not None,
            stale=panel_session.stale,
            degradation_reason=panel_session.degradation_reason,
            failure_reason=panel_session.failure_reason,
        )

    async def generate_editor_handoff_report(
        self,
        *,
        session_id: UUID,
        provider: Any = None,
    ) -> ReaderPanelSessionResult:
        """Classifies locked final ballots and persists one non-approval editor handoff."""
        panel_provider = provider or DeterministicReaderPanelProvider(
            scenario=ReaderPanelFakeScenario.CLEAN
        )
        severity_values = {item.value for item in Severity}
        action_values = {item.value for item in SuggestedAction}
        confidence_values = {item.value for item in Confidence}

        async def load_rows(model: type[Any]) -> list[Any]:
            rows = (
                (
                    await self._db.execute(
                        select(model)
                        .where(model.session_id == session_id)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            return [row for row in rows if row.session_id == session_id]

        async def load_locked_snapshot() -> tuple[
            ReaderPanelSession,
            list[ReaderRun],
            list[ReaderInitialReport],
            list[ReaderPanelIssue],
            list[ReaderPanelBallot],
            DocumentVersion,
            WorkflowRun,
            tuple[Any, ...],
        ]:
            session_stmt = (
                select(ReaderPanelSession)
                .where(ReaderPanelSession.id == session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            session = (await self._db.execute(session_stmt)).scalars().first()
            if session is None:
                raise ReaderPanelNotFoundError()
            runs: list[ReaderRun] = await load_rows(ReaderRun)
            reports: list[ReaderInitialReport] = await load_rows(ReaderInitialReport)
            issues: list[ReaderPanelIssue] = sorted(
                await load_rows(ReaderPanelIssue), key=lambda issue: issue.issue_number
            )
            ballots: list[ReaderPanelBallot] = await load_rows(ReaderPanelBallot)
            version = await self._db.get(
                DocumentVersion,
                session.document_version_id,
                populate_existing=True,
            )
            document = await self._db.get(
                Document,
                session.document_id,
                populate_existing=True,
            )
            workflow_run = await self._db.get(
                WorkflowRun,
                session.workflow_run_id,
                populate_existing=True,
            )
            workflow_metadata = (
                workflow_run.metadata_
                if workflow_run is not None and isinstance(workflow_run.metadata_, dict)
                else {}
            )
            segments = (
                version.metadata_.get("segments")
                if version is not None and isinstance(version.metadata_, dict)
                else None
            )
            if (
                session.status
                not in {
                    ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value,
                    ReaderPanelStatus.REPORT_GENERATING.value,
                    ReaderPanelStatus.COMPLETED.value,
                    ReaderPanelStatus.DEGRADED_COMPLETED.value,
                }
                or session.final_ballots_locked_at is None
                or version is None
                or document is None
                or workflow_run is None
                or workflow_run.workflow_type != "reader_panel"
                or document.project_id != session.project_id
                or document.chapter_id != session.chapter_id
                or workflow_run.project_id != session.project_id
                or workflow_metadata.get("chapter_id") != str(session.chapter_id)
                or workflow_metadata.get("document_id") != str(session.document_id)
                or workflow_metadata.get("document_version_id") != str(session.document_version_id)
                or workflow_metadata.get("source_hash") != session.source_hash
                or workflow_metadata.get("mode") != session.mode
                or version.document_id != session.document_id
                or version.content_hash != session.source_hash
                or not isinstance(segments, dict)
                or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in segments.items()
                )
            ):
                raise ReaderPanelInvalidStateError()
            if document.current_version_id != session.document_version_id:
                session.stale = True
            if (
                session.status
                in {
                    ReaderPanelStatus.COMPLETED.value,
                    ReaderPanelStatus.DEGRADED_COMPLETED.value,
                }
                and session.review_report_id is None
            ):
                raise ReaderPanelInvalidStateError()
            if session.status in {
                ReaderPanelStatus.COMPLETED.value,
                ReaderPanelStatus.DEGRADED_COMPLETED.value,
            }:
                if (
                    workflow_run.status != session.status
                    or workflow_run.current_node != "completed"
                    or workflow_run.next_node is not None
                    or workflow_run.awaiting_user
                    or workflow_run.completed_at is None
                ):
                    raise ReaderPanelInvalidStateError()
            elif workflow_run.status != "running" or workflow_run.completed_at is not None:
                raise ReaderPanelInvalidStateError()
            run_ids = {run.id for run in runs}
            issue_ids = {issue.id for issue in issues}
            if (
                len(run_ids) != len(runs)
                or len(issue_ids) != len(issues)
                or len({issue.issue_number for issue in issues}) != len(issues)
            ):
                raise ReaderPanelInvalidStateError()
            eligible_runs = sorted(
                [run for run in runs if run.status == "completed"],
                key=lambda run: (run.reader_profile_id, str(run.id)),
            )
            reports_by_run: dict[UUID, list[ReaderInitialReport]] = {}
            for report in reports:
                reports_by_run.setdefault(report.reader_run_id, []).append(report)
            if (
                not eligible_runs
                or any(run_id not in run_ids for run_id in reports_by_run)
                or any(len(items) > 1 for items in reports_by_run.values())
                or any(
                    len(reports_by_run.get(run.id, [])) != 1
                    or not reports_by_run[run.id][0].locked
                    or reports_by_run[run.id][0].locked_at is None
                    for run in eligible_runs
                )
            ):
                raise ReaderPanelInvalidStateError()
            segment_ids = set(segments)

            def evidence_is_bound(evidence: Any) -> bool:
                return (
                    isinstance(evidence, list)
                    and bool(evidence)
                    and all(
                        isinstance(ref, dict)
                        and isinstance(ref.get("segment_ids"), list)
                        and bool(ref["segment_ids"])
                        and set(ref["segment_ids"]).issubset(segment_ids)
                        for ref in evidence
                    )
                )

            report_ids = {str(reports_by_run[run.id][0].id) for run in eligible_runs}
            if any(
                not evidence_is_bound(issue.evidence)
                or not set(issue.source_reader_ids).issubset(report_ids)
                for issue in issues
            ):
                raise ReaderPanelInvalidStateError()
            eligible_ids = {run.id for run in eligible_runs}
            expected_pairs = {(run.id, issue.id) for run in eligible_runs for issue in issues}
            phase_pairs: dict[str, list[tuple[UUID, UUID]]] = {
                "initial": [],
                "final": [],
            }
            for ballot in ballots:
                if (
                    ballot.reader_run_id not in eligible_ids
                    or ballot.issue_id not in issue_ids
                    or ballot.phase not in phase_pairs
                    or ballot.severity not in severity_values
                    or ballot.suggested_action not in action_values
                    or ballot.confidence not in confidence_values
                    or not evidence_is_bound(ballot.evidence)
                ):
                    raise ReaderPanelInvalidStateError()
                phase_pairs[ballot.phase].append((ballot.reader_run_id, ballot.issue_id))
            if any(
                len(pairs) != len(set(pairs)) or set(pairs) != expected_pairs
                for pairs in phase_pairs.values()
            ):
                raise ReaderPanelInvalidStateError()
            snapshot = (
                session.project_id,
                session.chapter_id,
                session.workflow_run_id,
                session.document_id,
                session.document_version_id,
                session.source_hash,
                session.final_ballots_locked_at,
                session.mode,
                session.degradation_reason,
                repr(session.config_snapshot),
                repr(session.model_snapshot),
                repr(session.prompt_snapshot),
                repr(session.target_audience),
                workflow_run.status,
                workflow_run.current_node,
                workflow_run.next_node,
                workflow_run.awaiting_user,
                workflow_run.completed_at,
                tuple(
                    (str(run.id), run.reader_profile_id, run.status, run.is_target_audience)
                    for run in sorted(runs, key=lambda item: str(item.id))
                ),
                tuple(
                    (
                        str(report.id),
                        str(report.reader_run_id),
                        report.locked,
                        str(report.locked_at),
                        report.overall_reaction,
                        report.continue_reading,
                        report.confidence,
                        repr(report.strengths),
                        repr(report.reactions),
                        repr(report.concerns),
                    )
                    for report in sorted(reports, key=lambda item: str(item.id))
                ),
                tuple(
                    (
                        str(issue.id),
                        issue.issue_number,
                        issue.title,
                        issue.category,
                        issue.symptom,
                        repr(issue.root_cause_hypotheses),
                        repr(issue.evidence),
                        repr(issue.source_reader_ids),
                        issue.target_audience_relevance,
                        issue.minority_risk,
                        issue.discussion_status,
                    )
                    for issue in issues
                ),
                tuple(
                    sorted(
                        (
                            str(ballot.reader_run_id),
                            str(ballot.issue_id),
                            ballot.phase,
                            ballot.severity,
                            ballot.suggested_action,
                            ballot.confidence,
                            repr(ballot.evidence),
                            ballot.position_changed,
                            ballot.change_reason,
                            ballot.remaining_disagreement,
                        )
                        for ballot in ballots
                    )
                ),
            )
            return session, runs, reports, issues, ballots, version, workflow_run, snapshot

        def classify(
            runs: list[ReaderRun],
            issues: list[ReaderPanelIssue],
            ballots: list[ReaderPanelBallot],
        ) -> list[dict[str, Any]]:
            run_by_id = {run.id: run for run in runs if run.status == "completed"}
            initial_by_pair = {
                (ballot.reader_run_id, ballot.issue_id): ballot
                for ballot in ballots
                if ballot.phase == "initial"
            }
            final_by_pair = {
                (ballot.reader_run_id, ballot.issue_id): ballot
                for ballot in ballots
                if ballot.phase == "final"
            }
            classified = []
            for issue in issues:
                final_rows = [
                    final_by_pair[(run_id, issue.id)] for run_id in sorted(run_by_id, key=str)
                ]
                minority_high_risk = any(
                    ballot.severity == Severity.CRITICAL.value
                    and ballot.confidence == Confidence.HIGH.value
                    for ballot in final_rows
                )
                result = classify_issue_consensus(
                    [
                        BallotVote(
                            reader_id=str(ballot.reader_run_id),
                            severity=ballot.severity,
                            suggested_action=ballot.suggested_action,
                            confidence=ballot.confidence,
                            is_target_audience=run_by_id[ballot.reader_run_id].is_target_audience,
                            has_fatal_risk=(
                                ballot.severity == Severity.CRITICAL.value
                                and ballot.confidence == Confidence.HIGH.value
                            ),
                            position_changed=ballot.position_changed,
                            change_reason=ballot.change_reason,
                        )
                        for ballot in final_rows
                    ]
                )
                raw_distribution = {
                    severity.value: result.severity_distribution.get(severity, 0)
                    for severity in Severity
                }
                target_distribution = {
                    severity.value: result.target_audience_distribution.get(severity, 0)
                    for severity in Severity
                }
                consensus_class = result.consensus_class.value
                priority = (
                    result.recommended_priority.value
                    if isinstance(result.recommended_priority, EditorHandoffDecision)
                    else str(result.recommended_priority)
                )
                if minority_high_risk:
                    priority = EditorHandoffDecision.MUST_FIX.value
                risk_flags = [flag.value for flag in result.risk_flags]
                if minority_high_risk and RiskFlag.MINORITY_HIGH_RISK.value not in risk_flags:
                    risk_flags.append(RiskFlag.MINORITY_HIGH_RISK.value)
                tally = {
                    "raw_distribution": raw_distribution,
                    "target_audience_distribution": target_distribution,
                    "valid_votes": result.valid_votes,
                    "total_votes": result.total_votes,
                    "target_audience_votes": result.target_audience_votes,
                    "risk_flags": sorted(risk_flags),
                }
                action_counts = {
                    action: sum(ballot.suggested_action == action for ballot in final_rows)
                    for action in action_values
                }
                top_action_count = max(action_counts.values())
                top_actions = sorted(
                    action for action, count in action_counts.items() if count == top_action_count
                )
                editor_action = (
                    top_actions[0] if len(top_actions) == 1 else SuggestedAction.MANUAL_REVIEW.value
                )
                movements = []
                for index, run in enumerate(
                    sorted(
                        run_by_id.values(), key=lambda item: (item.reader_profile_id, str(item.id))
                    ),
                    start=1,
                ):
                    initial = initial_by_pair[(run.id, issue.id)]
                    final = final_by_pair[(run.id, issue.id)]
                    movements.append(
                        {
                            "reader": f"reader_{index}",
                            "is_target_audience": run.is_target_audience,
                            "initial_severity": initial.severity,
                            "final_severity": final.severity,
                            "initial_action": initial.suggested_action,
                            "final_action": final.suggested_action,
                            "position_changed": final.position_changed,
                        }
                    )
                classified.append(
                    {
                        "issue": issue,
                        "consensus_class": consensus_class,
                        "recommended_priority": priority,
                        "final_tally": tally,
                        "movements": movements,
                        "remaining_disagreements": sorted(
                            {
                                ballot.remaining_disagreement
                                for ballot in final_rows
                                if ballot.remaining_disagreement
                            }
                        ),
                        "minority_high_risk": minority_high_risk,
                        "editor_action": editor_action,
                        "suggested_actions": sorted(
                            {
                                ballot.suggested_action
                                for ballot in final_rows
                                if ballot.suggested_action != SuggestedAction.KEEP.value
                            }
                        ),
                    }
                )
            priority_rank = {
                EditorHandoffDecision.MUST_FIX.value: 0,
                EditorHandoffDecision.MANUAL_REVIEW.value: 1,
                EditorHandoffDecision.EXPERIMENT.value: 2,
                EditorHandoffDecision.KEEP.value: 3,
                EditorHandoffDecision.REJECTED.value: 4,
            }
            return sorted(
                classified,
                key=lambda item: (
                    priority_rank.get(item["recommended_priority"], 99),
                    item["issue"].issue_number,
                ),
            )

        async def existing_report_id(session: ReaderPanelSession) -> UUID | None:
            if session.review_report_id is None:
                return None
            report = await self._db.get(
                ReviewReport,
                session.review_report_id,
                populate_existing=True,
            )
            raw_report = report.raw_report if report is not None else None
            if (
                report is None
                or report.project_id != session.project_id
                or report.chapter_id != session.chapter_id
                or report.workflow_run_id != session.workflow_run_id
                or report.target_document_id != session.document_id
                or report.target_version_id != session.document_version_id
                or report.passed
                or report.review_mode != "reader_panel"
                or report.reviewer_agent_role != "moderator_agent"
                or report.report_document_id is not None
                or not isinstance(raw_report, dict)
                or raw_report.get("schema_version") != "reader_panel.editor_handoff.v1"
                or raw_report.get("source_document_id") != str(session.document_id)
                or raw_report.get("source_version_id") != str(session.document_version_id)
                or raw_report.get("source_hash") != session.source_hash
                or raw_report.get("automatic_application_allowed") is not False
            ):
                raise ReaderPanelInvalidStateError()
            return report.id

        (
            session,
            runs,
            reports,
            issues,
            ballots,
            _,
            _,
            source_snapshot,
        ) = await load_locked_snapshot()
        source_was_stale = session.stale
        report_id = await existing_report_id(session)
        if report_id is not None:
            await self._db.commit()
            return ReaderPanelSessionResult(
                session_id=session.id,
                workflow_run_id=session.workflow_run_id,
                status=session.status,
                mode=session.mode,
                review_report_id=report_id,
                stale=session.stale,
                degradation_reason=session.degradation_reason,
            )
        classified = classify(runs, issues, ballots)
        by_number = {item["issue"].issue_number: item for item in classified}
        report_by_run = {report.reader_run_id: report for report in reports}
        eligible_runs = sorted(
            [run for run in runs if run.status == "completed"],
            key=lambda run: (run.reader_profile_id, str(run.id)),
        )
        initial_reports = {
            f"reader_{index}": {
                "overall_reaction": report_by_run[run.id].overall_reaction,
                "continue_reading": report_by_run[run.id].continue_reading,
                "confidence": report_by_run[run.id].confidence,
                "strengths": report_by_run[run.id].strengths,
                "reactions": report_by_run[run.id].reactions,
                "concerns": report_by_run[run.id].concerns,
            }
            for index, run in enumerate(eligible_runs, start=1)
        }

        def extracted_issue(item: dict[str, Any]) -> ExtractedIssueItem:
            issue = item["issue"]
            try:
                return ExtractedIssueItem(
                    issue_number=issue.issue_number,
                    title=issue.title,
                    category=issue.category,
                    symptom=issue.symptom,
                    root_cause_hypotheses=issue.root_cause_hypotheses,
                    evidence=issue.evidence,
                    source_reader_ids=[],
                    target_audience_relevance=issue.target_audience_relevance,
                    minority_risk=item["minority_high_risk"],
                    discussion_status=issue.discussion_status,
                )
            except Exception:
                raise ReaderPanelInvalidStateError() from None

        def synthesis_request(**values: Any) -> ModeratorReportSynthesisRequest:
            try:
                return ModeratorReportSynthesisRequest(**values)
            except Exception:
                raise ReaderPanelInvalidStateError() from None

        request = synthesis_request(
            project_id=session.project_id,
            chapter_id=session.chapter_id,
            workflow_run_id=session.workflow_run_id,
            initial_reports=initial_reports,
            extracted_issues=[extracted_issue(item) for item in classified],
            final_consensus_results={
                number: {
                    "consensus_class": item["consensus_class"],
                    "recommended_priority": item["recommended_priority"],
                    "suggested_action": (item["editor_action"]),
                    **item["final_tally"],
                }
                for number, item in by_number.items()
            },
            minority_risk_issues=[
                number for number, item in by_number.items() if item["minority_high_risk"]
            ],
        )
        session.status = ReaderPanelStatus.REPORT_GENERATING.value
        session.current_step = "report_generating"
        await self._db.commit()
        output_received = False
        try:
            output = await self._invoke_provider(
                session_id=session.id,
                phase=ReaderPanelInvocationPhase.REPORT_SYNTHESIS,
                work_key="editor_handoff",
                request=request,
                invoke=panel_provider.synthesize_report,
                expected_type=ModeratorReportSynthesisOutput,
            )
            output_received = True
            output = ModeratorReportSynthesisOutput.model_validate(output.model_dump(mode="python"))

            def references_are_scoped(
                texts: list[str],
                *,
                issue_numbers: set[int],
                segment_ids: set[str],
            ) -> bool:
                joined = "\n".join(texts)
                if re.search(
                    r"(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])",
                    joined,
                    re.IGNORECASE,
                ):
                    return False
                if any(
                    int(match.group(1)) not in issue_numbers
                    for match in re.finditer(r"\bISSUE-(\d+)\b", joined, re.IGNORECASE)
                ):
                    return False
                allowed_segments = {segment_id.upper() for segment_id in segment_ids}
                if any(
                    match.group(0).upper() not in allowed_segments
                    for match in re.finditer(r"(?<![\w-])S\d+(?![\w-])", joined, re.IGNORECASE)
                ):
                    return False
                for match in re.finditer(r"\[([A-Za-z0-9_-]{1,64})\]", joined):
                    token = match.group(1)
                    issue_match = re.fullmatch(r"ISSUE-(\d+)", token, re.IGNORECASE)
                    if token.upper() not in allowed_segments and (
                        issue_match is None or int(issue_match.group(1)) not in issue_numbers
                    ):
                        return False
                return True

            def canonical_segment_ids(item: dict[str, Any]) -> list[str]:
                return list(
                    dict.fromkeys(
                        segment_id
                        for ref in item["issue"].evidence
                        for segment_id in ref["segment_ids"]
                    )
                )

            all_segments = {
                segment_id
                for item in classified
                for ref in item["issue"].evidence
                for segment_id in ref["segment_ids"]
            }
            if not references_are_scoped(
                [output.executive_summary, output.target_audience_appeal],
                issue_numbers=set(by_number),
                segment_ids=all_segments,
            ):
                raise ValueError("summary references")
            findings = {finding.issue_number: finding for finding in output.key_findings}
            if len(findings) != len(output.key_findings) or set(findings) != set(by_number):
                raise ValueError("findings")
            for number, finding in findings.items():
                authoritative = by_number[number]
                issue = authoritative["issue"]
                allowed_segments = {
                    segment_id for ref in issue.evidence for segment_id in ref["segment_ids"]
                }
                if (
                    finding.title != issue.title
                    or finding.consensus_class.value != authoritative["consensus_class"]
                    or finding.recommended_priority.value != authoritative["recommended_priority"]
                    or not finding.evidence
                    or any(
                        not set(ref.segment_ids).issubset(allowed_segments)
                        for ref in finding.evidence
                    )
                    or not references_are_scoped(
                        [finding.summary, *(ref.note for ref in finding.evidence)],
                        issue_numbers={number},
                        segment_ids=allowed_segments,
                    )
                ):
                    raise ValueError("finding binding")
            actionable = [
                item for item in classified if item["editor_action"] != SuggestedAction.KEEP.value
            ]
            if len(output.actionable_recommendations) != len(actionable):
                raise ValueError("recommendation count")
            validated_recommendations = list(
                zip(actionable, output.actionable_recommendations, strict=True)
            )
            for authoritative, recommendation in validated_recommendations:
                issue = authoritative["issue"]
                segment_ids = canonical_segment_ids(authoritative)
                if not references_are_scoped(
                    [recommendation.instruction],
                    issue_numbers={issue.issue_number},
                    segment_ids=set(segment_ids),
                ):
                    raise ValueError("recommendation references")
                if (
                    recommendation.priority.value != authoritative["recommended_priority"]
                    or recommendation.suggested_action.value != authoritative["editor_action"]
                    or recommendation.target_segment_ids != segment_ids
                ):
                    raise ValueError("recommendation binding")
        except _ReaderPanelWorkAlreadyCompleted:
            completed, *_ = await load_locked_snapshot()
            report_id = await existing_report_id(completed)
            if report_id is None:
                await self._db.commit()
                raise ReaderPanelInvalidStateError() from None
            await self._db.commit()
            return ReaderPanelSessionResult(
                session_id=completed.id,
                workflow_run_id=completed.workflow_run_id,
                status=completed.status,
                mode=completed.mode,
                review_report_id=report_id,
                stale=completed.stale,
                degradation_reason=completed.degradation_reason,
            )
        except (asyncio.CancelledError, _ReaderPanelWorkInProgressError):
            raise
        except Exception:
            if output_received:
                await self._reject_report_synthesis_output(session.id)
            failed, *_ = await load_locked_snapshot()
            report_id = await existing_report_id(failed)
            if report_id is not None:
                await self._db.commit()
                return ReaderPanelSessionResult(
                    session_id=failed.id,
                    workflow_run_id=failed.workflow_run_id,
                    status=failed.status,
                    mode=failed.mode,
                    review_report_id=report_id,
                    stale=failed.stale,
                    degradation_reason=failed.degradation_reason,
                )
            if not output_received:
                await self._settle_failed(failed, "report_synthesis_failed")
            elif failed.review_report_id is None:
                failed.status = ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value
                failed.current_step = "final_ballots_locked"
            await self._db.commit()
            raise ReaderPanelInvalidStateError() from None

        (
            fresh_session,
            fresh_runs,
            fresh_reports,
            fresh_issues,
            fresh_ballots,
            _,
            fresh_workflow,
            fresh_snapshot,
        ) = await load_locked_snapshot()
        report_id = await existing_report_id(fresh_session)
        if report_id is not None:
            await self._db.commit()
            return ReaderPanelSessionResult(
                session_id=fresh_session.id,
                workflow_run_id=fresh_session.workflow_run_id,
                status=fresh_session.status,
                mode=fresh_session.mode,
                review_report_id=report_id,
                stale=fresh_session.stale,
                degradation_reason=fresh_session.degradation_reason,
            )
        if fresh_snapshot != source_snapshot or (source_was_stale and not fresh_session.stale):
            fresh_session.status = ReaderPanelStatus.FINAL_BALLOTS_LOCKED.value
            fresh_session.current_step = "final_ballots_locked"
            await self._db.commit()
            raise ReaderPanelInvalidStateError()
        classified = classify(fresh_runs, fresh_issues, fresh_ballots)
        requested_readers = len(fresh_runs)
        valid_readers = sum(run.status == "completed" for run in fresh_runs)
        failed_readers = requested_readers - valid_readers
        degraded = bool(failed_readers or fresh_session.degradation_reason)
        degradation_reasons = [fresh_session.degradation_reason]
        if failed_readers:
            degradation_reasons.append("reader_sample_degraded")
        degradation_reason = (
            " ".join(dict.fromkeys(reason for reason in degradation_reasons if reason)) or None
        )
        fresh_session.degradation_reason = degradation_reason
        report_issues = []
        for item in classified:
            issue = item["issue"]
            issue.consensus_class = item["consensus_class"]
            issue.recommended_priority = item["recommended_priority"]
            issue.final_tally = item["final_tally"]
            finding = findings[issue.issue_number]
            report_issues.append(
                {
                    "issue_number": issue.issue_number,
                    "title": issue.title,
                    "category": issue.category,
                    "symptom": issue.symptom,
                    "consensus_class": item["consensus_class"],
                    "recommended_priority": item["recommended_priority"],
                    "final_tally": item["final_tally"],
                    "evidence": issue.evidence,
                    "initial_to_final_movement": item["movements"],
                    "remaining_disagreements": item["remaining_disagreements"],
                    "minority_findings": (
                        [RiskFlag.MINORITY_HIGH_RISK.value] if item["minority_high_risk"] else []
                    ),
                    "suggested_editorial_actions": item["suggested_actions"],
                    "moderator_summary": finding.summary,
                }
            )
        raw_report = {
            "schema_version": "reader_panel.editor_handoff.v1",
            "source_document_id": str(fresh_session.document_id),
            "source_version_id": str(fresh_session.document_version_id),
            "source_hash": fresh_session.source_hash,
            "mode": fresh_session.mode,
            "target_audience": fresh_session.target_audience,
            "automatic_application_allowed": False,
            "stale": fresh_session.stale,
            "sample": {
                "requested": requested_readers,
                "valid": valid_readers,
                "failed": failed_readers,
                "complete": failed_readers == 0,
                "degradation_reason": degradation_reason,
            },
            "issues": report_issues,
            "minority_issue_numbers": [
                item["issue"].issue_number for item in classified if item["minority_high_risk"]
            ],
            "moderator_wording": {
                "executive_summary": output.executive_summary,
                "target_audience_appeal": output.target_audience_appeal,
            },
            "provider_usage": {
                "report_synthesis_calls": 1,
                "total_panel_calls": None,
                "input_tokens": None,
                "output_tokens": None,
            },
        }
        warnings = []
        if fresh_session.stale:
            warnings.append("Source version is stale; automatic application is prohibited.")
        if failed_readers:
            warnings.append(
                f"Panel completed with {failed_readers} unavailable reader(s); this is not a full panel."
            )
        warnings.extend(
            f"ISSUE-{item['issue'].issue_number} retains a high-risk minority finding."
            for item in classified
            if item["minority_high_risk"]
        )
        fresh_actionable = [
            item for item in classified if item["editor_action"] != SuggestedAction.KEEP.value
        ]
        suggested_actions = [
            {
                "priority": item["recommended_priority"],
                "target_segment_ids": canonical_segment_ids(item),
                "suggested_action": item["editor_action"],
                "instruction": recommendation.instruction,
            }
            for item, recommendation in zip(
                fresh_actionable,
                output.actionable_recommendations,
                strict=True,
            )
        ]
        review_report = ReviewReport(
            id=uuid4(),
            project_id=fresh_session.project_id,
            chapter_id=fresh_session.chapter_id,
            workflow_run_id=fresh_session.workflow_run_id,
            review_mode="reader_panel",
            reviewer_agent_role="moderator_agent",
            target_document_id=fresh_session.document_id,
            target_version_id=fresh_session.document_version_id,
            passed=False,
            summary=output.executive_summary,
            blocking_issues=[
                {
                    "issue_number": item["issue"].issue_number,
                    "title": item["issue"].title,
                }
                for item in classified
                if item["recommended_priority"] == EditorHandoffDecision.MUST_FIX.value
            ],
            warnings=warnings,
            notes=[finding.summary for finding in output.key_findings],
            suggested_actions=suggested_actions,
            raw_report=raw_report,
            report_document_id=None,
        )
        self._db.add(review_report)
        fresh_session.review_report_id = review_report.id
        fresh_session.status = (
            ReaderPanelStatus.DEGRADED_COMPLETED.value
            if degraded
            else ReaderPanelStatus.COMPLETED.value
        )
        completed_at = (
            fresh_session.completed_at or fresh_workflow.completed_at or datetime.now(timezone.utc)
        )
        fresh_session.completed_at = completed_at
        fresh_session.current_step = "completed"
        fresh_workflow.status = fresh_session.status
        fresh_workflow.current_node = "completed"
        fresh_workflow.next_node = None
        fresh_workflow.awaiting_user = False
        fresh_workflow.completed_at = completed_at
        self._db.add(
            WorkflowEvent(
                workflow_run_id=fresh_session.workflow_run_id,
                event_type="reader_panel.report_completed",
                node_name="completed",
                payload={
                    "session_id": str(fresh_session.id),
                    "review_report_id": str(review_report.id),
                    "status": fresh_session.status,
                    "issue_count": len(classified),
                    "valid_reader_count": valid_readers,
                    "failed_reader_count": failed_readers,
                },
                event_sequence=None,
            )
        )
        await self._db.commit()
        return ReaderPanelSessionResult(
            session_id=fresh_session.id,
            workflow_run_id=fresh_session.workflow_run_id,
            status=fresh_session.status,
            mode=fresh_session.mode,
            planned_readers=requested_readers,
            completed_readers=valid_readers,
            initial_reports_locked=True,
            issue_count=len(classified),
            initial_ballot_count=len([b for b in fresh_ballots if b.phase == "initial"]),
            initial_ballots_locked=True,
            final_ballot_count=len([b for b in fresh_ballots if b.phase == "final"]),
            final_ballots_locked=True,
            review_report_id=review_report.id,
            stale=fresh_session.stale,
            degradation_reason=degradation_reason,
        )

    async def reconcile_stale_status(self, *, session_id: UUID) -> bool:
        """Reconciles staleness by checking if the manuscript current version has changed."""
        stmt = (
            select(ReaderPanelSession)
            .options(selectinload(ReaderPanelSession.document))
            .where(ReaderPanelSession.id == session_id)
        )
        panel_session = (await self._db.execute(stmt)).scalars().first()
        if panel_session is None or panel_session.document is None:
            return False

        if panel_session.document.current_version_id != panel_session.document_version_id:
            panel_session.stale = True
            await self._db.commit()
            return True
        return False
