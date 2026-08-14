from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest

from app.agents import DeterministicChapterWriterProvider, WriterAgent
from app.models import Chapter, Document
from app.services.chapter_production_v2_service import (
    ChapterProductionV2ReconciliationError,
    ChapterProductionV2Service,
    ChapterProductionV2ValidationError,
)


BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND / "app" / "services" / "chapter_production_repository.py"
SERVICE = BACKEND / "app" / "services" / "chapter_production_v2_service.py"


def _attribute_calls(tree: ast.AST) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_repository_has_a_closed_persistence_only_import_boundary() -> None:
    assert REPOSITORY.exists()
    source = REPOSITORY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    forbidden_prefixes = (
        "app.agents",
        "app.llm",
        "app.services.document_service",
        "app.workspace",
        "aiofiles",
        "langgraph",
        "os",
        "pathlib",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )
    assert not (
        {"add", "commit", "delete", "flush", "merge", "rollback"}
        & _attribute_calls(tree)
    )
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not ({"delete", "insert", "update"} & imported_names)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not ({"delete", "insert", "open", "update"} & called_names)
    assert "_CONTRACT_VERSION" not in source


def test_service_keeps_compatibility_delegates_and_injects_the_contract() -> None:
    source = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ChapterProductionV2Service"
    )
    methods = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "ChapterProductionRepository(" in methods["__init__"]
    assert "contract_version=_CONTRACT_VERSION" in methods["__init__"]
    for helper, delegate in (
        ("_require_project_owner", "require_project_owner"),
        ("_outline_for_chapter", "outline_for_chapter"),
        ("_chapter", "chapter"),
        ("_run", "run"),
        ("_locked_current_document_version", "locked_current_document_version"),
    ):
        body = methods[helper]
        assert f"self.repository.{delegate}(" in body
        assert "select(" not in body
        assert "self.session." not in body
    assert "project_id, chapter.id" in methods["_outline_for_chapter"]
    current_version_delegate = methods["_locked_current_document_version"]
    for explicit_scope in (
        "project_id=document.project_id",
        "chapter_id=document.chapter_id",
        "document_id=document.id",
        "expected_document_type=DocumentType.CHAPTER_FINAL",
    ):
        assert explicit_scope in current_version_delegate

    repository_source = REPOSITORY.read_text(encoding="utf-8")
    repository_tree = ast.parse(repository_source)
    repository_class = next(
        node
        for node in repository_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ChapterProductionRepository"
    )
    operation_run = next(
        node
        for node in repository_class.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "operation_run"
    )
    assert "operation identity classification" in (ast.get_docstring(operation_run) or "")


@pytest.mark.anyio
async def test_chapter_lock_order_is_advisory_then_refreshed_row_lock() -> None:
    module = __import__(
        "app.services.chapter_production_repository",
        fromlist=["ChapterProductionRepository"],
    )
    project_id = uuid4()
    chapter = Chapter(id=uuid4(), project_id=project_id, chapter_number=1)

    class RecordingSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def execute(self, statement: object, parameters: object = None) -> None:
            self.calls.append(("execute", (statement, parameters)))

        async def scalar(self, statement: object) -> Chapter:
            self.calls.append(("scalar", statement))
            return chapter

        async def commit(self) -> None:
            raise AssertionError("repository must not commit")

        async def rollback(self) -> None:
            raise AssertionError("repository must not roll back")

    session = RecordingSession()
    repository = module.ChapterProductionRepository(
        session,
        contract_version="injected-contract",
        inactive_run_statuses=frozenset({"done"}),
    )
    result = await repository.chapter(project_id, chapter.id, lock=True)

    assert result is chapter
    assert [kind for kind, _ in session.calls] == ["execute", "scalar"]
    advisory_statement, advisory_parameters = session.calls[0][1]
    assert "pg_advisory_xact_lock" in str(advisory_statement)
    assert advisory_parameters == {"key": f"chapter-production-v2:{chapter.id}"}
    row_statement = session.calls[1][1]
    assert row_statement._for_update_arg is not None
    assert row_statement.get_execution_options()["populate_existing"] is True


@pytest.mark.anyio
async def test_owner_lock_and_invalid_ids_keep_fixed_content_free_failures() -> None:
    module = __import__(
        "app.services.chapter_production_repository",
        fromlist=["ChapterProductionRepository"],
    )
    project_id = uuid4()
    owner_id = uuid4()

    class RecordingSession:
        def __init__(self) -> None:
            self.statement: object | None = None

        async def scalar(self, statement: object) -> object:
            self.statement = statement
            return project_id

    session = RecordingSession()
    repository = module.ChapterProductionRepository(
        session,
        contract_version="injected-contract",
        inactive_run_statuses=frozenset({"DONE"}),
    )
    await repository.require_project_owner(project_id, owner_id, lock=True)
    assert session.statement._for_update_arg is not None
    assert session.statement.get_execution_options()["populate_existing"] is True

    with pytest.raises(module._ChapterProductionRepositoryValidationError) as captured:
        await repository.chapter("unsafe-id", uuid4(), lock=False)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "unsafe-id" not in repr(captured.value)
    with pytest.raises(module._ChapterProductionRepositoryValidationError):
        await repository.operation_run(project_id, uuid4(), "A" * 64)


@pytest.mark.anyio
async def test_every_service_delegate_clears_internal_error_context() -> None:
    module = __import__(
        "app.services.chapter_production_repository",
        fromlist=["_ChapterProductionRepositoryValidationError"],
    )

    class FailingRepository:
        async def require_project_owner(self, *args: object, **kwargs: object) -> None:
            raise module._ChapterProductionRepositoryValidationError()

        async def outline_for_chapter(self, *args: object, **kwargs: object) -> object:
            raise module._ChapterProductionRepositoryValidationError()

        async def chapter(self, *args: object, **kwargs: object) -> object:
            raise module._ChapterProductionRepositoryValidationError()

        async def run(self, *args: object, **kwargs: object) -> object:
            raise module._ChapterProductionRepositoryValidationError()

        async def locked_current_document_version(
            self, *args: object, **kwargs: object
        ) -> object:
            raise module._ChapterProductionRepositoryReconciliationError()

    service = ChapterProductionV2Service(
        object(),  # type: ignore[arg-type]
        writer_agent=WriterAgent(DeterministicChapterWriterProvider()),
    )
    service.repository = FailingRepository()  # type: ignore[assignment]
    project_id = uuid4()
    chapter_id = uuid4()
    run_id = uuid4()
    chapter = Chapter(id=chapter_id, project_id=project_id, chapter_number=1)
    validation_calls = (
        lambda: service._require_project_owner(project_id, uuid4()),
        lambda: service._outline_for_chapter(chapter, project_id, lock=False),
        lambda: service._chapter(project_id, chapter_id, lock=False),
        lambda: service._run(project_id, chapter_id, run_id, lock=False),
    )
    for call in validation_calls:
        with pytest.raises(ChapterProductionV2ValidationError) as captured:
            await call()
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None

    document = Document(
        id=uuid4(),
        project_id=project_id,
        chapter_id=chapter_id,
        type="CHAPTER_FINAL",
        path="chapters/final.md",
    )
    with pytest.raises(ChapterProductionV2ReconciliationError) as reconciliation:
        await service._locked_current_document_version(document)
    assert reconciliation.value.__cause__ is None
    assert reconciliation.value.__context__ is None
