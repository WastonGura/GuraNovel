# Development log

Dates use Asia/Tokyo. Target: v0.12.0 desktop personal trial.

## 2026-10-16 — #313 desktop acceptance consolidation

- M0–M2 are Squash merged: #314/#315 (single UI/desktop settings), #316 (delete/undo), #317 (comments), #318 (ideas), #319 (three outlines), #322 (creation form), #324 (copy/navigation). Baseline work that predated this plan is labeled regression coverage.
- Additional merged work: #321 removes Qodana, #325 adds Docker/version Actions gates, #327 enables blank-project first review, #329 negotiates identity transport, #331 configures server reasoning policy, #333 supports bounded long-chapter revision/review, #335 binds exact revision context, #337 rejects unchanged revisions, #346 patches audited frontend dependencies. Reader PRs are still pending; merged does not mean released. Product versions remain 0.11.0.
- Two synthetic real-model samples reached COMPLETED local finalization. The long sample also completed real Reader. Model quality is partial: requested edits can be applied, but omissions, length overruns, continuity blind spots and invalid reports remain. No literary-quality guarantee or retrospective full TDD claim.
- Added docs/acceptance/v0.12.0-desktop.md and a sanitized, deduplicated 60-call usage ledger with model/input/version lineage, elapsed times, reported tokens and failures. Raw credentials/novel workspaces remain outside Git. The final release-commit Docker/UI verification is pending, distinct from earlier model runtimes.
- This PR relates to #313 and does not close it: broader setting/mobile/legacy/export acceptance is deferred per the accepted plan. Release preparation will update all version declarations and upgrade notes in its own PR.

## 2026-10-16 — #345 frontend dependency advisory maintenance

- The candidate Docker build reported 11 npm audit entries (7 high, 4 moderate), including development/transitive and unused server/RSC paths; this is not proof of exploitability in the static desktop app.
- npm updated react-router-dom 7.18.1 → 7.18.4 and vitest 4.1.10 → 4.1.12, then resolved compatible transitive patches without --force. npm audit reports zero known vulnerabilities at verification time. Product version remains 0.11.0 in this independent maintenance PR.
- Existing author-route tests and latest-head frontend lint/unit/types/build/design/Chromium and Docker checks remain required in Actions. Dependency maintenance uses regression verification, not a fabricated new TDD Red. Release preparation will incorporate these locks while retaining its own 0.12.0 version update.

## 2026-10-16 — #338 Reader budget settlement and live result

- Actions exposed a deterministic full-lifecycle regression: a standard four-reader/two-issue discussion lost one reader because successful calls kept charging the full enlarged output reservation. The local PostgreSQL reproduction returned six final ballots instead of eight.
- Red: the new three-call case completed only two readers despite small valid outputs; the two-call case charged 8192 instead of the observed 1187 UTF-8 bytes. Green: successful validated output settles its reservation to serialized UTF-8 bytes in the same locked transaction. In-flight, unknown and failed attempts retain the full reservation. These counters are conservative charges, not reported model token usage. Per-call and total bounds remain unchanged.
- Validation: all 112 service unit tests and 18 PostgreSQL Reader integration cases pass, including concurrent full lifecycle, oversize rejection, failure recovery and real-adapter route fixtures. Existing exact-call-budget assertions now check settled bytes while retaining the two-call limit; this is an explicit accounting behavior change.
- The isolated real-model long sample completed two-reader cold reading, discussion, final ballots and handoff at backend 7f8db09; simulated=false and server model deepseek-flash were verified. It then reached COMPLETED local finalization. Reader advice raised pacing/motivation issues; this does not imply literary quality approval. Its earlier input/output budget failures remain recorded. Latest settlement code still requires latest-head Actions. Added a retained-total-budget regression: after one small success settles, a second call is still refused if a full reservation cannot fit; oversize/unknown-attempt checks remain intact. Six affected boundary/recovery cases pass.

## 2026-10-16 — #338 output reservation verification

