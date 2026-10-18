# Development log

Dates use Asia/Tokyo. Target: v0.12.0 desktop personal trial.

## 2026-10-19 — #356 approved hover-halo delivery

- The user approved the final appearance and authorized PR submission for #355/#356 only. Delivery is split per issue: `fix/356-remove-hover-halos` targets main; the fixed-layout PR follows on top of it so the shared stylesheet remains reviewable. No merge is authorized by this submission request; #357 remains pending.
- #356 removes the pointer-following gray mask, pointer/click tracking and halo-only attributes/animation from bookshelf controls and its dialogs. Cover shadows, search motion, normal press states and keyboard focus remain. The browser fixture and halo behavior check are separated from fixed-layout checks to keep the first PR independent of #355.
- Validation evidence: the earlier halo check failed while the hovered overlay remained visible; the approved implementation passed the halo/create/cancel browser behavior, 63 targeted component checks and lint/types/build. Final PR-specific verification and latest-head Actions are recorded with the PR. Existing implementation regression coverage is not presented as new TDD evidence.
- No API, dependency, migration, version or release change. Earlier local-only entries below record the draft at that point in time and are superseded by this approval; published tags remain unchanged.

## 2026-10-19 — #355 clarification: fixed page shells, not hidden scrollbars

- The user clarified that the homepage must not scroll at all, and other page shells must stay fixed too. This supersedes the earlier interpretation and entries preserving dashboard wheel/PageDown scrolling. Updated issue #355's scope and acceptance criteria; #356's halo removal remains in place and #357's dialog redesign is still pending.
- Fixed root/app/dashboard viewport boundaries. The bookshelf now allocates available height among its three shelves and search area, shrinking image areas and spacing while retaining text/actions. Horizontal shelf navigation and designated content scrolling remain available; the homepage cannot be scrolled by wheel, PageDown or focus changes. Corrected transparent shadow padding intercepting the add buttons after compression, and removed the remaining unused creation-close halo marker.
- Red: the clarified browser check failed because the settings shelf extended to about y=939 at 1280×720. Green: populated and empty bookshelf checks pass at 1920×1080, 1440×900, 1280×720, 1062×660 and 390×844, including fixed greeting/outer scroll position, visible shelves/search, arrow navigation and modal focus. The existing Studio/setting layout checks were strengthened as regression coverage: wheel events do not move their outer page, while long setting content scrolls inside its document region with the header fixed.
- Verification: 19 targeted Chromium checks and 63 Dashboard/Studio/setting component tests pass; lint and types/production build pass, with the existing bundle warning. Inspected the populated 1280×720 fixture screenshot; evidence remains outside Git under external evidence/fixed-viewport-review. The live 15386 preview serves the updated fixed-layout source. HTTP fixtures are not real-backend/model acceptance evidence.
- Status remains local only, uncommitted/unpushed on `fix/355-dashboard-scrollbar`. No PR, release, version, dependency or API change; wait for the user's visual approval before PR submission.

## 2026-10-19 — #355 / #356 ready for local visual review

- The user prioritized only the two homepage fixes. Both are implemented locally: the dashboard's vertical scrollbar is hidden while wheel/PageDown and focus navigation still reach lower content; the pointer-following gray halo and its tracking/animation are removed while cover shadows and keyboard focus remain. #357 remains unimplemented pending design confirmation.
- Confirmed the active preview at `http://127.0.0.1:15386/` serves the updated source and scrollbar rule, rather than an older build. Re-ran the two targeted Chromium behaviors plus creation-dialog focus/close coverage: all 3 pass. This is final verification of the previous entry's Red/Green work, not a new TDD claim. The prior 17 Dashboard tests, lint and types/build results remain applicable; no application source changed during this follow-up.
- Development status: local implementation only on `fix/355-dashboard-scrollbar`, uncommitted/unpushed; no PR, merge, issue closure, version change or release. Await the user's visual confirmation before PR submission; complete remote gates will run through GitHub Actions after that authorization.

