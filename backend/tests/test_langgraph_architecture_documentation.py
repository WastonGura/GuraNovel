from pathlib import Path


ARCHITECTURE = (
    Path(__file__).resolve().parents[2] / "docs" / "architecture.md"
).read_text(encoding="utf-8")


def _section(heading: str, next_heading: str) -> str:
    start = ARCHITECTURE.index(heading)
    end = ARCHITECTURE.index(next_heading, start)
    return ARCHITECTURE[start:end]


def _table_first_column(section: str) -> set[str]:
    values: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        values.add(line.split("|", 2)[1].strip().strip("`"))
    return values


def test_langgraph_authority_and_disposable_cursor_contract() -> None:
    section = _section(
        "## LangGraph scheduling boundary (target contract, v0.9)",
        "## Reader-aware chapter quality pipeline",
    )

    assert "PostgreSQL is the sole business source of truth" in section
    assert "LangGraph is only the node/edge scheduler" in section
    assert "`thread_id = workflow_run_id`" in section
    assert "server-owned `graph_id` and `graph_version`" in section
    assert "discarded in full" in section
    assert "reconstructed from PostgreSQL" in section
    assert "must never become a second business truth" in section
    assert "exact 0, 1, or N cardinality" in section
    assert "orphan, mismatch, duplicate, malformed, or cross-scoped" in section
    assert "must not guess" in section
    for model in (
        "WorkflowRun",
        "WorkflowCheckpoint",
        "ActionRequest",
        "ReviewReport",
        "Document",
        "DocumentVersion",
    ):
        assert f"`{model}`" in section


def test_graph_state_has_an_exact_content_free_allowlist() -> None:
    section = _section(
        "### Exact content-free graph state allowlist",
        "### Closed typed outcome boundary",
    )

    assert _table_first_column(section) == {
        "workflow_run_id",
        "graph_id",
        "graph_version",
        "cursor",
        "workflow_checkpoint_index",
        "invocation_id",
        "attempt_id",
        "claim_id",
        "action_request_id",
        "resume_reason",
    }
    assert "No additional key is permitted" in section
    assert "IDs and enums are references, not authority" in section
    assert "non-negative integer" in section
    assert "freshly derived stale hint and reference" in section
    assert "has no authority" in section
    assert "may point only through `workflow_checkpoint_index`" in section
    assert "checkpointer payload, framework configuration, and framework metadata" in section
    assert "closed and sanitized" in section
    assert "hidden extension" in section


def test_typed_outcomes_are_closed_and_separate_from_state_and_telemetry() -> None:
    section = _section(
        "### Closed typed outcome boundary",
        "### Persisted human gates and pause/resume",
    )

    assert "closed discriminated mechanical schema" in section
    for outcome in (
        "continue",
        "await-user",
        "retryable-failure",
        "reconciliation-required",
        "cancelled",
        "complete",
    ):
        assert f"`{outcome}`" in section
    assert "not persistent graph state" in section
    assert "not an observability envelope" in section
    assert "observability allowlist does not define" in section


def test_runtime_choice_is_an_immutable_postgresql_pin() -> None:
    section = _section(
        "### PostgreSQL-authoritative runtime pin",
        "### Exact content-free graph state allowlist",
    )

    assert "`WorkflowRun.metadata.chapter_production_runtime`" in section
    assert _table_first_column(section) == {
        "scheduler_kind",
        "graph_id",
        "graph_version",
    }
    assert "exactly these three keys" in section
    assert "same transaction that creates the run" in section
    assert "missing namespace" in section
    assert "service_v2 legacy contract" in section
    assert "must never be auto-upgraded" in section
    assert "any change to the pinned tuple" in section
    assert "reconciliation-required" in section
    assert "graph checkpoint cannot create, own, or modify" in section
    assert "P0 schema-and-compatibility Issue" in section
    assert "completed before #149 begins" in section


