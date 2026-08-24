"""Domain service orchestrating version-bound Reader Panel initialization and cold-reading collection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any
from uuid import UUID, uuid4

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.reader_panel_agents import build_blind_ballot_request, build_cold_read_request
from app.agents.reader_panel_contracts import (
    ExtractedIssueItem,
    ModeratorIssueExtractionOutput,
    ModeratorIssueExtractionRequest,
    ReaderBallotOutput,
    ReaderInitialReadingOutput,
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
    WorkflowEvent,
    WorkflowRun,
)
from app.models.reader_panel import (
    ReaderInitialReport,
    ReaderPanelBallot,
    ReaderPanelIssue,
    ReaderPanelSession,
    ReaderRun,
)
from app.workflows.reader_panel import (
    PanelMode,
    ReaderPanelConfig,
    ReaderPanelStatus,
    get_mode_preset_config,
    is_mode_off,
)


class ReaderPanelServiceError(AppError):
    """Base error for reader panel service failures."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "reader_panel_service_error"
    default_message = "Reader panel service error."


def _sanitize_error_message(exc: Exception) -> str:
    """Sanitizes exception messages to prevent leaking internal traces or credentials."""
    msg = str(exc).strip()
    if not msg:
        return type(exc).__name__
    lower = msg.lower()
    for token in ("password", "token", "key", "secret", "bearer", "postgresql://", "mysql://"):
        if token in lower:
            return f"Provider invocation error: {type(exc).__name__} (redacted)"
    return msg[:500]


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


@dataclass(frozen=True)
class ReaderPanelSessionResult:
    session_id: UUID | None
    workflow_run_id: UUID | None
    status: str
    mode: str
    is_noop: bool = False
    planned_readers: int = 0
    completed_readers: int = 0
    initial_reports_locked: bool = False
    issue_count: int = 0
    initial_ballot_count: int = 0
    initial_ballots_locked: bool = False
    stale: bool = False
    degradation_reason: str | None = None
    failure_reason: str | None = None
    reports: list[dict] = field(default_factory=list)
    message: str | None = None