## 2026-10-18 — #355 / #356 local bookshelf surface fixes; #357 design pending

- Split the user's follow-up into [#355](https://github.com/WastonGura/GuraNovel/issues/355) (homepage scrollbar), [#356](https://github.com/WastonGura/GuraNovel/issues/356) (pointer-following gray halos) and [#357](https://github.com/WastonGura/GuraNovel/issues/357) (unified novel creation/details/metadata design). Proposed a shared cover-and-content dialog with directory/information views; implementation of #357 awaits the user's confirmation, including clarification of the extra landscape image in the supplied reference.
- Local fixes on `fix/355-dashboard-scrollbar`, based on merged main `5e9cdc2`: hide the dashboard scrollbar with standard styling and the WebKit fallback while retaining overflow scrolling. Remove the pointer/click halo layer, its mouse tracking and dedicated animation/attributes; retain cover shadows, normal pressed/focus states and search transitions. No dialog layout redesign, persistence, API, dependency or version change.
- TDD Red: corrected HTTP fixtures and control names first (those setup failures are not Red), then confirmed the actual browser failures: scrollbar policy remained `auto`, and hovered controls painted the gray overlay. Green: the two new browser checks plus the existing creation-dialog check pass, covering reachable lower content, no document/horizontal scroll, full-text control access, empty/populated shelves, keyboard focus and create/cancel behavior. All 17 Dashboard component tests, lint and types/production build pass; the existing bundle-size warning remains.
- Latest preview is available at 15386. Changes remain local and uncommitted/unpushed, with no PR or new Actions result claimed. Wait for the user's visual approval before PR submission. Original private design images and local runtime/evidence are not committed.

## 2026-10-18 — #353 approved outline UI delivery

