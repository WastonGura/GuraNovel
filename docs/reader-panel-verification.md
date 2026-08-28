# Reader Panel MVP verification

This matrix is the acceptance record for the Reader Panel MVP. It maps each requirement to the smallest authoritative automated evidence; it does not duplicate focused unit coverage in broad lifecycle tests.

## Acceptance criteria

| Criterion | Automated evidence |
| --- | --- |
| AC-01 — zero side effects when off | `backend/tests/test_reader_panel_service.py::TestReaderPanelServiceInitialization::test_mode_off_returns_noop_without_db_side_effects`; `backend/tests/test_reader_panel_revision_ready.py::test_off_is_the_first_panel_branch_after_ready`; PostgreSQL: `backend/tests/integration/test_reader_panel_revision_ready_integration.py::test_off_preserves_ready_with_zero_panel_side_effects` |
| AC-02 — cold-reading isolation | `backend/tests/test_reader_panel_contracts.py::TestReaderInitialReadingContract::test_cold_read_request_forbids_peer_reports_or_other_readers`; `backend/tests/test_reader_panel_contracts.py::TestBlindInitialBallotContract::test_blind_initial_ballot_request_isolation`; `backend/tests/test_reader_panel_service.py::TestReaderPanelInitialBallots::test_extracts_dedupes_and_collects_isolated_blind_ballots` |
| AC-03 — immutable initial results | `backend/tests/test_reader_panel_service.py::TestReaderPanelDiscussionAndFinalBallots::test_discusses_issue_in_isolation_and_locks_immutable_final_ballots`; PostgreSQL cross-phase comparison in `backend/tests/integration/test_reader_panel_service_integration.py::TestReaderPanelServiceIntegration::test_full_initial_reading_lifecycle_postgresql` |
| AC-04 — non-voting Moderator | `backend/tests/test_reader_panel_profiles.py::TestReaderPanelProfiles::test_moderator_profiles_load_and_cannot_vote_or_transition`; PostgreSQL ballot/message ownership assertions in `test_full_initial_reading_lifecycle_postgresql` |
| AC-05 — issue-scoped turns | `backend/tests/test_reader_panel_service.py::TestReaderPanelDiscussionAndFinalBallots::test_discusses_issue_in_isolation_and_locks_immutable_final_ballots`; `backend/tests/test_reader_panel_service.py::TestReaderPanelDiscussionAndFinalBallots::test_rejects_moderator_limit_and_foreign_evidence_without_leaking_errors`; PostgreSQL message-to-issue anchor assertions in `test_full_initial_reading_lifecycle_postgresql` |
| AC-06 — deterministic termination | `backend/tests/test_reader_panel_service.py::TestReaderPanelDiscussionRemediation::test_zero_round_limit_skips_discussion_but_collects_all_finals`; `backend/tests/test_reader_panel_service.py::TestReaderPanelDiscussionContextValidation::test_round_two_only_receives_previous_summary_and_current_round_turns`; `backend/tests/test_reader_panel_state.py::TestReaderPanelTransitions::test_happy_path_lifecycle_transitions` |
| AC-07 — minority-risk preservation | `backend/tests/test_reader_panel_state.py::TestDeterministicConsensusClassification::test_minority_high_risk_flag_triggered_independently`; `backend/tests/test_reader_panel_service.py::TestReaderPanelEditorHandoffReport::test_polarized_minority_and_target_audience_tally_are_server_owned`; PostgreSQL report assertions in `test_full_initial_reading_lifecycle_postgresql` |
| AC-08 — transparent voting views | `backend/tests/test_reader_panel_state.py::TestDeterministicConsensusClassification::test_target_audience_distribution_separation`; `backend/tests/test_reader_panel_service.py::TestReaderPanelEditorHandoffReport::test_polarized_minority_and_target_audience_tally_are_server_owned`; real route rendering in `frontend/src/ReaderPanelRoute.test.tsx`; `frontend/src/ReaderPanelWorkbench.test.tsx::ReaderPanelWorkbench > loads a deep-linked session, renders report handoff, classifications, risks, and evidence` |
| AC-09 — version invalidation | `backend/tests/test_reader_panel_state.py::TestReaderPanelTransitions::test_stale_flag_is_orthogonal_to_status`; `backend/tests/test_reader_panel_service.py::TestReaderPanelColdReadingCollection::test_version_change_during_provider_call_marks_stale_without_rebase`; PostgreSQL: `backend/tests/integration/test_reader_panel_revision_ready_integration.py::test_manual_start_coexists_and_new_version_only_marks_automatic_panel_stale` and `test_full_initial_reading_lifecycle_postgresql` |
| AC-10 — quorum and degradation | `backend/tests/test_reader_panel_service.py::TestReaderPanelRecoveryControls::test_degraded_quorum_when_partial_readers_fail`; `backend/tests/test_reader_panel_service.py::TestReaderPanelRecoveryControls::test_quorum_failure_when_below_min_valid_readers`; `backend/tests/test_reader_panel_service.py::TestReaderPanelInitialBallots::test_permanent_reader_failure_degrades_and_locks_remaining_quorum` |
| AC-11 — schema enforcement | `backend/tests/test_reader_panel_contracts.py` strict contract suite; `backend/tests/test_reader_panel_service.py::TestReaderPanelDiscussionBoundsAndAgenda::test_malformed_discussion_turn_exhaustion_fails_below_minimum`; `backend/tests/test_reader_panel_service.py::TestReaderPanelDiscussionAndFinalBallots::test_malformed_final_ballot_exhaustion_fails_below_minimum` |
| AC-12 — stable anchors | `backend/tests/test_reader_panel_service.py::TestReaderPanelDiscussionAndFinalBallots::test_rejects_moderator_limit_and_foreign_evidence_without_leaking_errors`; `backend/tests/test_reader_panel_service.py::TestReaderPanelEditorHandoffReport::test_custom_bracketed_segment_reference_is_allowed_when_bound`; PostgreSQL issue/ballot/message assertions in `test_full_initial_reading_lifecycle_postgresql` |
| AC-13 — no direct manuscript edits | `backend/tests/test_reader_panel_service.py::TestReaderPanelEditorHandoffReport::test_generates_version_bound_non_approval_report_idempotently`; PostgreSQL document/version/action row-count assertions in `test_full_initial_reading_lifecycle_postgresql`; real route assertion in `frontend/src/ReaderPanelRoute.test.tsx`; browser request assertion in `frontend/e2e/chapter-production-v2-lifecycle.pw.ts::chapter production v2 flow 4: revision-ready reader panel reaches an editor-only report without leaks or edits` |
| AC-14 — budget limits | `backend/tests/test_reader_panel_state.py::TestReaderPanelModes::test_presets_snapshot_complete_hard_budgets`; `backend/tests/test_reader_panel_service.py::TestReaderPanelColdReadingCollection::test_elapsed_budget_stops_without_provider_call`; `backend/tests/test_reader_panel_service.py::TestReaderPanelColdReadingCollection::test_exact_call_budget_completes_minimum_sample_then_degrades`; `backend/tests/test_reader_panel_service.py::TestReaderPanelColdReadingCollection::test_unknown_output_usage_is_reserved_and_oversize_fails_closed` |