class ReaderPanelService:
    """Service managing reader panel lifecycle, immutable version binding, and cold-reading sample collection."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def initialize_session(
        self,
        *,
        project_id: UUID,
        chapter_id: UUID,
        mode: PanelMode | str = PanelMode.STANDARD,
        config: ReaderPanelConfig | None = None,
        test_goals: list[str] | None = None,
        target_audience: list[str] | None = None,
        custom_profile_ids: list[str] | None = None,
    ) -> ReaderPanelSessionResult:
        """Initializes a version-bound Reader Panel session and its individual ReaderRun slots."""
        if is_mode_off(mode):
            return ReaderPanelSessionResult(
                session_id=None,
                workflow_run_id=None,
                status="off",
                mode="off",
                is_noop=True,
                message="Reader panel is disabled (mode=off)",
            )

        panel_mode = PanelMode(mode.lower()) if isinstance(mode, str) else mode
        panel_config = config or get_mode_preset_config(panel_mode)

        # 1. Resolve project, chapter, document, and immutable version
        project = await self._db.get(Project, project_id)
        if project is None:
            raise ReaderPanelNotFoundError()

        chapter = await self._db.get(Chapter, chapter_id)
        if chapter is None or chapter.project_id != project_id:
            raise ReaderPanelNotFoundError()

        doc_stmt = (
            select(Document)
            .where(
                Document.project_id == project_id,
                Document.chapter_id == chapter_id,
            )
            .order_by(Document.created_at.desc())
        )
        doc = (await self._db.execute(doc_stmt)).scalars().first()
        if doc is None or doc.current_version_id is None:
            raise ReaderPanelInvalidStateError()

        doc_version = await self._db.get(DocumentVersion, doc.current_version_id)
        if doc_version is None or not doc_version.content_hash:
            raise ReaderPanelInvalidStateError()

        # 2. Check for an existing active session on this exact version to preserve idempotency
        existing_stmt = select(ReaderPanelSession).where(
            ReaderPanelSession.project_id == project_id,
            ReaderPanelSession.chapter_id == chapter_id,
            ReaderPanelSession.document_version_id == doc_version.id,
            ReaderPanelSession.source_hash == doc_version.content_hash,
            ReaderPanelSession.status.not_in(
                [ReaderPanelStatus.CANCELLED.value, ReaderPanelStatus.FAILED.value]
            ),
        )
        existing_session = (await self._db.execute(existing_stmt)).scalars().first()
        if existing_session is not None:
            runs_stmt = select(ReaderRun).where(ReaderRun.session_id == existing_session.id)
            runs = (await self._db.execute(runs_stmt)).scalars().all()
            completed_runs = [r for r in runs if r.status == "completed"]
            return ReaderPanelSessionResult(
                session_id=existing_session.id,
                workflow_run_id=existing_session.workflow_run_id,
                status=existing_session.status,
                mode=existing_session.mode,
                is_noop=False,
                planned_readers=len(runs),
                completed_readers=len(completed_runs),
                initial_reports_locked=existing_session.initial_reports_locked_at is not None,
                stale=existing_session.stale,
                degradation_reason=existing_session.degradation_reason,
                failure_reason=existing_session.failure_reason,
            )

        # 3. Create chapter-scoped workflow run
        workflow_run = WorkflowRun(
            project_id=project_id,
            workflow_type="reader_panel",
            status="running",
            metadata_={
                "chapter_id": str(chapter_id),
                "document_id": str(doc.id),
                "document_version_id": str(doc_version.id),
                "source_hash": doc_version.content_hash,
                "mode": panel_mode.value,
            },
        )
        self._db.add(workflow_run)
        await self._db.flush()

        # 4. Resolve audience and goals
        project_meta = project.metadata_ if isinstance(project.metadata_, dict) else {}
        final_target_audience = (
            target_audience
            if target_audience is not None
            else list(project_meta.get("target_audience", []))
        )
        final_test_goals = test_goals or []
        profile_ids = custom_profile_ids or list(panel_config.reader_profile_ids)

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
            stale=False,
            config_snapshot={
                "mode": panel_mode.value,
                "reader_count": len(profile_ids),
                "reader_profile_ids": profile_ids,
                "min_valid_readers": panel_config.min_valid_readers,
                "max_ballot_issues": panel_config.max_ballot_issues,
                "max_discussion_issues": panel_config.max_discussion_issues,
                "max_rounds_per_issue": panel_config.max_rounds_per_issue,
            },
            model_snapshot={"provider": "fake", "model": "deterministic-reader-panel-v1"},
            prompt_snapshot={"version": "v1"},
            target_audience=final_target_audience,
            test_goals=final_test_goals,
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

        await self._db.commit()

        return ReaderPanelSessionResult(
            session_id=session_id,
            workflow_run_id=workflow_run.id,
            status=ReaderPanelStatus.INDEPENDENT_READING.value,
            mode=panel_mode.value,
            is_noop=False,
            planned_readers=len(profile_ids),
            completed_readers=0,
            initial_reports_locked=False,
            stale=False,
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

        # 2. Check staleness against live document version
        if (
            panel_session.document is not None
            and panel_session.document.current_version_id != panel_session.document_version_id
        ):
            panel_session.stale = True

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
                for r in panel_session.reader_runs
                if r.initial_report is not None
            ]
            return ReaderPanelSessionResult(
                session_id=panel_session.id,
                workflow_run_id=panel_session.workflow_run_id,
                status=panel_session.status,
                mode=panel_session.mode,
                is_noop=False,
                planned_readers=len(panel_session.reader_runs),
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
        for run in panel_session.reader_runs:
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
                # LLM / provider invocation outside database transaction
                output: ReaderInitialReadingOutput = panel_provider.generate_initial_reading(req)

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
            except Exception as exc:
                run.status = "failed"
                run.error_code = type(exc).__name__
                run.error_message = _sanitize_error_message(exc)

        # 5. Evaluate Quorum
        valid_count = len(collected_reports)
        min_valid = panel_session.config_snapshot.get("min_valid_readers", 1)
        planned_count = len(panel_session.reader_runs)

        if valid_count >= min_valid:
            panel_session.status = ReaderPanelStatus.INITIAL_REPORTS_LOCKED.value
            panel_session.initial_reports_locked_at = datetime.now(timezone.utc)
            if valid_count < planned_count:
                panel_session.degradation_reason = (
                    f"Degraded sample: {valid_count}/{planned_count} readers succeeded"
                )
            await self._db.commit()

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
                for r in panel_session.reader_runs
                if r.initial_report is not None
            ]
            return ReaderPanelSessionResult(
                session_id=panel_session.id,
                workflow_run_id=panel_session.workflow_run_id,
                status=panel_session.status,
                mode=panel_session.mode,
                is_noop=False,
                planned_readers=planned_count,
                completed_readers=valid_count,
                initial_reports_locked=True,
                stale=panel_session.stale,
                degradation_reason=panel_session.degradation_reason,
                reports=reports_data,
            )
        else:
            panel_session.status = ReaderPanelStatus.FAILED.value
            panel_session.failure_reason = (
                f"Quorum failed: only {valid_count}/{min_valid} required readers succeeded"
            )
            await self._db.commit()
            raise ReaderPanelQuorumError()

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
            session: ReaderPanelSession, eligible_runs: list[ReaderRun]
        ) -> tuple[Any, ...]:
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
                        for run in eligible_runs
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
            for _ in range(2):
                try:
                    output = panel_provider.extract_issues(request)
                    if type(output) is not ModeratorIssueExtractionOutput:
                        raise TypeError
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
                    normalized = deduped
                    break
                except Exception:
                    continue

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
                    panel_session.status = ReaderPanelStatus.FAILED.value
                    panel_session.failure_reason = (
                        "Issue extraction produced invalid structured output."
                    )
                    self._db.add(
                        WorkflowEvent(
                            workflow_run_id=panel_session.workflow_run_id,
                            event_type="reader_panel.failed",
                            node_name="failed",
                            payload={
                                "session_id": str(panel_session.id),
                                "status": panel_session.status,
                                "reason_code": "invalid_issue_extraction",
                            },
                            event_sequence=None,
                        )
                    )
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
        pending: list[tuple[ReaderRun, ReaderPanelIssue, ReaderBallotOutput]] = []
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
                for _ in range(2):
                    try:
                        output = panel_provider.generate_blind_ballot(request)
                        if type(output) is not ReaderBallotOutput:
                            raise TypeError
                        if (
                            output.issue_number != issue.issue_number
                            or not output.evidence
                            or any(
                                not set(ref.segment_ids).issubset(segment_ids)
                                for ref in output.evidence
                            )
                        ):
                            raise ValueError
                        valid_output = output
                        break
                    except Exception:
                        continue
                if valid_output is not None:
                    pending.append((run, issue, valid_output))

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
        valid_run_ids = {run.id for run in eligible_runs}
        issues_by_id = {issue.id: issue for issue in issues}
        for run, issue, output in pending:
            pair = (run.id, issue.id)
            if (
                panel_session.status == ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value
                or run.id not in valid_run_ids
                or issue.id not in issues_by_id
                or pair in actual_pairs
            ):
                continue
            ballot = ReaderPanelBallot(
                id=uuid4(),
                session_id=panel_session.id,
                reader_run_id=run.id,
                issue_id=issue.id,
                phase="initial",
                severity=output.severity.value,
                suggested_action=output.suggested_action.value,
                confidence=output.confidence.value,
                evidence=[ref.model_dump() for ref in output.evidence],
                position_changed=False,
                change_reason=None,
                remaining_disagreement=None,
            )
            self._db.add(ballot)
            ballots.append(ballot)
            actual_pairs.add(pair)
        expected_pairs = {(run.id, issue.id) for run in eligible_runs for issue in issues}
        complete = expected_pairs.issubset(actual_pairs)
        if complete and panel_session.initial_ballots_locked_at is None:
            panel_session.initial_ballots_locked_at = datetime.now(timezone.utc)
            panel_session.status = ReaderPanelStatus.INITIAL_BALLOTS_LOCKED.value
            panel_session.degradation_reason = None
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
            panel_session.degradation_reason = "One or more initial ballots were invalid."
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
