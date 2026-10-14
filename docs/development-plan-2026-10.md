# v0.12.0 desktop personal trial plan

Accepted 2026-10-14 (Asia/Tokyo). Roadmap: #297. This is a plan, not a release claim.

## Goal and delivery sequence

Create a novel, persist chapter ideas, generate three outline candidates, compare/select/edit and approve one, generate and revise prose, review, optionally consult readers, and finalize locally.

1. M0: review/integrate the existing setting workspace baseline (#300) and remove the legacy UI (#305). Preserve unrelated working changes.
2. M1: durable setting deletion/undo (#298), comments (#302), chapter ideas (#303).
3. M2: three persisted outline candidates and author editing (#299), creation form (#306), accurate capability copy (#309).
4. M3: stage-specific release acceptance (#313), real-model sampling, version/release preparation PR.
5. Later: setting conversations (#301), history/archive (#311), new concept/maintenance/legacy-task operations, mobile (#304), metadata (#307), volume/rename (#308), assistant consistency (#310), export (#312). Keep #313 open until its full scope is delivered.

## Behavior and interface decisions

- Setting deletion is recoverable server-side, retaining document IDs, content, versions and references. Batch operations report per-item results. No permanent purge in the first trial.
- Comments persist content, anchors and source versions; unmatched anchors are explicitly orphaned.
- Ideas are stored per project/chapter with optimistic revisions and local failure recovery; conflicts never silently overwrite another tab.
- Each generation batch contains three titled outline candidates with summaries and full text. Persist input/context versions, task identity, state and output. A new batch retains earlier batches.
- Viewing candidates never replaces edits. Explicit selection saves the current edits and materializes a selected-outline document. Author edits autosave; approval binds the visible version. Starting prose production locks the outline.
- Add idea read/write, generation create/query, candidate selection and selected-outline save interfaces; reuse document versioning, outline approval and V2 prose workflows.
- Resolve uncertain requests by their original identity before retrying; do not duplicate model generation. Legacy V1 tasks remain read-only until explicit continuation support exists. Reader is optional; local finalization does not publish externally.

## TDD and verification

For every new behavior: Red (a behavior assertion fails for the missing feature), Green (minimal implementation), Refactor (preserve behavior and rerun affected tests). Import/environment failures are not valid Red evidence. Existing implementations receive regression tests and are not described as retrospectively TDD.

Local checks are narrowly targeted. All Python installation and execution uses uv (`uv sync --frozen --all-groups`, `uv run ...`). Full CI runs in GitHub Actions: Ruff, architecture/unit, dedicated PostgreSQL integration/migrations, frontend lint/unit/types/build/design, Chromium E2E, version consistency and Docker/backend smoke tests.

Acceptance covers empty novel through finalization, optional Reader, delete/undo/reload, persisted comments/ideas, refresh/back/chapter switching, concurrent tabs, 409/network failures, lost responses, duplicate clicks and partial batch failures. Real-model sampling is separately recorded with model/input versions, latency/usage and checks for candidate diversity, setting consistency, revision fidelity and actionable reviews.

## PRs, version and development log

Use short-lived issue branches and Conventional Commits; normally one closing issue per PR. Describe behavior, API/migrations, Red/Green evidence, latest-head Actions checks, manual acceptance and limitations. Review and squash-merge only after the current head passes CI; do not push main directly. Record Tokyo dates, issue/PR, evidence and actual delivery status in development-log.md.

Keep feature PR versions unchanged. A dedicated release preparation PR synchronizes frontend/backend/API versions and lockfiles to 0.12.0, documents migrations, upgrade instructions and limitations, and adds release notes. Never rewrite published migrations. Tag/release only a merged and verified commit; never move a published tag.

Public deployment, multiple authors, external publishing, EPUB and complete mobile support are outside the first trial. No promised release date before acceptance.