The PostgreSQL lifecycle test binds one immutable document version and session snapshot, performs isolated initial reads, extracts issues, seals initial and final ballots, runs bounded discussion, creates one editor report, marks the result stale after a new version, and verifies replay without duplicate canonical rows. Separate PostgreSQL tests remain authoritative for unique constraints, cross-project rejection, concurrent starts, cancellation, and commit-indeterminate recovery.

## Canonical clean-checkout commands

Backend unit and style gates:

```bash
cd backend
uv sync --frozen --all-groups
uv run ruff check .
uv run pytest -m "not integration"
```

Migration verification (offline; no PostgreSQL service required):

```bash
cd backend
uv run pytest tests/test_alembic.py
```

Frontend unit, lint, design, and build gates:

```bash
cd frontend
npm ci
npm run lint
npm run test -- --run
npm run build
npx --no-install @google/design.md lint DESIGN.md
```

Browser gate, with every API supplied by same-origin Playwright route fakes and all external requests blocked:

```bash
cd frontend
npx playwright install --with-deps chromium
npm run test:e2e
```

PostgreSQL integration is intentionally **PR CI only**. Do not start or run PostgreSQL locally for this verification issue. The pull request's `Backend - PostgreSQL integration tests` job supplies a dedicated `_test` database and runs:

```bash
uv run pytest -m integration -v
```

Tests use deterministic in-memory or route fakes and write no committed fixture, transcript, credential, workspace, screenshot, trace, or browser artifact. Playwright's configured output stays under ignored `frontend/node_modules/.cache/`.