- The Chinese multi-finding output test now passes with a 32768-byte per-call reservation; the other long-input and provider-provenance cases also pass. All 111 service unit cases pass, including explicit budget exhaustion.
- The exact-two-call budget test now states its intended 4096 per-call reservation explicitly, keeping its original 8192 total-budget assertion independent of default changes. This fixture adjustment is not a new behavioral Red.
- Live Reader report and release acceptance remain pending. Application versions remain 0.11.0 until a dedicated release preparation PR.

## 2026-10-14 — #338 bounded Chinese Reader input and truthful source

- Status: implementation; PR/latest-head Actions/review and live Reader acceptance pending. No migration or app version bump.
- Diagnosis correction: studio_* aliases already load through ProfileRegistry. No persona ID/UI change is needed; the initial ID hypothesis in #338 was corrected before implementation.
- Red: a PostgreSQL HTTP test with the production adapter and streamed transport fixture rejected a normal Chinese chapter before cold reading because the 8192 per-call reservation counts UTF-8 bytes. A second test reported simulated=true for a configured real provider.
- Green: default non-off per-call reservations are 65536, still bounded by total input/call/time budgets and the provider's input policy. This is a conservative reservation, not actual token usage. New sessions snapshot server-owned provider/model configuration without credentials/endpoints; unknown providers remain unverified.
- Validation: both new PostgreSQL cases pass, with two actual adapter/transport calls and complete Chinese source retained. 126 provider/service unit cases pass, retaining explicit low-budget failure coverage. The test stream fixture was corrected after an intermediate pre-consumed-response error; that fixture error is not claimed as a product Red.
- Existing sessions retain their immutable original configuration/source snapshot. A failed disposable live sample is retained; a new invitation is required to use the revised budget. Actual model quality remains separate from the streamed fixture evidence.
- Live cold reading then produced two structurally valid reports (10.66/16.44 s; 7674/7661 input and 1664/2898 output tokens), but the old 4096-byte output reservation rejected them after invocation. A new Chinese multi-finding response test reproduced that failure. Default per-call output reservation is now 32768, preserving the 1000000 total reservation and provider output bounds. Actual tokens and reserved bytes remain distinct in acceptance records.

## 2026-10-16 — #332 latest-head CI follow-up

- Actions found an old review-saga limit test still expecting 65 targets to overflow. Updated its fixture to 1025, preserving rejection above the new shared 1024-locator bound. This is maintenance of an existing boundary assertion, not a new TDD Red.
- PR #333 remains pending latest-head Actions and review; no release or version change is claimed.

## 2026-10-14 — #336 reject unchanged feedback revisions

- Status: implementation; PR/latest-head Actions/review and real quality revalidation pending.
- Real-model evidence: deepseek-flash with reasoning_effort=none returned 104 unchanged paragraphs in 34.01 s (18731 input / 10305 output tokens), despite a valid new candidate identity. This sample failed revision quality and is retained as failure evidence.
- Red: a PostgreSQL provider fixture returning all original targeted prose was incorrectly accepted with HTTP 200 and a new draft version. Green: unchanged user-feedback candidates are rejected as invalid provider output before persistence, leaving the original version and a retryable FAILED task. The same immutable submission can be retried, then replayed without a third provider call. Both timeout and unchanged-output integration cases pass.
- Prompt profile user_feedback_revision advances from v2 to v3 and explicitly distinguishes preserving identity/unrequested prose from performing the requested edits. Text change alone is not a semantic quality guarantee; actual model revalidation remains required. No app version bump or migration.

## 2026-10-14 — #334 exact outline context and revision failure recovery

- Status: implemented; PR/latest-head Actions/review pending. No migration or version bump.
- Red: a PostgreSQL Studio revision with the real context assembler returned 422 before model invocation; the author-phase snapshot omitted approved outline content, and recording provider failure saw an old run projection. Earlier deterministic-provider tests did not enforce this context requirement.
- Green: the author phase reloads and validates the exact approved outline, then includes its version content in the pure provider request. Failure recording reloads the committed run projection. The new HTTP test verifies a provider timeout becomes FAILED, keeps the original draft, retries the same immutable submission successfully and replays without another provider call.
- Focused recovery integration and 20 handoff/facade/architecture tests pass. Real-model quality remains in #313 acceptance.

