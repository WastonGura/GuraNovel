# Development log

Dates use Asia/Tokyo. Target: v0.12.0 desktop personal trial.

## 2026-10-14 — #305 unified frontend

- Status: implemented locally; PR and latest-head Actions verification pending.
- Removed legacy project/chapter/concept/maintenance/reader surfaces. Old URLs redirect to Dashboard/Studio; reader references remain version/session scoped. Historical V1 content stays read-only and no task is altered.
- Existing implementation: regression verification, not retrospective TDD. Prior local baseline: 419 unit tests and 11 Chromium tests passed; current PR's complete verification is delegated to Actions.
- Adopted the accepted uv/TDD/PR/v0.12.0 development plan. Original checkout and its unrelated backend changes are preserved. Initial outlines, durable delete undo/comments/ideas remain follow-up work.

## 2026-10-14 — #320 remove Qodana

- Status: reviewed and Squash merged through PR #321 at a1b05a0; no release or version bump.
- Removed the Qodana workflow at the maintainer's explicit request. GitHub Actions ci.yml remains the canonical quality gate; no token or license update is needed.
- Validation: diff review and Actions run 38515444409 passed, with unrelated product jobs skipped by path filtering. This configuration change does not claim behavioral TDD evidence.

## 2026-10-14 — #300 standalone setting workspace baseline

- Status: implemented locally; PR and GitHub Actions verification pending.
- Integrated the existing desktop container correction, read-failure defense, local draft recovery, conflict handling and author-confirmed proposal application. Original source checkout is preserved.
- Existing changes receive regression verification; this entry does not claim retrospective Red/Green development.
- Tests: setting workspace/unit regression and Chromium geometry, read failure, draft recovery and proposal scenarios. Full quality gates run in GitHub Actions.
- Limitations: mobile layout, durable comment metadata, delete recovery and Agent conversations remain separate issues. No version bump or release.
