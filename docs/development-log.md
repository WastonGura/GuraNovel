# Development log

Dates use Asia/Tokyo. Target: v0.12.0 desktop personal trial.

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