## 2026-10-14 — #332 long chapter revision and review

- Status: implementation and focused verification; PR/latest-head Actions/review pending. No version bump or migration.
- Red: the real-model draft had 104 paragraph locators; the PostgreSQL HTTP test reproduced a 500 when submitting whole-chapter requirements. This is a new behavioral test, not a retrospective TDD claim for earlier Studio code.
- Green: revision and review share the document layer's existing 1024-locator bound; the independent request/output byte budgets remain enforced. Initial outline sections remain limited to 64. No locator regrouping or silent truncation occurs.
- The same test exposed stale ORM author-action state across fresh phase sessions. Expiring the request's committed reads before the handoff allows persistence to observe the committed action resolution.
- Validation: 78 focused PostgreSQL cases pass, including a 104-paragraph revision, exact replay, immutable source binding and subsequent review/local finalization; 108 writer/reviewer/segment/handoff/primitives cases pass. The deterministic provider adds one paragraph per revised paragraph (208 reviewed locators); this proves transport and persistence behavior, not model quality. Real-model revalidation remains pending.
- Live revalidation exposed a second 64-ID bound in typed provider-attempt recovery evidence. Its new 104-ID roundtrip test failed before the change; 64 attempt-contract/store cases now pass, including rejection above 1024. The interrupted disposable QA run requires explicit no-write reconciliation before retry; no model call was observed for that attempt.

## 2026-10-14 — #330 server reasoning budget

- Status: implementation and focused verification; PR/latest-head Actions/review pending.
- Red: the compatible-provider behavioral fixture rejected calls because the requested server budget never reached the HTTP body. Green: validated optional profile policy propagates to the transport; omission retains provider defaults. 80 gateway/transport/outline cases pass.
- Server configuration supports none/low/medium/high/max for initial outlines, V2 writer/revision/review and Reader; clients cannot override it through author APIs. Strict output, lineage, size and timeout policies remain intact.
- Live outline sampling succeeded with deepseek-flash (860 input / 4366 output tokens, 31.22 s). High-default reasoning on the longer prose call remained unsuccessful; subsequent quality sampling explicitly records none and a 120 s bound. No claim that real prose/revision quality has passed yet. No version bump.

## 2026-10-14 — #328 bounded model transport negotiation

- Status: implemented; PR #329/latest-head Actions/review pending. No version bump.
- Red: a compression-capable mock service returned valid gzip after default compression negotiation, which the bounded raw transport rejected. Green: explicitly request identity; all 20 transport cases pass, retaining unexpected-compression and size-limit rejection.
- Live read-only provider diagnostics confirmed identity negotiation is honored. Model quality remains a separate acceptance item.

## 2026-10-14 — #323 release CI gates

- Status: CI implementation; PR/latest-head Actions pending. No version bump.
- Version check Red: five independent manifest/API/lock mismatch injections were incorrectly accepted; Green: mismatches now fail and the six current version declarations agree. Focused stdlib tests pass using uv.
- Added a Docker build/health/real-HTTP author workflow gate through the frontend proxy, including persisted ideas, exactly three candidates, replay identities, selection, approval, prose save, review and idempotent local finalization. Both new jobs feed All Checks Passed.
- Branch pushes no longer duplicate their PR checks: pull requests and main pushes remain gated, with manual dispatch available. This reduces queue pressure during stacked PR delivery without skipping any PR's latest-head validation.
- Local clean-checkout Docker build at 7e31742 passed; services became healthy. Actual API acceptance exposed a previously uncovered 422 when reviewing a blank project's first chapter. This is a product gap to fix separately, not a reason to weaken the smoke gate. Deterministic Provider evidence is separate from real-model quality.