def test_action_gate_and_provider_transaction_contracts_are_explicit() -> None:
    gate = _section(
        "### Persisted human gates and pause/resume",
        "### Provider-node three-phase transaction",
    )
    provider = _section(
        "### Provider-node three-phase transaction",
        "### Typed node contracts",
    )

    assert "persisted `ActionRequest` is the pause capability" in gate
    assert "exact action ID" in gate
    assert "fresh transaction" in gate
    assert "client-supplied graph state" in gate
    assert "in-memory interrupt payload" in gate
    assert "`ActionRequest`, `WorkflowRun.awaiting_user`, and `WorkflowCheckpoint`" in gate
    assert "only when that domain transition contract defines one" in gate
    assert "A `WorkflowEvent` is audit evidence, never gate authority" in gate
    assert "same locked transaction" in gate
    assert "after acquiring every required lock" in gate
    assert "single PostgreSQL `clock_timestamp()` wall-clock value" in gate
    assert "`expires_at IS NULL` means no expiry" in gate
    assert "`database_now < expires_at`" in gate
    assert "`database_now >= expires_at`" in gate
    assert "must not use `CURRENT_TIMESTAMP`" in gate
    assert "`transaction_timestamp()`" in gate
    assert "`statement_timestamp()`" in gate
    assert "application or client clock" in gate
    assert "fixed stale/foreign action error" in gate
    assert "does not disclose" in gate

    assert "Phase 1 - claim and snapshot" in provider
    assert "commit the claim before returning" in provider
    assert "fresh UUID `attempt_id` or `claim_id`" in provider
    assert "generation token" in provider
    assert "Phase 2 - provider call" in provider
    assert "no database session or transaction is open" in provider
    assert "Phase 3 - fresh lock, revalidate, and persist" in provider
    assert "current version and content hash" in provider
    assert "operation key, checkpoint, and current domain binding" in provider
    assert "commit-indeterminate" in provider
    obsolete_counter = "attempt" + "_generation"
    assert obsolete_counter not in ARCHITECTURE


def test_every_proposed_node_has_a_complete_typed_contract() -> None:
    section = _section("### Typed node contracts", "### Service convergence map")
    rows = [line for line in section.splitlines() if line.startswith("| `")]
    cells = [[cell.strip() for cell in line.strip("|").split("|")] for line in rows]

    assert {row[0].strip("`") for row in cells} == {
        "reconstruct",
        "draft",
        "await_author_action",
        "author_revision",
        "editor_review",
        "chief_editor_review",
        "lore_review",
        "corrective_revision",
        "mark_revision_ready",
        "finalize",
        "reconcile",
    }
    assert all(len(row) == 6 for row in cells)
    assert all(all(cell for cell in row) for row in cells)
    assert "Typed input" in section
    assert "Typed output" in section
    assert "Authority" in section
    assert "Side effects" in section
    assert "Failure semantics" in section
    assert "candidate persistence" in section
    assert "review evaluation" in section
    assert "final evaluation" in section
    assert "internal coordinator sub-stages, not separate scheduler nodes" in section
    assert "#149 body must be updated before development begins" in section
    await_row = next(row for row in cells if row[0] == "`await_author_action`")
    assert "when its domain transition requires one" in await_row[4]
    assert "event has no gate authority" in await_row[5]


def test_current_service_has_an_explicit_convergence_map() -> None:
    section = _section("### Service convergence map", "### Recovery and failure matrix")

    assert "oversized `ChapterProductionV2Service` monolith" in section
    assert "CI hardening Issue" in section
    assert "completed before #149 begins" in section
    assert "facade must not reacquire ORM queries or lock orchestration" in section
    for component in (
        "ChapterProductionV2Service",
        "ChapterProductionRepository",
        "ChapterDraftRevisionCoordinator",
        "ChapterReviewCoordinator",
        "RevisionReadinessStore",
        "ChapterFinalizationSaga",
        "ChapterProductionRecovery",
        "ProviderAttemptCoordinator",
    ):
        assert f"`{component}`" in section
    for disposition in ("Retain", "Wrap", "Split", "Remove"):
        assert f"**{disposition}**" in section


def test_reconstruction_has_executable_evidence_for_every_frozen_state() -> None:
    section = _section(
        "### PostgreSQL reconstruction evidence matrix",
        "### Observability and content boundary",
    )
    rows = [line for line in section.splitlines() if line.startswith("| `")]
    cells = [[cell.strip() for cell in line.strip("|").split("|")] for line in rows]

    assert {row[0].strip("`") for row in cells} == {
        "DRAFTING",
        "AUTHOR_REVISION",
        "EDITOR_REVIEW",
        "REVIEW_REVISION",
        "CHIEF_FINAL_REVIEW",
        "LORE_FINAL_REVIEW",
        "REVISION_READY",
        "ARCHIVE_UPDATE",
        "FAILED",
        "CANCELLED",
        "COMPLETED",
    }
    assert all(len(row) == 5 and all(row) for row in cells)
    for required in (
        "latest domain checkpoint is exactly one contiguous maximum",
        "pending action cardinality is exactly 0 or 1",
        "current draft and version resolve to exactly one bound pair",
        "stage reports use the required exact 0/1 combination",
        "provider attempt or reviewer claim is 0 or 1 and current",
        "READY checkpoint plus event is exact 1+1",
        "final document, version, current file, and snapshot file",
        "N greater than expected, orphan, mismatch, malformed, or cross-scope",
    ):
        assert required in section
    chief = next(row for row in cells if row[0] == "`CHIEF_FINAL_REVIEW`")
    assert "never reconstructed when policy skips Chief Editor" in " ".join(chief)
    assert "route directly from Editor to Lore" in " ".join(chief)
    failed = " ".join(next(row for row in cells if row[0] == "`FAILED`"))
    assert "known provider failure requires exactly one" in failed
    assert "`status=failed`" in failed
    assert "failed-from status, checkpoint, operation key, fresh token, and target" in failed
    assert "commit-indeterminate permits exactly one `status=claimed` exact current claim" in failed
    assert "explicit reconciliation with no provider retry" in failed
    assert "failure before claim persistence permits zero" in failed
    assert "unrelated, duplicate, expired, wrong-status, or wrongly bound" in failed
    assert "fail closed" in failed


