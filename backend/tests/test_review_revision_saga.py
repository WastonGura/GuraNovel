from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2CommitIndeterminateError,
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Updated,
    ChapterProductionV2ValidationError,
)
from app.services.review_revision_handoff import ReviewRevisionPlan
from app.services.review_revision_saga import (
    ReviewRevisionIdentity,
    ReviewRevisionSaga,
    _candidates,
    _exact_attempt,
    _finalize_review_revision,
    _normalize_uuid,
    _normalized_plan,
    _release,
    _validate_persist_inputs,
)
from app.workflows.chapter_production import ChapterProductionStatus

PROJECT_ID = UUID("11111111-1111-4111-8111-111111111111")
CHAPTER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
ACTOR_ID = UUID("77777777-7777-4777-8777-777777777777")
DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")
VERSION_ID = UUID("66666666-6666-4666-8666-666666666666")
SEGMENT_ID = UUID("88888888-8888-4888-8888-888888888888")
REPORT_ID = UUID("99999999-9999-4999-8999-999999999999")


def _plan() -> ReviewRevisionPlan:
    return ReviewRevisionPlan(
        source_document_id=DOCUMENT_ID,
        source_version_id=VERSION_ID,
        source_content_hash="a" * 64,
        operation_key="b" * 64,
        attempt_id=str(UUID(int=1)),
        attempt_checkpoint_index=1,
        report_ids=(REPORT_ID,),
        report_input_hash="c" * 64,
        target_segment_ids=(SEGMENT_ID,),
        segment_map=SimpleNamespace(),
        candidate=SimpleNamespace(
            segments=(SimpleNamespace(segment_id=SEGMENT_ID, content="New scene."),)
        ),
    )


def _identity() -> ReviewRevisionIdentity:
    return ReviewRevisionIdentity(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=RUN_ID,
        document_id=DOCUMENT_ID,
        version_id=VERSION_ID,
        source_version_id=VERSION_ID,
        source_content_hash="a" * 64,
        content_hash="d" * 64,
        operation_key="b" * 64,
        attempt_id=str(UUID(int=1)),
        report_ids=(REPORT_ID,),
        report_input_hash="c" * 64,
    )


_PERSIST_KWARGS: dict[str, object] = {
    "project_id": PROJECT_ID,
    "chapter_id": CHAPTER_ID,
    "workflow_run_id": RUN_ID,
    "actor_user_id": ACTOR_ID,
}


class _FakeSession:
    def __init__(self) -> None:
        self.rolled_back = 0
        self.committed = 0

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        self.rolled_back += 1


class _FakeDocuments:
    def __init__(self) -> None:
        self.read_calls: list[tuple[object, object]] = []
        self.write_calls: list[dict[str, object]] = []
        self.source_content = "source"
        self.written_version: object = None

    async def read_version_content(self, document_id: object, version_id: object) -> str:
        self.read_calls.append((document_id, version_id))
        return self.source_content

    async def write_document(self, **kwargs: object) -> object:
        self.write_calls.append(kwargs)
        assert self.written_version is not None
        return self.written_version


class _RecordingService:
    def __init__(self) -> None:
        self.session = _FakeSession()
        self.documents = _FakeDocuments()
        self.release_calls: list[tuple[object, dict[str, object]]] = []
        self.rollback_calls = 0

    async def _release_attempt(self, workflow_run_id: object, **kwargs: object) -> None:
        self.release_calls.append((workflow_run_id, kwargs))

    async def _commit(self) -> None:
        await self.session.commit()

    async def _rollback(self) -> None:
        self.rollback_calls += 1
        await self.session.rollback()


def test_identity_is_frozen_slots_and_content_safe() -> None:
    identity = _identity()

    with pytest.raises(FrozenInstanceError):
        identity.content_hash = "e" * 64  # type: ignore[misc]
    assert not hasattr(identity, "__dict__")
    assert "New scene." not in repr(identity)
    assert "canary" not in repr(identity)


