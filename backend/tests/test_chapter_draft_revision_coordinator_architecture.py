from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FACADE = ROOT / "app/services/chapter_production_v2_service.py"
COORDINATOR = ROOT / "app/services/chapter_draft_revision_coordinator.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _span(node: ast.AST) -> int:
    return node.end_lineno - node.lineno + 1  # type: ignore[attr-defined]


def _service_method(name: str) -> tuple[str, ast.AsyncFunctionDef]:
    source = FACADE.read_text(encoding="utf-8")
    owner = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "ChapterProductionV2Service"
    )
    method = next(
        node
        for node in owner.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, method) or "", method


def test_coordinator_exists_and_is_bounded() -> None:
    assert COORDINATOR.exists()
    source = COORDINATOR.read_text(encoding="utf-8")
    tree = _tree(COORDINATOR)

    assert len(source.splitlines()) <= 300
    assert all(
        _span(node) <= (400 if isinstance(node, ast.ClassDef) else 80)
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_coordinator_is_session_free_with_no_facade_authority() -> None:
    source = COORDINATOR.read_text(encoding="utf-8")
    tree = _tree(COORDINATOR)
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)
    }

    assert "sqlalchemy" not in imports and "asyncpg" not in imports
    assert "app.services.chapter_production_v2_service" not in imports
    for forbidden in (
        "select(",
        "with_for_update",
        "pg_advisory",
        "session.scalar",
        "session.execute",
        "write_document",
        "write_staged_files",
        "create_document",
        "ChapterProductionV2Service",
    ):
        assert forbidden not in source


def test_coordinator_is_constructed_once_by_the_facade() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "ChapterDraftRevisionCoordinator" in source
    assert source.count("self._draft_revision = ChapterDraftRevisionCoordinator(") == 1


def test_reconcile_indeterminate_is_a_thin_draft_delegate() -> None:
    method, _ = _service_method("reconcile_indeterminate")

    assert len(method.splitlines()) <= 40
    assert "self._draft_revision.reconcile" in method
    for forbidden in (
        "_reconciliation_candidates",
        "_candidate_matches_provider_attempt",
        "_resolved_source_action",
        "_binding_from_checkpoint_action",
        "_restore_feedback_without_write",
        "FeedbackCandidateIdentity(",
        "ReviewRevisionIdentity(",
        "_finalize_manual_edit",
        "self.session",
        "select(",
    ):
        assert forbidden not in method


def test_acknowledge_provider_no_write_is_a_thin_draft_delegate() -> None:
    method, _ = _service_method("acknowledge_provider_no_write")

    assert len(method.splitlines()) <= 60
    assert "self._draft_revision.acknowledge_no_write" in method
    for forbidden in (
        "_reconciliation_candidates",
        "_candidate_matches_provider_attempt",
        "_restore_feedback_without_write",
        "_set_attempt",
        "_append_state",
        "self.session",
        "select(",
    ):
        assert forbidden not in method


def test_coordinator_routes_draft_reconcile_statuses_to_phase_modules() -> None:
    source = COORDINATOR.read_text(encoding="utf-8")

    assert "self._feedback_saga.reconcile_drafting" in source
    assert "self._review_saga.reconcile_review" in source
    assert "self._manual_edit.reconcile_manual" in source
    assert "ChapterProductionStatus.DRAFTING" in source
    assert "ChapterProductionStatus.AUTHOR_REVISION" in source
    assert "ChapterProductionStatus.REVIEW_REVISION" in source


def test_coordinator_routes_draft_acknowledge_statuses_to_phase_modules() -> None:
    source = COORDINATOR.read_text(encoding="utf-8")

    assert "self._feedback_saga.acknowledge_no_write" in source
    assert "self._review_saga.acknowledge_no_write" in source


def test_removed_private_recovery_bodies_are_absent_from_facade() -> None:
    source = FACADE.read_text(encoding="utf-8")

    for name in (
        "def _reconciliation_candidates",
        "def _candidate_matches_provider_attempt",
        "def _resolved_source_action",
        "def _binding_from_checkpoint_action",
        "def _restore_feedback_without_write",
    ):
        assert name not in source


def _budget_base_ref() -> str:
    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    if head.returncode != 0:
        raise AssertionError("HEAD unavailable in this checkout")
    for ref in ("origin/main", "refs/remotes/origin/main", "main", "HEAD^1"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=REPO,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() != head.stdout.strip():
            return ref
    raise AssertionError("base ref unavailable in this checkout")


def test_budget_base_ref_skips_refs_at_head() -> None:
    commits = {
        "HEAD": "tip",
        "origin/main": "tip",
        "refs/remotes/origin/main": "tip",
        "HEAD^1": "parent",
    }

    def resolve(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        ref = command[-1]
        if ref not in commits:
            return subprocess.CompletedProcess(command, 1, "", "missing")
        return subprocess.CompletedProcess(command, 0, f"{commits[ref]}\n", "")

    with patch.object(subprocess, "run", side_effect=resolve):
        assert _budget_base_ref() == "HEAD^1"


def test_total_production_additions_stay_within_budget() -> None:
    ref = _budget_base_ref()
    result = subprocess.run(
        [
            "git",
            "diff",
            f"{ref}...HEAD",
            "--numstat",
            "--",
            COORDINATOR.relative_to(REPO).as_posix(),
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    added = 0
    files = 0
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3:
            added += int(parts[0])
            files += 1
    assert added <= 600
    assert files <= 10


def test_facade_has_no_trailing_whitespace() -> None:
    for number, line in enumerate(FACADE.read_text(encoding="utf-8").splitlines(), 1):
        assert line == line.rstrip(), f"trailing whitespace at line {number}"


def test_draft_reconcile_guards_checkpoint_index_before_candidate_match() -> None:
    feedback = (ROOT / "app/services/feedback_candidate_saga.py").read_text(encoding="utf-8")
    review = (ROOT / "app/services/review_revision_saga.py").read_text(encoding="utf-8")
    guard = 'attempt.get("checkpoint_index") != checkpoint.checkpoint_index'

    assert guard in feedback
    assert guard in review


def test_feedback_candidate_matcher_rejects_non_feedback_attempt_kind() -> None:
    feedback = (ROOT / "app/services/feedback_candidate_saga.py").read_text(encoding="utf-8")
    guard = 'attempt.get("kind") != "feedback"'

    assert guard in feedback