def test_recovery_and_observability_contracts_are_complete() -> None:
    recovery = _section(
        "### Recovery and failure matrix",
        "### Observability and content boundary",
    )
    observability = _section(
        "### Observability and content boundary",
        "### Persistence constraints and graph cutover blockers",
    )

    for scenario in (
        "Restart",
        "Stale invocation",
        "Claim-token ABA",
        "Cancellation",
        "Provider failure",
        "Commit-indeterminate",
        "Reconciliation",
    ):
        assert f"| {scenario} |" in recovery

    for allowed in (
        "graph_id",
        "graph_version",
        "workflow_run_id",
        "node_name",
        "invocation_id",
        "attempt_id",
        "claim_id",
        "action_request_id",
        "outcome_code",
        "failure_code",
        "duration_ms",
    ):
        assert f"`{allowed}`" in observability
    for forbidden in (
        "chapter prose",
        "full prompts",
        "credentials",
        "raw provider payloads",
        "filesystem or workspace paths",
        "unrestricted metadata",
        "full report bodies",
    ):
        assert forbidden in observability
    assert "Only structured logs, traces, metrics, and safe errors" in observability
    assert "safe subset of persistent graph state and typed outcome fields" in observability
    assert "does not expand either closed schema" in observability


def test_persistence_debt_and_graph_cutover_blockers_are_explicit() -> None:
    section = _section(
        "### Persistence constraints and graph cutover blockers",
        "### Migration, compatibility, rollback, and parity",
    )

    assert "per-run monotonic `event_sequence` transition ordinal for Chapter Production V2" in section
    assert "nullable `event_sequence`" in section
    assert "legacy rows and other workflow types remain `NULL`" in section
    assert "partial unique index" in section
    assert "non-`NULL`" in section
    assert "V2 consumers order only by this sequence" in section
    assert "`created_at` plus a random UUID" in section
    assert "graph cursor" in section
    assert "P0 schema-and-compatibility Issue" in section
    assert "runtime pin and event ordering are graph cutover blockers" in section
    for debt in (
        "runtime pin",
        "event sequence",
        "READY semantic key",
        "one pending action",
        "review tuple",
        "typed provider claims and operation keys",
        "event-to-checkpoint binding",
        "same-document foreign key",
    ):
        assert debt in section
    assert "P0" in section
    assert "P1" in section
    assert "P2" in section
    assert "fail closed" in section
    assert "P0 schema-and-compatibility Issue" in section
    assert "P0 CI hardening Issue" in section


def test_langgraph_rollout_orders_migration_and_proves_rollback_parity() -> None:
    section = _section(
        "### Migration, compatibility, rollback, and parity",
        "## Reader-aware chapter quality pipeline",
    )

    assert section.index("#148") < section.index("#149") < section.index("#150")
    extraction_positions = [section.index(f"#{issue}") for issue in range(153, 159)]
    assert extraction_positions == sorted(extraction_positions)
    assert section.index("#148") < extraction_positions[0]
    assert extraction_positions[-1] < section.index("3. **#149")
    assert "existing service-backed scheduler remains the default" in section
    assert "stable `ChapterProductionV2Service` facade" in section
    assert "numbered behavior-preserving extraction sequence" in section
    assert "completed before #149 begins" in section
    assert "Each Issue moves only its named responsibility" in section
    assert "#149 only composes the graph" in section
    assert "import-boundary tests" in section
    assert "rollback switch" in section
    assert "affects only the default for runs created after the switch" in section
    assert "already pinned to `langgraph`" in section
    assert "must never execute through `service_v2`" in section
    assert "compatible exact graph version" in section
    assert "paused until that exact version is deployed again" in section
    assert "no safe mid-run fallback" in section
    assert "no dual scheduler" in section
    assert "frozen #114/#115 fixtures" in section
    assert "full serial PostgreSQL integration suite" in section
    assert "provider-call parity" in section
    assert "independent Correctness and Security reviewers return PASS" in section
    for artifact in (
        "DocumentVersion",
        "ActionRequest",
        "ReviewReport",
        "WorkflowCheckpoint",
        "WorkflowEvent",
    ):
        assert f"`{artifact}`" in section
    assert "Project Creation" in section
    assert "Project Maintenance" in section
    assert "legacy Chapter Production" in section