def test_exact_attempt_rejects_aba_late_or_foreign_tokens() -> None:
    expected_key = "b" * 64
    expected_token = str(UUID(int=1))
    expected_report_hash = "c" * 64
    attempt = {
        "key": expected_key,
        "attempt_id": expected_token,
        "kind": "review",
        "checkpoint_index": 1,
        "report_input_hash": expected_report_hash,
        "status": "claimed",
    }

    assert (
        _exact_attempt(
            attempt,
            operation_key=expected_key,
            attempt_id=expected_token,
            checkpoint_index=1,
            report_input_hash=expected_report_hash,
        )
        is True
    )
    assert (
        _exact_attempt(
            attempt,
            operation_key=expected_key,
            attempt_id=str(UUID(int=2)),
            checkpoint_index=1,
            report_input_hash=expected_report_hash,
        )
        is False
    )
    assert (
        _exact_attempt(
            attempt,
            operation_key=expected_key,
            attempt_id=expected_token,
            checkpoint_index=2,
            report_input_hash=expected_report_hash,
        )
        is False
    )
    assert (
        _exact_attempt(
            None,
            operation_key=expected_key,
            attempt_id=expected_token,
            checkpoint_index=1,
            report_input_hash=expected_report_hash,
        )
        is False
    )
    assert (
        _exact_attempt(
            {**attempt, "kind": "feedback"},
            operation_key=expected_key,
            attempt_id=expected_token,
            checkpoint_index=1,
            report_input_hash=expected_report_hash,
        )
        is False
    )
    assert (
        _exact_attempt(
            {**attempt, "status": "failed"},
            operation_key=expected_key,
            attempt_id=expected_token,
            checkpoint_index=1,
            report_input_hash=expected_report_hash,
        )
        is False
    )
    assert (
        _exact_attempt(
            {**attempt, "report_input_hash": "d" * 64},
            operation_key=expected_key,
            attempt_id=expected_token,
            checkpoint_index=1,
            report_input_hash=expected_report_hash,
        )
        is False
    )


@pytest.mark.anyio
async def test_persist_writes_exactly_one_candidate_when_none_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RecordingService()
    version = SimpleNamespace(id=VERSION_ID, content_hash="d" * 64)
    service.documents.written_version = version
    saga = ReviewRevisionSaga(service, lambda *a, **k: "revised", lambda **k: None)
    plan = _plan()
    order: list[str] = []

    async def revalidate(*args: object, **kwargs: object) -> None:
        order.append("revalidate")

    async def candidates(*args: object, **kwargs: object) -> list[object]:
        order.append("candidates")
        return []

    async def read_content(document_id: object, version_id: object) -> str:
        order.append("read")
        return "source"

    def identity(value: object, *args: object, **kwargs: object) -> object:
        order.append("identity")
        return SimpleNamespace(version_id=value.id)

    monkeypatch.setattr("app.services.review_revision_saga._revalidate_revision_prewrite", revalidate)
    monkeypatch.setattr("app.services.review_revision_saga._candidates", candidates)
    monkeypatch.setattr("app.services.review_revision_saga._identity", identity)
    monkeypatch.setattr(service.documents, "read_version_content", read_content)

    result = await saga.persist(plan, **_PERSIST_KWARGS)

    assert result.version_id == VERSION_ID
    assert len(service.documents.write_calls) == 1
    assert service.documents.write_calls[0]["document_id"] == DOCUMENT_ID
    assert service.documents.write_calls[0]["content"] == "revised"
    assert service.documents.write_calls[0]["source"].value == "writer_agent"
    assert service.documents.write_calls[0]["agent_role"] == "revision_agent"
    assert service.documents.write_calls[0]["expected_current_version_id"] == VERSION_ID
    assert service.documents.write_calls[0]["workflow_run_id"] == RUN_ID
    assert service.documents.write_calls[0]["version_metadata"] == {
        "contract_version": "chapter-production-v2",
        "operation_key": plan.operation_key,
        "attempt_id": plan.attempt_id,
    }
    assert order == ["revalidate", "read", "candidates", "identity"]
    assert service.release_calls == []