## 2026-10-14 — #328 bounded model transport negotiation

- Status: implemented; PR/latest-head Actions/review pending. No version bump.
- Red: a compression-capable mock service returned valid gzip after the client's default compression negotiation, which the bounded raw transport correctly rejected.
- Green: explicitly request identity encoding, matching the transport's accepted representation. Unexpected compression and oversized responses remain rejected; existing transport cases are retained.
- Live read-only provider diagnostics confirmed identity negotiation is honored. Live model quality is a separate stage-acceptance item.

## 2026-10-14 — #326 first-chapter review

- Status: implementation and focused verification; PR/latest-head Actions/review pending.
- Red: the real Docker author smoke and a PostgreSQL HTTP test returned 422 at first-chapter Editor review when no optional context documents existed. Existing tests had seeded those documents.
- Green: optional review context may be empty while exact prose, approved outline, project/chapter scope and version validation remain mandatory. Empty bound collections preserve their own revision instead of using legacy fallback. The blank-project test now reaches COMPLETED local finalization; all 32 review-agent contract cases pass.
- Updated the former mandatory-optional-context assertion to require empty context with intact prose/outline identity; cross-project, wrong-kind and evidence checks remain. No migration, version bump or real-model quality claim.

## 2026-10-14 — #320 remove Qodana

- Status: reviewed and Squash merged through PR #321 at a1b05a0; no release or version bump.
- Removed the Qodana workflow at the maintainer's explicit request. GitHub Actions ci.yml remains the canonical quality gate; no token or license update is needed.
- Validation: diff review and Actions run 38515444409 passed, with unrelated product jobs skipped by path filtering. This configuration change does not claim behavioral TDD evidence.

## 2026-10-14 — #309 author capability and status copy

- Status: local implementation and focused verification; PR/latest-head Actions/review pending.
- Red: no accessible unavailable-settings control, English creation stages, and unwired setting discussion presented as a real Agent. Three behavior tests failed before implementation.
- Green: unavailable preferences and setting discussion have explicit disabled controls; examples stay in preview mode. Dashboard/forms and creation stages use Chinese author terms, review identities/severity are translated, and local finalization is distinct from an external publication.
- Validation: 118 affected Dashboard/Studio/setting/navigation cases and the focused Chromium creation/keyboard case pass. Existing test selectors were updated for translated labels. Full gates run in Actions.
- Docker interaction exposed a stale `stage=Draft` URL after submission. The explicit-route regression failed before the fix; progress now synchronizes the URL and stale route recovery does not display a misleading author warning. All 69 Studio/navigation cases and focused ESLint pass. Remaining review progress text is translated to Chinese.
- Actions exposed an older Chromium scenario that generated a demo proposal in the formal setting discussion. Updated it to verify the accepted capability boundary: discussion/example actions are disabled, no preview proposal appears and no apply request occurs. The focused Chromium scenario passes; proposal application backend coverage remains independent of this unavailable UI.
- README distinguishes the desktop delivery scope, future capabilities and deterministic Provider evidence. No model quality or release claim; version unchanged.

## 2026-10-14 — #306 author creation form

- Status: implemented and focused verification; PR/latest-head Actions/review pending. No version bump.
- Red: the title did not receive focus, cancelling did not return focus, and Chromium Shift+Tab did not cycle inside the modal.
- Green: a native modal makes the background inert; explicit Tab boundary handling cycles within visible controls, Escape restores trigger focus, and pending submissions keep the modal open. Chinese titles generate a stable Slug; manual overrides live under advanced options.
- Thirteen Dashboard component tests pass, including retained inputs, conflict-message focus, double-submit suppression and stable retry Slug. One focused Chromium keyboard/small-desktop case passes at 1062×660. Intro height is bounded and the submit action stays reachable.
- Creation uses the existing project endpoint and transaction; uncertain response copy directs authors to inspect the bookshelf before another create attempt. No new server idempotency guarantee is claimed.

