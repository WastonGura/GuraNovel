from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models import DocumentSource
from app.services.chapter_finalization_saga import (
    ChapterFinalizationSaga,
    _ReadySourceEvidence,
    _final_document_path,
    _final_operation_key,
    _valid_final_document_paths,
)
from app.services.chapter_production_v2_contracts import (
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2ValidationError,
)
from app.workspace.hashing import sha256_content
from app.workspace.paths import version_snapshot_path


def _state(document_version_id: str, review_policy_version: str) -> SimpleNamespace:
    return SimpleNamespace(
        document_version_id=document_version_id,
        review_policy_version=review_policy_version,
    )


def test_final_operation_key_is_deterministic_and_content_free() -> None:
    run_id = uuid4()
    run = SimpleNamespace(id=run_id)
    state = _state(str(uuid4()), "chapter-quality-v1")

    key = _final_operation_key(run, state)

    assert key == _final_operation_key(run, state)
    assert type(key) is str and len(key) == 64
    assert all(character in "0123456789abcdef" for character in key)
    assert "chapter" not in key and "final" not in key


def test_final_operation_key_rejects_stale_state_without_source_version() -> None:
    run = SimpleNamespace(id=uuid4())
    state = SimpleNamespace(
        document_version_id=None,
        review_policy_version="chapter-quality-v1",
    )

    with pytest.raises(ChapterProductionV2ValidationError):
        _final_operation_key(run, state)


def test_final_document_path_is_re_derived_from_chapter_and_run() -> None:
    run_id = uuid4()
    chapter = SimpleNamespace(chapter_number=7)
    run = SimpleNamespace(id=run_id)

    path = _final_document_path(chapter=chapter, run=run)

    assert path == f"chapters/chapter-0007-{run_id}-final.md"
    assert path == _final_document_path(chapter=chapter, run=run)


def test_valid_final_document_paths_accepts_exact_canonical_v1() -> None:
    document_id = uuid4()
    run_id = uuid4()
    chapter = SimpleNamespace(chapter_number=3)
    run = SimpleNamespace(id=run_id)
    document = SimpleNamespace(id=document_id, path=_final_document_path(chapter=chapter, run=run))
    version = SimpleNamespace(
        version_number=1,
        file_path=document.path,
        snapshot_path=version_snapshot_path(str(document_id), 1).as_posix(),
    )

    assert _valid_final_document_paths(
        chapter=chapter,
        run=run,
        document=document,
        version=version,
    )


@pytest.mark.parametrize(
    ("version_number", "file_path", "snapshot_path"),
    [
        (2, None, None),
        (True, None, None),
        (1, "chapters/foreign.md", None),
        (1, None, ".versions/other/v0001.md"),
    ],
)
def test_valid_final_document_paths_rejects_noncanonical_paths(
    version_number: object,
    file_path: str | None,
    snapshot_path: str | None,
) -> None:
    document_id = uuid4()
    run_id = uuid4()
    chapter = SimpleNamespace(chapter_number=3)
    run = SimpleNamespace(id=run_id)
    canonical = _final_document_path(chapter=chapter, run=run)
    document = SimpleNamespace(id=document_id, path=canonical)
    version = SimpleNamespace(
        version_number=version_number,
        file_path=file_path if file_path is not None else canonical,
        snapshot_path=(
            snapshot_path
            if snapshot_path is not None
            else version_snapshot_path(str(document_id), 1).as_posix()
        ),
    )

    assert not _valid_final_document_paths(
        chapter=chapter,
        run=run,
        document=document,
        version=version,
    )


class _PgProtoUUID:
    """Minimal stand-in for asyncpg ``pgproto.UUID`` (not stdlib ``uuid.UUID``)."""

    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value


class _RecordingService:
    def __init__(self) -> None:
        self.validated_ids: tuple[object, ...] | None = None

    def _validated_ids(self, *values: object) -> tuple[object, ...]:
        self.validated_ids = values
        return values

    async def _rollback(self) -> None:
        pass


@pytest.mark.anyio
async def test_finalize_normalizes_pgproto_uuid_boundary_ids_before_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """asyncpg pgproto.UUID inputs must become stdlib UUIDs before any bind."""

    service = _RecordingService()
    saga = ChapterFinalizationSaga(service)
    values = [_PgProtoUUID(str(uuid4())) for _ in range(4)]

    async def fail_after_validation(**_: object) -> object:
        raise ChapterProductionV2ValidationError()

    monkeypatch.setattr(saga, "_consume_ready_locked", fail_after_validation)
    with pytest.raises(ChapterProductionV2ValidationError):
        await saga.finalize(
            project_id=values[0],  # type: ignore[arg-type]
            chapter_id=values[1],  # type: ignore[arg-type]
            workflow_run_id=values[2],  # type: ignore[arg-type]
            actor_user_id=values[3],  # type: ignore[arg-type]
        )

    assert service.validated_ids is not None
    assert all(type(value) is UUID for value in service.validated_ids)


class _RecoverySession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


class _RecoveryService:
    def __init__(self, version: SimpleNamespace) -> None:
        self.version = version
        self.session = _RecoverySession()

    async def _locked_current_document_version(
        self, document: object
    ) -> SimpleNamespace:
        return self.version


@pytest.mark.anyio
async def test_recover_existing_final_rejects_parented_version_one() -> None:
    """A version-1 CHAPTER_FINAL with a parent must fail before any rewrite."""

    document_id = uuid4()
    run_id = uuid4()
    chapter = SimpleNamespace(
        chapter_number=3,
        final_document_id=document_id,
    )
    run = SimpleNamespace(id=run_id)
    document = SimpleNamespace(
        id=document_id,
        path=_final_document_path(chapter=chapter, run=run),
    )
    state = _state(str(uuid4()), "chapter-quality-v1")
    operation_key = _final_operation_key(run, state)
    version = SimpleNamespace(
        version_number=1,
        file_path=document.path,
        snapshot_path=version_snapshot_path(str(document_id), 1).as_posix(),
        content_hash=sha256_content("content"),
        workflow_run_id=run_id,
        source=DocumentSource.SYSTEM.value,
        metadata_={
            "contract_version": "chapter-production-v2",
            "operation_key": operation_key,
        },
        parent_version_id=uuid4(),
    )
    evidence = _ReadySourceEvidence(
        project_id=uuid4(),
        chapter_id=uuid4(),
        workflow_run_id=run_id,
        actor_user_id=uuid4(),
        draft_document_id=uuid4(),
        draft_version_id=uuid4(),
        draft_hash=sha256_content("content"),
        content="content",
    )
    service = _RecoveryService(version)
    saga = ChapterFinalizationSaga(service)

    with pytest.raises(ChapterProductionV2ReconciliationError):
        await saga._recover_existing_final_document(
            evidence=evidence,
            chapter=chapter,
            run=run,
            state=state,
            final_document=document,
        )
    assert service.session.committed is False