@pytest.mark.anyio
async def test_persist_adopts_one_exact_candidate_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RecordingService()
    saga = ReviewRevisionSaga(service, lambda *a, **k: "revised", lambda **k: None)
    plan = _plan()
    document = SimpleNamespace(id=DOCUMENT_ID)
    version = SimpleNamespace(id=VERSION_ID, content_hash="d" * 64)

    async def revalidate(*args: object, **kwargs: object) -> None:
        return None

    async def candidates(*args: object, **kwargs: object) -> list[object]:
        return [(document, version)]

    def validate_candidate(*args: object, **kwargs: object) -> None:
        return None

    def identity(value: object, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(version_id=value.id)

    monkeypatch.setattr("app.services.review_revision_saga._revalidate_revision_prewrite", revalidate)
    monkeypatch.setattr("app.services.review_revision_saga._candidates", candidates)
    monkeypatch.setattr("app.services.review_revision_saga._validate_candidate", validate_candidate)
    monkeypatch.setattr("app.services.review_revision_saga._identity", identity)

    result = await saga.persist(plan, **_PERSIST_KWARGS)

    assert result.version_id == VERSION_ID
    assert service.documents.write_calls == []
    assert service.release_calls == []
    assert service.session.committed == 1


@pytest.mark.anyio
async def test_persist_rejects_multiple_candidates_and_releases_the_exact_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RecordingService()
    saga = ReviewRevisionSaga(service, lambda *a, **k: "revised", lambda **k: None)
    plan = _plan()
    document = SimpleNamespace(id=DOCUMENT_ID)
    version = SimpleNamespace(id=VERSION_ID, content_hash="d" * 64)

    async def revalidate(*args: object, **kwargs: object) -> None:
        return None

    async def candidates(*args: object, **kwargs: object) -> list[object]:
        return [(document, version), (document, version)]

    monkeypatch.setattr("app.services.review_revision_saga._revalidate_revision_prewrite", revalidate)
    monkeypatch.setattr("app.services.review_revision_saga._candidates", candidates)

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await saga.persist(plan, **_PERSIST_KWARGS)

    assert service.documents.write_calls == []
    assert service.release_calls == [
        (
            RUN_ID,
            {
                "expected_key": plan.operation_key,
                "expected_attempt_id": plan.attempt_id,
                "expected_kind": "review",
                "expected_checkpoint_index": plan.attempt_checkpoint_index,
            },
        )
    ]


@pytest.mark.anyio
async def test_persist_rejects_foreign_candidate_and_releases_the_exact_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RecordingService()
    saga = ReviewRevisionSaga(service, lambda *a, **k: "revised", lambda **k: None)
    plan = _plan()
    document = SimpleNamespace(id=UUID(int=99))
    version = SimpleNamespace(id=VERSION_ID, content_hash="d" * 64)

    async def revalidate(*args: object, **kwargs: object) -> None:
        return None

    async def candidates(*args: object, **kwargs: object) -> list[object]:
        return [(document, version)]

    def validate_candidate(*args: object, **kwargs: object) -> None:
        raise ChapterProductionV2ReconciliationError()

    monkeypatch.setattr("app.services.review_revision_saga._revalidate_revision_prewrite", revalidate)
    monkeypatch.setattr("app.services.review_revision_saga._candidates", candidates)
    monkeypatch.setattr("app.services.review_revision_saga._validate_candidate", validate_candidate)

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await saga.persist(plan, **_PERSIST_KWARGS)

    assert service.documents.write_calls == []
    assert service.release_calls[0][1]["expected_key"] == plan.operation_key
    assert service.release_calls[0][1]["expected_kind"] == "review"


@pytest.mark.anyio
async def test_finalize_preserves_reconciliation_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RecordingService()
    saga = ReviewRevisionSaga(service, lambda *a, **k: "revised", lambda **k: None)
    identity = _identity()

    async def finalize(*args: object, **kwargs: object) -> object:
        raise ChapterProductionV2ReconciliationError()

    monkeypatch.setattr("app.services.review_revision_saga._finalize_review_revision", finalize)

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await saga.finalize(identity, actor_user_id=ACTOR_ID)

    assert service.rollback_calls == 1
    assert service.session.rolled_back == 1


@pytest.mark.anyio
async def test_finalize_maps_unexpected_errors_to_validation_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RecordingService()
    saga = ReviewRevisionSaga(service, lambda *a, **k: "revised", lambda **k: None)
    identity = _identity()

    async def finalize(*args: object, **kwargs: object) -> object:
        raise RuntimeError("canary-private-review-prose")

    monkeypatch.setattr("app.services.review_revision_saga._finalize_review_revision", finalize)

    with pytest.raises(ChapterProductionV2ValidationError) as raised:
        await saga.finalize(identity, actor_user_id=ACTOR_ID)

    assert raised.value.__cause__ is None
    assert "canary" not in repr(raised.value)
    assert service.rollback_calls == 1


@pytest.mark.anyio
async def test_finalize_returns_the_next_review_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RecordingService()
    saga = ReviewRevisionSaga(service, lambda *a, **k: "revised", lambda **k: None)
    identity = _identity()
    updated = ChapterProductionV2Updated(RUN_ID, DOCUMENT_ID, VERSION_ID, None)

    async def finalize(*args: object, **kwargs: object) -> object:
        return updated

    monkeypatch.setattr("app.services.review_revision_saga._finalize_review_revision", finalize)

    assert await saga.finalize(identity, actor_user_id=ACTOR_ID) == updated
    assert service.rollback_calls == 0


class _CandidateScalarSession:
    def __init__(self, versions: list[object], documents: list[object]) -> None:
        self.versions = versions
        self.documents = documents
        self.statements: list[object] = []

    async def scalars(self, statement: object) -> list[object]:
        self.statements.append(statement)
        if len(self.statements) == 1:
            return self.versions
        return self.documents


@pytest.mark.anyio
async def test_candidates_filters_documents_by_project_and_chapter() -> None:
    plan = _plan()
    session = _CandidateScalarSession([SimpleNamespace(document_id=DOCUMENT_ID)], [])

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await _candidates(session, plan, PROJECT_ID, CHAPTER_ID, RUN_ID)

    assert len(session.statements) == 2
    sql = str(session.statements[1].compile(compile_kwargs={"literal_binds": True}))
    assert "documents.project_id =" in sql
    assert "documents.chapter_id =" in sql


class _ZeroScalarSession:
    async def scalar(self, statement: object) -> int:
        return 0


class _FinalizeStubService:
    def __init__(self, state: object, attempt: object) -> None:
        self.state = state
        self.attempt = attempt
        self.session = _ZeroScalarSession()

    async def _require_project_owner(self, *args: object, **kwargs: object) -> None:
        return None

    async def _chapter(self, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(id=CHAPTER_ID, current_draft_document_id=DOCUMENT_ID)

    async def _run(self, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(id=RUN_ID)

    async def _locked_state(self, run: object) -> tuple[object, object]:
        return self.state, SimpleNamespace(checkpoint_index=1)

    def _run_metadata(self, run: object) -> dict[str, object]:
        return {"provider_attempt": self.attempt}


@pytest.mark.anyio
async def test_finalize_rechecks_state_binding_before_transition() -> None:
    identity = _identity()
    state = SimpleNamespace(
        status=ChapterProductionStatus.REVIEW_REVISION,
        awaiting_user=False,
        document_id=str(UUID(int=999)),
        document_version_id=str(identity.source_version_id),
        content_hash=identity.source_content_hash,
        editor_report_id=str(identity.report_ids[0]),
        chief_editor_report_id=None,
        lore_report_id=None,
    )
    attempt = {
        "key": identity.operation_key,
        "attempt_id": identity.attempt_id,
        "kind": "review",
        "checkpoint_index": 1,
        "report_input_hash": identity.report_input_hash,
        "status": "claimed",
    }

    with pytest.raises(ChapterProductionV2ValidationError):
        await _finalize_review_revision(_FinalizeStubService(state, attempt), identity, ACTOR_ID)


@pytest.mark.anyio
async def test_persist_maps_post_write_identity_failure_to_commit_indeterminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _RecordingService()
    version = SimpleNamespace(id=VERSION_ID, content_hash="d" * 64)
    service.documents.written_version = version
    saga = ReviewRevisionSaga(service, lambda *a, **k: "revised", lambda **k: None)
    plan = _plan()

    async def revalidate(*args: object, **kwargs: object) -> None:
        return None

    async def candidates(*args: object, **kwargs: object) -> list[object]:
        return []

    def identity(*args: object, **kwargs: object) -> object:
        raise RuntimeError("post-commit canary")

    monkeypatch.setattr("app.services.review_revision_saga._revalidate_revision_prewrite", revalidate)
    monkeypatch.setattr("app.services.review_revision_saga._candidates", candidates)
    monkeypatch.setattr("app.services.review_revision_saga._identity", identity)

    with pytest.raises(ChapterProductionV2CommitIndeterminateError):
        await saga.persist(plan, **_PERSIST_KWARGS)

    assert service.release_calls == []
    assert service.session.rolled_back == 1


@pytest.mark.anyio
async def test_persist_malformed_plan_with_usable_attempt_fields_releases_attempt() -> None:
    service = _RecordingService()
    saga = ReviewRevisionSaga(service, lambda *a, **k: "revised", lambda **k: None)
    plan = ReviewRevisionPlan(
        source_document_id=DOCUMENT_ID,
        source_version_id=VERSION_ID,
        source_content_hash="a" * 64,
        operation_key="b" * 64,
        attempt_id=str(UUID(int=1)),
        attempt_checkpoint_index=1,
        report_ids=[REPORT_ID],
        report_input_hash="c" * 64,
        target_segment_ids=(SEGMENT_ID,),
        segment_map=SimpleNamespace(),
        candidate=SimpleNamespace(
            segments=(SimpleNamespace(segment_id=SEGMENT_ID, content="New scene."),)
        ),
    )

    with pytest.raises(ChapterProductionV2ValidationError):
        await saga.persist(plan, **_PERSIST_KWARGS)

    assert service.release_calls == [
        (
            RUN_ID,
            {
                "expected_key": plan.operation_key,
                "expected_attempt_id": plan.attempt_id,
                "expected_kind": "review",
                "expected_checkpoint_index": plan.attempt_checkpoint_index,
            },
        )
    ]


@pytest.mark.anyio
async def test_release_skips_unusable_attempt_fields_without_raw_error() -> None:
    service = _RecordingService()
    plan = ReviewRevisionPlan(
        source_document_id=DOCUMENT_ID,
        source_version_id=VERSION_ID,
        source_content_hash="a" * 64,
        operation_key=None,
        attempt_id=None,
        attempt_checkpoint_index=None,
        report_ids=(REPORT_ID,),
        report_input_hash="c" * 64,
        target_segment_ids=(SEGMENT_ID,),
        segment_map=SimpleNamespace(),
        candidate=SimpleNamespace(segments=()),
    )

    await _release(service, plan, RUN_ID)

    assert service.release_calls == []


def test_validate_persist_inputs_accepts_real_handoff_plan_shape() -> None:
    from uuid import uuid4

    from app.workspace.hashing import sha256_content

    plan = ReviewRevisionPlan(
        source_document_id=DOCUMENT_ID,
        source_version_id=VERSION_ID,
        source_content_hash=sha256_content("source content"),
        operation_key=sha256_content("operation key"),
        attempt_id=str(uuid4()),
        attempt_checkpoint_index=2,
        report_ids=(REPORT_ID,),
        report_input_hash=sha256_content("report input"),
        target_segment_ids=(SEGMENT_ID,),
        segment_map=SimpleNamespace(),
        candidate=SimpleNamespace(segments=()),
    )

    _validate_persist_inputs(plan, PROJECT_ID, CHAPTER_ID, RUN_ID, ACTOR_ID)


def test_validate_persist_inputs_accepts_pgproto_uuid_values() -> None:
    import asyncpg
    from uuid import uuid4

    from app.workspace.hashing import sha256_content

    source_document_id = asyncpg.pgproto.pgproto.UUID(uuid4().hex)
    source_version_id = asyncpg.pgproto.pgproto.UUID(uuid4().hex)
    report_id = asyncpg.pgproto.pgproto.UUID(uuid4().hex)
    target_segment_id = asyncpg.pgproto.pgproto.UUID(uuid4().hex)
    plan = ReviewRevisionPlan(
        source_document_id=source_document_id,
        source_version_id=source_version_id,
        source_content_hash=sha256_content("source content"),
        operation_key=sha256_content("operation key"),
        attempt_id=str(uuid4()),
        attempt_checkpoint_index=2,
        report_ids=(report_id,),
        report_input_hash=sha256_content("report input"),
        target_segment_ids=(target_segment_id,),
        segment_map=SimpleNamespace(),
        candidate=SimpleNamespace(segments=()),
    )

    _validate_persist_inputs(plan, PROJECT_ID, CHAPTER_ID, RUN_ID, ACTOR_ID)
    assert _normalize_uuid(plan.source_document_id) == UUID(str(plan.source_document_id))
    assert _normalize_uuid(plan.source_version_id) == UUID(str(plan.source_version_id))
    normalized = _normalized_plan(plan)
    assert type(normalized.source_document_id) is UUID
    assert type(normalized.report_ids[0]) is UUID
    assert type(normalized.target_segment_ids[0]) is UUID