- Status: the user approved the final local appearance and authorized PR submission after verification. Delivery: [issue #353](https://github.com/WastonGura/GuraNovel/issues/353) → branch `fix/353-outline-design-and-motion` → [PR #354](https://github.com/WastonGura/GuraNovel/pull/354), open for review. Latest-head Actions must pass before any merge; this delivery does not create a release. The older “awaiting visual approval” entries below record earlier iterations, superseded by this approval and the final plain-text actions.
- Final scope: share the designed outline input/three-paper surface across real chapters and preview; animate splitting/merging and text disappearance/appearance; capture outgoing glyphs before DOM mutation so resize/scroll cannot reuse stale coordinates. Respect reduced motion. History arrows browse completed result groups only, leaving the idea and selected outline intact. Hide card scrollbars while retaining focusable full-text scrolling.
- Restore the original collapsed Gura circle, remove duplicate bookshelf navigation, use neutral chrome and pure-text edit/adopt actions with darker text and underline feedback. Remove redundant success/implementation copy while retaining progress, failures, conflicts, retry and preview disclosures. The expanded assistant panel is not redesigned. Explicit server-version replacement now clears obsolete setting recovery copies only after the server read succeeds.
- Verification on the final approved source: 154 focused component tests (8 files) and 12 Chromium checks (outline split/return, dust alignment after resize/scroll, reduced motion, monochrome hover, history layout, creation focus, setting recovery and assistant) pass. ESLint, types/production build and design-document lint (0 errors/warnings) pass. The existing >500 kB bundle warning remains. Chromium uses declared HTTP fixtures; these results are not real-model quality evidence. Earlier entries distinguish captured Red/Green evidence from regression coverage for already-written code.
- Remote evidence: [Actions 39128192545](https://github.com/WastonGura/GuraNovel/actions/runs/39128192545) passed for PR head `5e86849695d8595cec8421788425e11a1fef54a8`: all 450 frontend unit tests (24 files), all 21 Chromium checks, frontend lint/build/design gates, version consistency and the actual Docker author workflow. Backend-only jobs were intentionally skipped by path filtering. This documentation follow-up changes no application source; its latest-head CI evidence is recorded in PR #354. PR remains unmerged and awaiting review.
- No API, dependency, migration or application-version change. v0.12.0 remains the target/baseline; the published tag is untouched. #313 and the broader deferred functionality remain open. Only this clone's reviewed UI/tests/assets/log are included; local runtime configuration and evidence stay outside Git.

## 2026-10-18 — local plain-text outline actions, awaiting visual approval

- Supersedes the prior outlined selection bar: “编辑思路”/“返回方案” and “采用此方案” now use transparent, borderless text controls. Hover deepens the gray text to black and reveals an underline; keyboard focus uses a thicker underline instead of a button outline. Removed the circular arrow, fills and shadows. Real/preview adoption labels stay shared; target identity, explicit selection and save/version behavior are unchanged.
- TDD Red: the existing browser hover check still saw the old white-on-black selection treatment instead of a dark underline. Green: all six outline Chromium checks pass, including both controls' transparent backgrounds, absent borders/shadows, stable hitboxes, scrolling, transition timing, reduced motion and dust positions. All 73 Studio/navigation component tests pass. Inspected the final real-page underline after simplifying the action's formatting context so the underline renders on its child label.
- Lint and types/build pass; the existing bundle-size warning remains. Screenshot: external evidence/outline-text-actions.png (first action focused). No adoption, generation or content edit was invoked during visual inspection. Local-only on 15386; no commit, push, PR, version or release change. Await user approval before PR submission.

## 2026-10-18 — local monochrome interactions and outline actions, awaiting visual approval

- Replaced chromatic CSS chrome/status/focus accents with neutral black/white/gray across bookshelf/forms, Studio, setting workspace and assistant, including search highlights and input focus. Status labels and failure/recovery controls remain explicit. Authored cover assets and stored annotation data are not rewritten.
- Reworked the real and preview outline selection affordance into a shared full-width card-footer action: “采用此方案” with a circular arrow. Hover/keyboard focus invert to black with white text; the arrow moves subtly and pressed/disabled states remain distinct. Explicit adoption identities, autosave/version checks and confirmation behavior are unchanged. Text actions gain neutral hover shells, inset edges and pressed feedback; outline secondary actions also reveal an underline. Card geometry stays fixed during hover to preserve particle alignment.
- TDD Red: the existing adoption check still found the old visible label, and the browser color check found blue text. Green: 83 focused Studio/navigation/history/motion tests pass, including exact candidate adoption. Six outline Chromium checks verify monochrome text, visible hover feedback without hitbox movement, full-text scrolling, both transition directions, reduced motion and outgoing dust positions. Six additional creation/setting/assistant Chromium checks pass. Lint/types/build pass; the existing bundle-size warning remains.
- Inspected the live retained-backend cards and keyboard focus without adopting, generating or editing content. External evidence/outline-monochrome-actions.png shows idle actions; outline-monochrome-focus.png shows the equivalent black/white focus feedback. Latest code is served on 15386. This remains an uncommitted/unpushed local visual draft with no PR, version or release change, awaiting user approval.

## 2026-10-18 — local outgoing-text position correction, awaiting visual approval

- Fixed real outline-to-idea dust using cached glyph coordinates captured during an earlier render. CSS unfolding, viewport changes and card scrolling made that cache stale, so disappearing text could jump or restore offscreen titles. The outline motion boundary now captures the actual outgoing DOM in React's pre-mutation snapshot lifecycle and starts the dust after the update. Same-key typing/status renders do not start new animations; cancellation/unmount cleanup and reduced-motion behavior remain in place. Other motion surfaces retain their existing implementation.
- TDD Red: a Chromium check comparing the actual canvas text draw coordinates with the visible heading after resizing/scrolling failed against the old cache. Green: that check now passes, including exclusion of a scrolled-away first-card label. All six outline Chromium checks, 83 focused Studio/navigation/history/motion tests, lint and types/build pass. Corrected the boundary's empty-state TypeScript type before the successful final build; the existing bundle-size warning remains.
- Manually scrolled an existing real-backend card and returned to the idea, confirming the visible scrolled content dissolves in place. No generation, adoption or content write occurred. External evidence/outline-dust-position-before.png and outline-dust-position-after.png capture the real page; outline-return-position-fixed.webm records the HTTP-fixture transition check. Restarted the stopped frontend on 15386; the retained backend/database are healthy. This remains an uncommitted/unpushed local iteration, with no PR/version/release change, pending visual approval.

## 2026-10-18 — local return-to-idea motion timing, awaiting visual approval

- Fixed the return transition showing input controls while the three papers were still merging. The return now gives outgoing dust a short lead-in, closes the gaps continuously over 640 ms and reveals the idea heading/input/controls only after the geometry settles at 1000 ms. Incoming text waits for that same layout boundary before measuring and revealing its rows. Initial idea loading and the forward split retain their timings; reduced motion skips the wait.
- Used a distinct return-entry animation so the footer restarts correctly rather than reusing the completed forward animation's playback position. The same paper nodes remain mounted; editing the idea does not create a generation or change the selected outline.
- TDD Red: a Chromium frame-sampling check found visible return controls during the paper morph. Green: all five outline Chromium checks pass, covering forward split, reverse merging, retained idea/hidden history, scrolling and reduced motion. All 83 focused Studio/navigation/history/motion tests and lint/types/build pass; the motion unit check also verifies delayed reveal and interruption cleanup. Final CSS animation-name adjustment was verified in Chromium; the existing large-bundle warning remains.
- Manually browsed existing real-backend results and returned to the idea at 1280×720 without generation, adoption or content writes. External evidence/outline-return-smooth.png captures the settled page; outline-return-smooth.webm records the HTTP-fixture motion check, not real-model acceptance. Latest changes remain local on 15386, uncommitted/unpushed, with no PR, version or release change. Await user visual approval before PR submission.

## 2026-10-18 — local outline scrollbar appearance, awaiting visual approval

- Hid the scrollbar only inside real outline candidate cards using standard scrollbar styling plus the WebKit fallback. Retained overflow scrolling, focusable full-text regions and the fixed selection action; other editors are unchanged.
- Extended the existing saved-chapter browser check with overflowing content and actual wheel scrolling. All three outline Chromium checks pass, including split motion and reduced motion. This CSS appearance change uses regression/visual verification; the scroll check also passed before styling, so no failing behavior Red is claimed. Verified the live retained-backend cards at 15386; screenshot stays outside Git at external evidence/outline-cards-no-scrollbars.png. No content/model writes, commit, push or PR; user approval is pending.

## 2026-10-18 — local outline result history and text motion, awaiting visual approval

- Kept the operational/progress copy identified in the user's screenshots. Idea editing no longer shows a batch counter; arrows count and browse only completed generations with candidates. Failed/running/uncertain/cancelled attempts remain in server/task recovery but do not consume a visible result number. Loading defaults to the newest successful result, even when a later attempt failed; the current idea draft is preserved. Failure feedback remains actionable without calling the idea a batch.
- Confirmed that incoming text already uses a soft, line-aware left-to-right reveal, paired with outgoing particle dissolution. The real outline alternatives bypassed it. Reused the existing motion for real result changes and idea/result transitions, with a hidden input layout mirror; kept the paper-split transition and reduced-motion behavior. Manual inspection exposed particles for offscreen, scroll-clipped content; snapshot capture now excludes clipped glyphs instead of painting hidden paragraphs below the cards.
- TDD Red: four batch/navigation checks failed for inappropriate counting/default selection; a Chromium frame check found no real-outline dust; a glyph-visibility check captured hidden text. Green: 83 focused Studio/navigation/history/motion tests and eight Chromium split/editing/layout checks pass, including reduced motion; lint and types/build pass. Fixed a fixture typing error and avoided new Fast Refresh warnings during refactor. The pre-existing bundle-size warning remains.
- Verified existing real-backend history switching and return-to-idea at 1280×720 without generation, adoption or content edits. The two successful result groups now show 1/2 and 2/2, while the idea has no counter. Screenshot: external evidence/outline-idea-without-batch.png. Automated motion checks use HTTP fixtures, not model-quality evidence. Latest code is served at 15386 with the retained healthy backend/database; no version, migration, commit, push, PR, merge or release was made. Visual approval is still pending.

## 2026-10-18 — local UI copy cleanup, awaiting visual approval

- Removed routine save-success labels, the server-storage footer, visible setting revision numbers, proposal UUID/version/agent-role metadata, repeated assistant implementation notes and creation-dialog filler. Real setting discussion keeps a concise unavailable placeholder instead of inactive example controls and API explanations. Loading/saving progress, failures and retry actions, conflicts, read-only states, preview disclosures, proposal reasons and shared-setting impact remain visible. Persistence and version checks remain in place.
- TDD Red: eight focused behavior checks failed against the redundant copy before implementation. Regression exposed a conflict-reload defect: choosing the server version dismissed the banner but an old session recovery copy restored the discarded draft over the freshly read text. Clear the obsolete body/feedback recovery copies only after the explicit reload succeeds; the strengthened test now checks the actual displayed server content and recovery-key removal.
- Green: 143 tests across Dashboard, Studio/navigation, assistant, setting collection and setting editor pass; lint and types/build pass. Nine Chromium checks cover creation focus/layout, setting layout/read failure/draft recovery/unavailable discussion, assistant interaction and outline split/reduced motion. The existing launcher asset assertion was updated to check its accessible collapsed state; that earlier implementation is regression coverage, not a new TDD claim. The existing large-bundle warning remains.
- Browser checks/screenshots use declared HTTP fixtures, not a running backend or real-model acceptance. Inspected the captured setting, discussion and bookshelf states; evidence stays outside Git under external evidence/ui-copy-review. Restarted frontend preview on 15386; the retained backend on 18214 is currently unavailable. No novel/model writes occurred. This is uncommitted local work on fix/local-outline-design-restore; no push, issue, PR, merge, CI run, version change or release was made. User visual approval is still required before PR submission.

## 2026-10-17 — local outline title cleanup, awaiting visual approval

- Follow-up: silenced the routine “思路已保存” success state in the idea input and below the three alternatives. Pending saves, generation/selection progress, loading failures, save failures and revision-conflict recovery remain visible; the autosave mechanism is unchanged. TDD Red: generation and lost-save-response checks failed while the success message was still rendered. Green: all 36 navigation tests and the saved-chapter Chromium HTTP-fixture check pass, including retry identity/local-copy cleanup, retained corner numbering and absence of the redundant success notice. Lint/types/build pass. This remains a local, unsubmitted visual iteration.
- Removed redundant leading “方案一/二/三” (also numeric labels with common separators) from displayed candidate headings and corresponding accessible names; the upper-left “方案 01/02/03” labels remain. This is display-only: stored titles/content and candidate IDs are retained, including exact adoption/version behavior. Unprefixed titles remain unchanged.
- TDD Red: the existing generation/adoption behavior test failed to find the clean heading when fixture titles contained Chinese/numeric prefixes. Green: all 36 navigation tests pass, including preserved corner labels and explicit adoption by the original candidate ID. A saved-chapter Chromium HTTP-fixture check verifies all three clean headings alongside the existing split motion and arrow layout; lint and types/build pass. A test typing error found by build was corrected before the final successful build.
- Local Docker engine is unavailable, so visual evidence uses declared browser HTTP fixtures rather than existing backend data; no real model call or document write occurred. Restarted the local frontend preview only. Screenshot is external evidence/outline-titles-clean.png. Changes remain uncommitted/unpublished on the existing local branch; no issue, PR, merge, release or version change was made.

## 2026-10-16 — local outline design restoration, awaiting visual approval

- Status: local implementation only on fix/local-outline-design-restore. The user now requires visual approval before branch push/PR creation. This work is uncommitted and unpublished; no issue, PR, merge, tag or release was created for it.
- Traced the mismatch to #319's separate real-API outline page bypassing the existing designed preview. Loaded the original Figma Create contexts/screenshots: 27:9 (input), 29:302 (three alternatives), 29:232 (outline/comment editing). Restored a shared input/choice surface for preview and real chapters; retained persistence, explicit selection/version checks, task recovery and collapsed batch history.
- TDD Red: the real chapter lacked the original input heading, and generation kept the full input form alongside results. Both behavior checks failed before implementation. Green: 70 Studio/navigation tests pass, including saved ideas, conflict recovery, explicit selection and no implicit approval/drafting. Local lint and types/build passed; a duplicated conflict status found during regression was corrected. No remote CI is claimed for this local draft.
- Visual check at the original 1920×1080 viewport: input panel x=585/y=427/w=750/h=420; choice group aligns to the original 744×380 group with 200px papers and 72px gaps. Downloaded the original send/refresh SVG exports locally and kept their intrinsic dimensions. Existing navigation chrome is outside this focused restoration; no novel/model mutation was invoked during inspection. Await the user's visual review before delivery.

- Follow-up local feedback: replaced the disclosure/native-select history with a batch summary list showing current batch, idea excerpt, timestamp, status and option count. Explicit browsing closes the panel and returns focus; Escape/outside interaction dismisses without switching. Failed-generation feedback no longer overlaps the history trigger. Removed the duplicate bottom bookshelf action; the header retains its save-before-leave behavior. Omitted the ordinary outline screen's redundant implementation-status note; legacy and generated-outline read-only notices remain.
- History inspection exposed response-order-dependent initial selection. TDD Red confirmed the latest completed batch was not shown when an older failed batch arrived first; Green sorts by generation time before opening the newest batch. Browsing an old batch preserves the current idea and invokes neither selection, generation nor idea writes. Three additional navigation/history behavior checks also failed before implementation. Current result: 74 focused component/navigation tests, local lint/types/build, and the existing Chromium assistant interaction test pass. The existing large-bundle warning remains; no remote CI is claimed for this unpublished draft.
- Per the user's clarification, only Gura's collapsed launcher was restored from Figma node 34:45; the expanded panel was not redesigned. Downloaded exact circle/shark SVG assets with intrinsic 54×54 and 22×21 dimensions. Browser verification: at 1920×1080 the launcher is x=1795/y=985/w=40/h=40, icon frame 24×24 and refresh x=910/y=808/w=100/h=40; original choice geometry is retained. Inspected history at the normal desktop size and 1280×720. Visual inspection only browsed existing results; no generation, adoption or novel-content edit was invoked. Updated screenshots stay outside Git, awaiting user approval.

- Additional local motion request: kept the same joined three-section paper surface while input/title fade out, then widened the two seams over 760ms after a 140ms lead-in. The middle paper remains centred; geometry, corners and shadows transition together. Choices and footer reveal only after the split, with reduced-motion users receiving the final state immediately. The real input remains mounted but inert/hidden during comparison, enabling its outgoing fade without losing idea state. This changes presentation only; generation request identity, polling, explicit adoption and version checks remain intact.
- Motion TDD Red: a Chromium frame-sampling check showed choices were visible while papers were still separating. Green: preview and saved-chapter HTTP-fixture flows now keep content hidden during separation, preserve the centre and finish at the expected geometry; reduced-motion check also passes (3 Chromium checks). Existing 74 Studio/navigation/history component tests plus lint/types/build pass. Saved-chapter browser coverage and its 2.8-second recording use declared HTTP fixtures, not a real model acceptance run. Local manual preview also verified the transition; no extra model call or PR submission occurred. Recording/screenshots remain outside Git for visual approval.

- Latest local visual iteration supersedes the history popover above: replaced it with previous/next arrows around “第 N / M 批”. Left browses older batches and right browses newer ones; unavailable directions are disabled, including a single batch or an unknown active identity. The generation control stays centred and separate. Batch changes preserve ideas and selected outlines; no adoption/generation/write occurs while browsing.
- Arrow TDD Red: 3 history behavior cases and the navigation integration case failed against the prior list. Green: 75 focused Studio/history/navigation tests pass, covering chronological switching, boundaries and preserved ideas. Browser inspection caught inherited vertical nav styling; a failing Chromium geometry assertion now protects the horizontal arrangement. All 3 outline Chromium cases plus lint/types/build pass. Manual browsing of existing real batches and Enter activation verified the display; no model call/content mutation was made. Restarted the stopped local Vite preview on 15386; retained Docker backend/data and local uncommitted approval workflow. Screenshot: external evidence/outline-history-arrows.png. No PR was created or submitted.

## 2026-10-16 — #351 bookshelf composition and creation dialogs

- Delivery: issue #351 → [PR #352](https://github.com/WastonGura/GuraNovel/pull/352), with review/latest-head Actions required before Squash merge. This is a post-release source fix; application versions and the published v0.12.0 tag remain unchanged. The linked PR records the merge and applicable CI result.
- Compared the existing design reference with the user's deployed screenshots. Restored a centered greeting/content hierarchy, content-driven shelves, an actionable empty state and vertical scrolling instead of fixed rows/cropped windows. Creation forms share native modal behavior and a monochrome paper style; less common metadata/internal identifiers are collapsed.
- Red: 3 new component behaviors failed for the missing first-novel action, collection cancellation/focus restoration and pending Escape dismissal. Chromium measured the old desktop greeting at y=40.95px, outside the intended composition. Environment/port and incomplete-fixture errors are not Red evidence.
- Green: all 17 Dashboard component tests and 3 focused Chromium cases pass, including short/narrow windows, collapsed fields, keyboard focus, pending dismissal and failure retention. Local lint and types/build passed; applicable full gates run in Actions. Visual evidence, exact fixture boundaries and deployment steps are in [the acceptance record](acceptance/ui-bookshelf-351.md).
- Original dirty checkout and existing user/trial data remain intact. No backend, migration, dependency, model invocation or release/version update is included.

## 2026-10-16 — #349 README onboarding and project overview

- Delivery: issue #349 → [PR #350](https://github.com/WastonGura/GuraNovel/pull/350), through review/latest-head Actions and Squash merge. This updates main documentation; the published application remains 0.12.0 and the release tag is unchanged.
- Reorganized Chinese and English READMEs around the author workflow, a retained real-model Studio screenshot, pinned-release Docker startup, first-chapter steps, model configuration, troubleshooting and documentation navigation.
- Corrected pre-publication wording to the actual v0.12.0 prerelease. Distinguished deterministic examples from real model calls, explained provider context/charges and kept the known model-quality limits and deferred issue scope visible.
- Validation: commands checked against Compose, provider defaults and frontend configuration; all 5 existing deployment/documentation regression tests passed using uv, 46 local links/images/anchors checked, fenced blocks balanced and git diff --check passed. Reviewed screenshot/source diff; the linked PR records the applicable Actions result. This is documentation maintenance, not a new behavioral TDD claim; no runtime, migration, dependency or version change.

## 2026-10-16 — v0.12.0 published desktop personal trial (#343)

- Status: published as a prerelease at https://github.com/WastonGura/GuraNovel/releases/tag/v0.12.0 on 2026-10-16 14:07 JST. Fixed tag v0.12.0 targets 97f58476bc1e3588480a58007ae098de77beb831; the tag was created once and not moved. Audit PR #348 records the published state.
- Application code c326494 is Squash merged through #344 and passed full main Actions 38853233647: backend tests 1996, architecture gate 95, PostgreSQL integration/migration 679, frontend tests 438 and Chromium 12, plus Ruff/types/build/design/version/Docker gates. Counts describe their respective gate runs, not a sum of unique test cases.
- Follow-up #347 documents the exact release-upgrade 0011/0012 behavior and verified runtime, with no application-code change. Its latest-head Actions 38853829060 and tag-target main Actions 38854032395 passed; runtime jobs were intentionally skipped for documentation-only changes, and the complete code result is recorded separately. Docker was built and containers recreated from the final tag-target checkout, with matching running-image digests, health 0.12.0, migration 0012, actual author API finalization and retained read-only prose verified.
- M0 #300/#305 → #314/#315; M1 #298/#302/#303 → #316/#317/#318; M2 #299/#306/#309 → #319/#322/#324 are reviewed and Squash merged. Existing baseline implementations are explicitly regression coverage, not retrospective TDD.
- Hardening #326/#328/#330/#332/#334/#336/#338/#340 → #327/#329/#331/#333/#335/#337/#339/#341; CI #320/#323 → #321/#325; dependency #345 → #346; stage acceptance #313 → #342; release preparation/verification #343 → #344/#347 plus this final published-state audit.
- Two synthetic real-model samples completed local finalization; one completed actual Reader. Quality remains partial (missed edits/contradictions, length and language errors); model runtimes and 60-call ledger are explicit. Neither deterministic fixtures nor workflow completion are claimed as a literary-quality guarantee.
- Paired backup restore verified 25 document-version hashes against the workspace archive. No published migrations were rewritten, and original repository uncommitted changes were preserved. Private model configuration and raw backups stay outside Git.
- Local trial is running the released code with the previously tested deepseek-flash/none/120-second configuration, without the QA observer proxy. Startup verification invoked no generation routes. Disposable deterministic/test services were stopped with their volumes retained.
- #313 remains open for broader setting/mobile/legacy/export acceptance. Deferred issues #301/#304/#307/#308/#310/#311/#312 and new concept/maintenance continuation are not marked delivered. No public deployment, multiple authors or external novel publishing is included.

## 2026-10-16 — #343 final application and upgrade verification

- Application code is merged at c326494, with all six product versions at 0.12.0. Rebuilding from the exact checkout and force-recreating disposable containers confirmed the running image digests match this build. Health/version, migration 0012, actual proxy author workflow and retained old final prose/read-only UI passed. Machine-readable evidence and a synthetic UI screenshot are included.
- Main full Actions 38853233647 passed on exact application commit c326494. Tag and GitHub Release have not been created; final publication is tracked by #343.
- Comparing the published v0.11.0 tag revealed both 0011 and 0012 are new for an actual release upgrade. Documented 0011 current-setting copies, retained original histories/references, reused workspace paths, and destructive downgrade boundaries. No existing migration was changed. Also clarified Vite's actual Node minimum (20.19+/22.12+).
- This follow-up changes documentation/evidence only. Latest-head documentation gates and byte-identical runtime-code verification are required before merging and tagging; the full code CI result remains separate from the intentionally skipped runtime jobs of a docs-only change.

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

## 2026-10-16 — #343 v0.12.0 release preparation

- Dependencies #335/#337/#339/#341/#342/#346 are now Squash merged. Initial release Actions rejected omitted README lifecycle instructions for explicit network binding and destructive down -v. Both language READMEs now retain those literal commands with their effects; focused deployment documentation and health tests pass. This is a documentation regression correction, not a new TDD feature. Latest-head release Actions must pass before merge.
- Status: release preparation only, not a published tag/release. Feature versions remained 0.11.0; this dedicated branch synchronizes backend pyproject/uv.lock/API and frontend package/npm lock to 0.12.0. Updated the health contract expectation accordingly.
- uv regenerated the lock without dependency upgrades; lock revision becomes 5. The CI-pinned uv 0.11.21 accepts frozen all-groups synchronization. npm version --no-git-tag-version --ignore-scripts updated only package/root versions. The version checker and both mismatch-detection tests pass. These are release checks, not a new behavioral TDD claim.
- Replaced obsolete README capability guarantees and deployment claims with the verified desktop scope, uv development workflow, failure/model limits and exact Compose model configuration. The example override forwards the ignored backend/.env and explicitly overrides base Reader=off; Compose config validation passed without resolving secrets.
- Added release/paired database-workspace backup and additive migration 0012 upgrade notes. Existing published migrations remain unchanged. Latest-head Actions, independent release diff review, final merged-commit Docker/UI and immutable tag/Release are still pending.

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