## 2026-10-14 — #299 three outline candidates and author editing

- Status: implementation and focused verification; latest-head Actions/review/merge pending.
- Red: generation API returned 404 and the formal generate button remained disabled. Empty-project-to-V2 acceptance additionally exposed missing project ownership required by the existing production pipeline.
- Green: three distinct persisted candidates, batch/history/query identities, version-checked selection, scoped author editing, approval and post-draft locking pass PostgreSQL cases. The UI test explicitly compares alternatives and selects without approving; 32 affected navigation tests pass, including confirmation of the just-flushed outline version.
- Batches snapshot current setting versions, chapter ideas and preceding prose before a transaction-free model call. Requests are durable and replayed by ID; uncertain results block new calls until explicit author abandonment. Batch and selected-outline evidence cannot be rewritten through generic document operations.
- Uses the existing structured-output gateway and OpenAI-compatible server configuration, plus an explicitly labeled deterministic Provider. No real-model quality claim until separate sampling.
- Real-adapter validation found missing strict output-model configuration before any model request. Red: an actual gateway plus transport-fixture case raised the same policy rejection; Green: strict root/nested models pass, preserve three options and record token usage. Transport allocation now follows successful profile validation. Live quality sampling remains separate.
- Follow-up acceptance: strict candidate UUIDs must be reconstructed at the HTTP response boundary; persisted model output survived a response-validation failure and can be queried after this fix. All four outline PostgreSQL cases pass. A browser-created `stage=Outline` URL also needed to advance to `stage=Draft` after approval; its failing navigation assertion now passes with all 33 affected navigation cases.
- Fixed Actions diagnostics in dependency branches: narrowed recovered text/version types, corrected test role options and fixture imports. These CI fixes are included here too. Version remains 0.11.0 pending the release preparation PR.

## 2026-10-14 — #303 persisted chapter ideas

- Status: local implementation; PR/Actions pending. Ideas are stored per authoritative project/chapter with expected revisions and request identities; drafted/final chapters are read-only.
- Red: the idea endpoint returned 404 and Studio ignored saved ideas. A stale local idea had no explicit server-version resolution action.
- Green: targeted PostgreSQL persistence/idempotency/stale-write/wrong-project/read-only scenario passes; all 30 Studio navigation cases pass, including save-before-settings, explicit conflict resolution and lost-response retries with the same request ID.
- UI debounces writes, retains scoped recovery snapshots and blocks navigation on failed saves. Strict-effect stale reads cannot overwrite a later load. Full gates run only in Actions; uv is used locally. Version unchanged.

## 2026-10-14 — #302 version-bound setting comments

- Status: implemented locally; dependent PR and Actions pending.
- Red: API returned success but omitted comment metadata; source editing never flushed comment anchors to the server; reload lost the feedback recovery snapshot; deleting a quoted span did not mark its comment orphaned.
- Green: content and validated UTF-16 comment anchors share one optimistic document write/version. Metadata records the exact resulting source version and the immutable version retains feedback. Stale writes do not alter comments. Affected setting regression tests and the PostgreSQL round-trip are run locally via uv; full gates run in Actions.
- UI save queues track text plus comments, flush while editing, retain failed snapshots including their original version, and preserve orphaned quotes. Existing unrelated draft recovery behavior is retained. No version bump.

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

## 2026-10-16 — #340 Reader genre context

- Red: cold-reading request tests for missing and empty project genre observed fantasy, an unsupported assumption. An explicitly supplied Chinese realistic-fiction genre already passed.
- Green: absent genre is now unspecified, explicit genres remain unchanged, and the genre-experienced profile advances v1 → v2 with instructions to apply only conventions supported by the manuscript or explicit genre. No project metadata or frozen Reader session is rewritten.
- Validation: 136 focused service, profile-registry and production-provider cases pass. This corrects context delivery; it does not guarantee the model follows the instruction. PR/Actions and final acceptance pending. No migration or application version bump.
