# Development log

Dates use Asia/Tokyo. Target: v0.12.0 desktop personal trial.

## 2026-10-14 — #298 durable setting deletion and undo

- Status: local implementation; review and latest-head Actions pending. New nullable document tombstone migration 0012 preserves content/version/reference identities; archived and foreign-owner restoration is rejected. A deleted-documents listing and idempotent undelete API support recovery after reload/restart.
- Red: PostgreSQL test failed because a repeated delete returned 404; UI tests failed because failed deletions removed notes and undo never called the server.
- Green: four targeted PostgreSQL cases pass; 42 affected setting component tests pass, including failed deletes, partial batch success, failed restore and retry. Python commands use uv against a disposable `_test` database.
- Frontend waits for confirmed per-item results, flushes content before delete, retains failed items and keeps failed undo actions retryable. Immutable task version reads remain available; deleted documents are excluded from new setting context.
- Full CI remains delegated to GitHub Actions. Qodana is currently blocked by the existing cloud license/token rejection; no merge bypass is authorized or performed. No version bump.

## 2026-10-14 — #305 unified frontend

- Status: implemented locally; PR and latest-head Actions verification pending.
- Removed legacy project/chapter/concept/maintenance/reader surfaces. Old URLs redirect to Dashboard/Studio; reader references remain version/session scoped. Historical V1 content stays read-only and no task is altered.
- Existing implementation: regression verification, not retrospective TDD. Prior local baseline: 419 unit tests and 11 Chromium tests passed; current PR's complete verification is delegated to Actions.
- Adopted the accepted uv/TDD/PR/v0.12.0 development plan. Original checkout and its unrelated backend changes are preserved. Initial outlines, durable delete undo/comments/ideas remain follow-up work.

## 2026-10-14 — #300 standalone setting workspace baseline

- Status: implemented locally; PR and GitHub Actions verification pending.
- Integrated the existing desktop container correction, read-failure defense, local draft recovery, conflict handling and author-confirmed proposal application. Original source checkout is preserved.
- Existing changes receive regression verification; this entry does not claim retrospective Red/Green development.
- Tests: setting workspace/unit regression and Chromium geometry, read failure, draft recovery and proposal scenarios. Full quality gates run in GitHub Actions.
- Limitations: mobile layout, durable comment metadata, delete recovery and Agent conversations remain separate issues. No version bump or release.
