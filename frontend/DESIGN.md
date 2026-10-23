---
version: alpha
name: GuraNovel creative workbench
description: A calm, local, document-oriented interface for novel drafting and review.
colors:
  canvas: "#FDFDFD"
  surface: "#FFFFFF"
  ink: "#171717"
  muted: "#737373"
  border: "#E6E6E6"
  primary: "#171717"
  accent-hover: "#333333"
  accent-soft: "#F3F3F3"
  neutral-soft: "#EEEEEE"
  status-success: "#15803D"
  status-success-soft: "#DCFCE7"
  status-warning: "#B45309"
  status-warning-canvas: "#FFFBEB"
  status-warning-soft: "#FEF3C7"
  status-warning-border: "#FDE68A"
  status-danger: "#B91C1C"
  status-danger-canvas: "#FEF2F2"
  status-danger-soft: "#FEE2E2"
  status-danger-border: "#FECACA"
typography:
  headline-lg:
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "24px"
    fontWeight: 600
    lineHeight: "1.2"
  headline-md:
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "18px"
    fontWeight: 600
    lineHeight: "1.3"
  body-md:
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "16px"
    fontWeight: 400
    lineHeight: "1.5"
  body-sm:
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "1.5"
  label-md:
    fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 500
    lineHeight: "1.3"
rounded:
  none: "0px"
  sm: "4px"
  md: "8px"
  lg: "12px"
  xl: "16px"
  full: "9999px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  6: "24px"
  8: "32px"
components:
  app-shell:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    typography: "{typography.body-md}"
    padding: "{spacing.8}"
  top-bar:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.label-md}"
    rounded: "{rounded.sm}"
    padding: "{spacing.4}"
  navigation-item:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    typography: "{typography.label-md}"
    rounded: "{rounded.sm}"
    padding: "{spacing.3}"
  navigation-item-active:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent-hover}"
    typography: "{typography.label-md}"
    rounded: "{rounded.sm}"
    padding: "{spacing.3}"
  document-row:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: "{spacing.3}"
  divider:
    backgroundColor: "{colors.border}"
  interactive-link:
    textColor: "{colors.primary}"
  status-approved:
    backgroundColor: "{colors.status-success-soft}"
    textColor: "{colors.status-success}"
  status-attention:
    backgroundColor: "{colors.status-warning-soft}"
    textColor: "{colors.status-warning}"
  status-attention-panel:
    backgroundColor: "{colors.status-warning-canvas}"
    textColor: "{colors.ink}"
  status-attention-border:
    backgroundColor: "{colors.status-warning-border}"
  status-blocked:
    backgroundColor: "{colors.status-danger-soft}"
    textColor: "{colors.status-danger}"
  status-blocked-panel:
    backgroundColor: "{colors.status-danger-canvas}"
    textColor: "{colors.ink}"
  status-blocked-border:
    backgroundColor: "{colors.status-danger-border}"
  loading-placeholder:
    backgroundColor: "{colors.neutral-soft}"
---

## Overview

GuraNovel is a local writing workspace for moving deliberately from story context to chapter drafts and review decisions. The interface is a calm desk: document-oriented, compact, and clear about where work sits in its hierarchy. It is an authoring tool, not a marketing page.

## Colors

Use a white canvas, white paper surfaces, near-black ink and quiet neutral gray interface controls. Selected surfaces use the soft neutral token; hover and focus darken text and retain a clear outline. Keep green, amber, and red semantic: approved, attention, and blocked respectively. Preserve authored comment/reference colors and Gura's identity. Never use color as the only status signal. The setting collection is the first workspace to consolidate its controls under this language; legacy application areas can adopt the shared rules incrementally.

## Typography

Use the system UI stack defined above. Use sentence case for labels and concise, concrete wording for actions. Treat draft text as the primary content; interface chrome recedes and never competes with the manuscript.

## Layout

Use only the 4, 8, 12, 16, 24, and 32 pixel spacing tokens. Larger page breathing room may be composed from those steps. Keep a persistent top bar for workspace identity and a compact left rail for the current area. Present relationships in this order: workspace, project, chapter, then active document or review state. Favor a readable central editing column, with supporting metadata in a narrow adjacent region or collapsible detail area.

## Shapes

Prefer thin neutral dividers, restrained corner radii and legible document rows. Icon buttons use 8px corners, menu surfaces use 12px corners and primary confirmation buttons use pill corners. Larger paper dialogs retain their established rounded frames.

## Bookshelf and creation dialogs

The bookshelf follows the monochrome author interface: a white canvas, black text, subdued gray metadata, rounded book covers and soft cover shadows. Keep the greeting centered above a content column of at most 1000px, with responsive top spacing of 64–128px so the composition sits higher in the viewport. The page shell stays fixed in the viewport; allocate shelf height from available space and shrink image areas/spacing without losing controls. Keep the three shelf sections compact, with 12–18px track gaps and a content-height cap based on the existing 1:1.2:1 row proportions, heading spacing and card heights. Tall windows must not spread spare height between shelves; keep the accepted card sizes and allow short windows to compress as before. Keep card carousels horizontal, including their edge fade, unclipped leading hover shadow and keyboard access. Do not restore pointer-following halos.

Place a quiet search icon beside settings in the upper-left toolbar. Remove the former bottom search line, hover expansion and associated animations rather than keeping parallel entry points. Ctrl/Cmd+K opens search when another dialog is not already open. Searches operate on loaded works/setting collections, not generated story content or a new backend index. Distinguish a loading/read failure from a valid empty result.

Center the bookshelf's two create glyphs explicitly inside their 22px controls using grid alignment and block images; inline-image text baselines must not lift the 18px glyphs. Preserve the existing artwork and sizes.

Search windows reuse SearchDialog above the native PickerDialog and its shared entrance/exit/reduced-motion behavior. Adapt the supplied search reference into a monochrome rounded surface: a prominent top query, relevant type filters, a scrollable result list on the left and an independently scrollable read-only preview on the right. Hover/focus changes the preview; clicking or Enter confirms the result after the exit animation. Up/down keys browse enabled results and return to the query; Escape restores the trigger focus. Hide the preview below 700px, preserve the query/footer, and scroll only the results. Provide a clear-query action and ignore IME-confirmation Enter. Search type filters use black backgrounds with white text on hover/keyboard focus; give this rule enough specificity to remain readable under shared button styles. The same surface hosts bookshelf search, setting-collection selection, label search/custom entry and setting-content search. Keep label count/name/duplicate checks and immediate addition; preview selected tags. Content search keeps the prior note/graph/conversation beneath it and returns there on cancellation without modifying documents. Search-result snippets are bounded; the selected preview can show the full text. Inline wiki-reference completion remains an editor control.

Bookshelf search results use small portrait covers for works and a distinct outlined dossier icon (collection.svg) for setting collections. Reserve the gear for application settings. Align result titles with subdued label chips beneath; show at most three labels and an overflow count in the list, all valid labels in the preview. Remove novel introductions from result rows while retaining introduction matching and adding label matching. The right preview is a white column with a quiet vertical divider: a portrait cover beside the full title/tags/status, followed by introduction and the associated setting collection. Do not place a portrait inside a wide gray banner. Collection previews use the dossier identity, description and actual related works rather than an arbitrary artwork. Reuse CoverImage for responsive thumbnail delivery. SearchDescription clamps introductions/descriptions to four rendered lines, measures overflow and offers expansion only when needed; expanding preserves the full text and scrolls within the preview. Reset expansion and scroll when changing records. Keep long titles/tags contained and preserve the fixed outer page on short desktops.

Novel creation, details and metadata editing share a two-column paper dialog, at most 1120px wide and 760px tall, constrained to the viewport. A large portrait cover and book title occupy the left column. The right column opens directly on the chapter list without directory/information tabs or a redundant heading. Keep the quiet edit action in the footer; while editing, that same position becomes the chapter-directory action. Returning to the directory is a view switch and retains the same live editing state, including after a save failure. Metadata autosaves after a 400ms input pause without leaving the editor. The cover caption mirrors title input immediately. Keep inputs usable during writes; serialize requests, retain newer edits when older responses arrive, and flush before workflow navigation. On dismissal, try synchronization when possible; failed or pending saves must allow an exit after retaining a verified recovery copy. Prefer a durable browser-local backup, with this-tab session storage as fallback; restore drafts when reopening and clear the owned backup after successful synchronization. Preserve late-response backups and deliver completed saves to the parent bookshelf. Expose failures and retry only when needed, distinguishing rejected/invalid data from connectivity failures. Never claim a copy exists when storage writes fail. Do not show a manual save action or routine saved-status text. Do not infer extra banner images or volume-management capabilities from the private design references.

Metadata editing combines the supplied information reference with the accepted large serif editable title (30px), beside a compact neutral status pill. Stack word count and chapter count with the existing size.svg and chapter.svg assets, aligned with the label.svg heading. Keep icon-to-text spacing at 10px, statistic rows and the chapter-to-label gap at 12px, and main sections 24px apart. Give statistic values the same 14px type/20px line box as their labels, with a subtle upward optical correction. Indent chips to align with the label text. Use the same 28px pill height for labels and the add action; two-character labels and the add action share a 46px minimum width. Reuse StudioLabels and its existing search/custom-label picker, duplicate prevention, 8-label limit and 16-character name limit; do not introduce a separate label editor or local-only label persistence path. Reuse PickerDialog for label, setting and cover selection: its native dialog renders through a portal so metadata field styling and parent focus trapping do not interfere. Escape cancels only the picker and restores its trigger focus. Keep a large white introduction textarea with a thin border and restrained corner radius. Place setting selection and platform in the initially collapsed, borderless optional disclosure. Remove genre from both creation and metadata forms; labels provide classification, while the legacy API/data field remains compatible and is not overwritten by these forms. Use a quiet rounded setting trigger and a searchable list with descriptions and a current-selection marker. Creation offers a new blank setting collection; editing offers only existing collections, matching the backend requirement that a work remain bound. Use the dossier identity without a downward chevron. Omit unchanged association fields from metadata PATCH requests; never send a forbidden null. Repair old unsupported unbound recovery selections to the server binding while retaining the other draft text, with an explanatory notice. Do not expose the old native select or a separate association-navigation arrow alongside this picker; open setting workspaces from their bookshelf entry. Keep title, labels and introduction visible in a short desktop window. Creation omits the duplicate comma-separated label input; manage work labels exclusively through the existing StudioLabels picker in metadata editing and its autosave flow. Creation keeps its labeled form and a single right-aligned create action with the outlined book-plus icon; remove the duplicate footer cancel action, retaining the close button, Escape/backdrop cancellation and pending-submit guard. Generate the internal workspace slug once per open form using a UUID; keep it stable through retries and hide the technical identifier/disclosure from authors. Do not change existing workspace identifiers. Both modes share field values and modal/footer geometry.

The portrait cover is a keyboard-accessible change trigger in both creation and details. Use a pure #e4e4e4 CSS surface for a missing/default work cover in creation, details, bookshelf/recent cards and search. Persist the explicit cover-default metadata value for new works and reset choices; retain explicitly selected presets/uploads/links. This default must allocate no image request, decode, loading shimmer or resize observer. Open the shared picker with a default-cover reset and the existing eight cover assets, a current-selection marker, local PNG/JPEG/WebP upload (at most 5 MB) and a validated HTTP(S) image-link option. Only choose a linked image after it decodes successfully; cancellation/load errors preserve the previous cover. Selecting a cover updates the preview immediately and uses the existing metadata autosave; creation saves it with the create action. Bookshelf/recent cards and reopened dialogs resolve the saved metadata cover before using the existing artwork fallback. Size the picker grid to keep its controls reachable in a short window. Match every preset thumbnail to the live large-cover frame aspect ratio, including viewport and title-wrap changes while the picker is open. Use that same frame ratio for the shared crop/contain decision and portrait focal position so selection predicts the actual cover, including intentional letterboxing for wide art. Do not animate object-fit, object-position or intrinsic aspect-ratio when replacing image content. Local uploads use persistent backend image assets, then the same metadata-save flow. Upload errors/limits preserve the previous cover; closing aborts pending upload work and ignores late responses. Do not embed image bytes in project metadata.

All photo surfaces on the bookshelf, collection cards, detail cover and cover picker reuse CoverImage. Measure the actual container and offer 160/320/640/1200-width candidates so the browser accounts for device pixel density; lazy-load clipped/offscreen cards and prioritize the first recent cover/detail. Keep fixed image geometry, asynchronously decode replacements before swapping out the last ready image, and reveal the initial image gently. Preserve aspect ratio: card thumbnails crop toward the upper part of portraits, while a large preview or picker uses contain when the aspect mismatch is substantial. Use neutral placeholders, a bounded original-image fallback when an optimized asset is unavailable, and retry failed loads on reconnect. Respect reduced motion. Never stretch a photo to a different ratio.

Preset WebP derivatives and their generated source manifest use content-fingerprinted filenames for immutable caching; regenerate from the existing originals with `cd backend && uv run python ../scripts/prepare_cover_assets.py`. Uploaded covers use the same width ladder through the image API; older uploads lazily acquire and retain derivatives, with ETag/304 revalidation. Direct external image links benefit from lazy loading/decoding but keep the host-provided bytes; do not silently proxy arbitrary URLs through the backend.

Setting-collection creation retains its compact dialog, at most 600px wide. Both dialog types use white paper, restrained ink and soft blurred backdrops. Labels use a medium 13px weight and inputs regular 14px text. Quiet text actions darken and underline on hover; compact dark primary actions sit at the bottom right. Use visible monochrome keyboard focus. Scroll only the designated body so the cover, close control and footer remain fixed in short desktop windows. In narrow windows, use a compact cover/title header over the same internally scrolling content. Native modal behavior isolates background controls, focuses the title field for creation and the close control for read-only details, returns focus on dismissal and prevents dismissal/repeated writes during submission. Failed saves retain fields and focus a recoverable error.

All creation/detail dialogs and label/setting/cover pickers share useDialogMotion: use a short opacity/scale/vertical entrance and fade the backdrop. Picker selection completes through the same close handler, including uploaded-image completion. Route close, cancel, Escape and backdrop dismissal through the same exit lifecycle: retain the native modal until its 240ms exit completes, make the closing content inert and restore focus only after removal. Use a bounded fallback if the end event is lost. Respect reduced motion by disabling panel/backdrop animations and dismissing immediately. Switching between the metadata form and chapter directory does not dismiss the dialog.

## Standalone setting workspace

Retain the original large collection title at the top, using Studio's responsive 28–48px headline scale. Keep the header free of home-navigation controls. Place a quiet, text-only 回到书架 link in the fixed sidebar footer; its destination is always `/`. Loading/error pages keep an equivalent lower navigation fallback. Darken and underline on hover/focus without adding a button outline. Remove the constant impact notice, retaining actual save/conflict/archive feedback. Flush body and pending title edits before home navigation; failed writes keep the current draft and a retryable error.

Category switches retain the outgoing particle dissolve and incoming text reveal inside TextMotionSurface. Clip the decorative particle layer to the list surface, and let actual new list content determine height rather than animating the old full list's height. Switching from many entries to an empty category must not create a temporary scrollbar; genuinely long lists remain scrollable with the footer outside the scrolling region. Empty-category titles share the saved entry title's content origin and spacing, without an extra padded container.

Reuse the existing circular Gura launcher and animated drawer, keyboard focus return and reduced-motion behavior. Render exactly one global Gura instance. Opening it reserves a reading column on wide desktops and space above the drawer on short/narrow desktop windows; the outer viewport remains fixed. Keep document and message scrolling inside their designated regions. Gura refers only to the global assistant; the document-authoring Agent has its own clearly named panel and history.

Gura is one continuous conversation across the bookshelf, chapter workbench and setting collections. Page navigation updates the context for the next message without selecting another chat or clearing the composer. New conversations and history are explicit, compact icon actions at the window's upper right, using the existing animated icon family. History replaces the window's message area with a quiet list of titles, dates and previews; choosing a conversation resumes it with its own draft. Clicking the same history icon again returns to the active chat, including from read-only legacy history; do not add a separate return control. Keep its pressed state accurate and restore the composer focus and draft. Avoid creating repeated empty chats. The active conversation survives reload and page navigation. Only the existing `/preview/studio` subtree displays the explicitly unsent interaction preview.

Hide Gura while the native work-details dialog is open. Keep its established cover/content layout without reserving assistant space or expanding the dialog. Closing work details restores the bookshelf assistant with the same conversation and draft. The conversation identity belongs to the user; current project/chapter or collection/document/version is captured separately for each send. Gura remains distinct from the setting collection's authoring Agent.

Persist an uncertain message's original conversation, request identity, text and page context before sending. Preserve that journal separately from newer composer text. Reload reconciles the exact saved acknowledgement; explicit retry reuses the original tuple even after page navigation. A late reply or old acknowledgement cannot replace another selected conversation, newer text or author focus. A failed initial load offers a real reload action instead of silently creating a fresh conversation. Prior page-bound histories remain preserved as clearly identified read-only records, separately from resumable global conversations.

Global-assistant conversations place user messages on the right in charcoal bubbles with white text; Gura replies sit on the left in white bubbles with a subtle gray border. Keep speaker labels and mirrored lower corners so position and shape also distinguish the roles. Use content-sized bubbles bounded to the message column, retaining natural wrapping for long text. Apply the same roles to pending messages and clearly disclosed user-only preview messages.

Keep conversation history internally scrollable by wheel, touch and keyboard while hiding its scrollbar chrome. The send action uses a 32px circular charcoal button and a legible 20px paper-plane SVG with its viewport optically centered on the filled silhouette, avoiding excess asset whitespace. Center inline SVG controls in the standard icon grid; do not apply the oversized, offset round-asset wrapper intended for exported image assets. Its disabled state stays the same size with quiet gray fill; retain accessible naming, focus and submission guards.

Gura's floating window supports independent width and height. Keep the previous responsive size as the minimum, preserve the bottom/right anchor, and offer subtle left/top/corner resize targets with visible focus and keyboard arrows. Cap width at the smaller of 560px and 45vw, and height at the smaller of 720px and 80vh; the established responsive minimum takes priority in small windows while available viewport space remains the hard bound. Clamp older oversized preferences to the same limits. Shift increases the step; double-click restores the baseline. Remember committed dimensions locally without overwriting them when a smaller viewport clamps the window. Keep handles within the viewport, cancel interrupted drags cleanly and disable competing layout transitions during a drag. Reserve the actual width/height in surrounding layouts; in very narrow windows the minimum-size conversation remains a floating surface. Resizing and closing never discard the conversation or composer draft.

Anchor the collaboration tool to the workspace's upper-right corner, outside the assistant-responsive body column. Global-assistant opening and closing must not shift that control. In narrow windows reserve a separate top row above the document. Note context menus use a short fade/scale/vertical entrance and exit, sharing the existing dismissal lifecycle and fallback. Keep closing menus inert until removal, restore keyboard focus on Escape, and wait for dismissal before opening delete confirmation. Cancel stale queued actions when category/context changes. Respect reduced motion and preserve arrow-key, copy and outside-click behavior.

In a collection, the continuous global conversation reads the collection identity and active saved document/version for each new message. A collection with no associated novel still supports real assistance. A rejected stale version retains the text and allows a new request after the current version is checked. Global Gura never silently changes or approves setting content, including archived collections.

### Direct writing and the setting Agent

Use one 新建内容 entry. Open an empty title and body in the current category with title focus, without separate manual/chat creation actions. Writable entries are directly editable; retain the existing annotation/reference editor. Autosave actual writing, keep untouched blank drafts local, and preserve the exact first creation identity, newer text and recovery copies on uncertain outcomes. Retain each document's cursor and inner scroll when returning. Archived documents have no write controls.

Setting document changes use the existing particle dissolve and text reveal for the outgoing/incoming title and body. Snapshot the latest typed content before replacing the document; measure against the visible viewport so an outgoing scrolled document does not jump. Reveal visible lines from top to bottom, each from left to right, with a short stagger that allows adjacent lines to overlap. Keep native text rasterization stable through completion: on the setting canvas, reveal with temporary background-colored covers rather than masking the glyphs themselves. Do not animate offscreen rows. Remove the covers on completion, interruption or scroll, without adding permanent layers or changing global font rendering. Empty categories still dissolve the outgoing prose. Typing, autosave acknowledgements and composer switches do not replay the document transition; interaction cancels an in-progress reveal immediately, and reduced motion shows the final content directly.

Trigger inline setting references only by typing @, then search/select an entry in the existing completion list. Do not expose a separate reference button, Ctrl+Space trigger or bracket trigger. Render references as atomic entry names with #666 text on the unchanged page surface; no background fill, superscript, extra padding or visible double brackets. Hover/focus darkens the text, and hover adds a fine underline. Keep canonical reference syntax in persistence so graphs, rename propagation, saved comments and existing documents remain compatible. Map visible selections to canonical UTF-16 offsets, retain IME/plain-text paste, and delete a reference as a single unit. Ctrl/Meta-click or focus/Enter opens its entry; ordinary clicking places the cursor beside it. Reading mode uses the same plain-name text treatment.

Keep reference editing in one bounded canonical undo/redo history, including typing, completed IME input, line breaks, reference insertion/deletion, paste and cut. Restore the canonical caret with each operation and clear the redo branch after new writing. Authoritative document replacements reset history; autosave acknowledgements and comment decoration do not. Clipboard/drop input uses plain text with LF line endings.

与设定 Agent 协作 opens the collection's selected authoring conversation in the central workspace, including when the collection has no entries. The page-level collection title provides context; do not repeat the title or Agent identity above the discussion. Assistant messages identify 设定集 Agent as their author. The conversation never changes identity when a different entry/category is selected. The server supplies the saved collection snapshot, and the author can discuss the world as a whole, propose a new entry or revise an existing one. Flush actual manual drafts before generation; do not manufacture a blank target document merely to open or send a message. Keep each selected conversation's discussion, composer and request identity separate from other conversations, global Gura and legacy per-document conversations.

Place compact 历史对话 and 新建对话 icon controls beside 返回文稿 in the conversation header. Reuse the shared animated history/create vectors at 20px within 32px targets. The history icon toggles the central area between the current conversation and its history; clicking it again returns directly, without another return control. History uses quiet rows with title, date, preview and the current-conversation mark. Hover and keyboard focus give the whole row an opaque #eee surface and #171717 title, with an additional visible keyboard outline. Scope these rules to the setting workspace so generic transparent button hovers cannot override them. Verify the actual settled row background in the complete workspace; a title-color change alone does not establish visible row feedback. Keep row bounds still during these transitions and respect reduced motion. Load lightweight summaries first and read a conversation only when selected; offer explicit pagination. New conversation opens a reusable local draft without a naming dialog or server creation. Repeated clicks and history round trips preserve that draft; only the first explicit send creates a conversation with a stable creation identity. Its first author request supplies the title. Unsent drafts and existing empty server conversations do not appear in history; real saved requests, including interrupted requests, remain accessible for recovery.

Preserve the approved central layout and 760px composer. Fade the top 22px of the message viewport only after it has scrolled, using a CSS mask that cannot intercept clicks. Apply the same treatment to a scrolled history list; keep the first row fully visible at the top. History transitions use a short opacity fade and honor reduced motion. Keep scrolling inside the workspace without a visible scrollbar. Return from history with the same draft and scroll position, and return to the manuscript without overriding the editor's restored focus. Preserve each conversation's pending recovery independently, verify the exact creation acknowledgement and keep an uncertain creation UUID for an explicit retry. Conflicting older recovery records remain intact and show a useful error rather than being combined.

Keep uncertain-request recovery within the existing quiet feedback/footer area; preserve the established central discussion and narrow composer. Read the persisted request after refresh without automatically sending again. Offer 继续发送 only for a prepared request that has not dispatched, 检查结果 for an unconfirmed dispatched request, and 恢复结果 when the server has cached a result but not finalized its message pair. A missing server record does not prove that an earlier send never arrived. 结束等待 preserves the input and permanently fences the old request; explain that it does not cancel an upstream call. Only the author's next explicit send creates a fresh request. Never show a completed delivery from only a user record or an unrelated assistant reply. Newer typing, identical retyping and deliberate clearing survive late acknowledgements and reload. Checking or resuming a request must not flush newer manuscript drafts or silently change its saved context. Recovered proposals still require comparison and explicit adoption; keep newer prose visible when a suggestion is stale.

Keep the same horizontal content origin, right inset and top alignment as the direct manuscript editor. Use the shared workspace column, without an independently centered maximum-width panel or a welcome message centered in the viewport. Align the empty-state heading with the entry title's top-left origin; the return action must not push it down. Leave room at its right for that action in narrow columns. Center only the bottom composer and its feedback within the available column, capped at 760px and shrinking to fit narrow spaces. A quiet 返回文稿 action at the upper right restores the manuscript, which stays mounted with its text/cursor/scroll. Use quiet monochrome text actions and the shared 32px send circle; enter/exit motion remains a short fade and vertical translation, with reduced-motion support. At desktop widths of at least 800px, reserve the same Gura column in both manuscript and collaboration views; compact sidebar sizing also remains the same between them. Gura's height changes do not shorten the central writing surface.

Keep suggestions visibly separate from editable text and identify the affected entry. Require the author to expand the original/proposed comparison before adoption. New-entry proposals create no document until explicit acceptance. If the collection snapshot changes, preserve the current text and suggestion and offer a new discussion. Only explicit, version-checked adoption creates a document or version; discarded suggestions remain in history. A response lost after adoption offers a deliberate result check instead of silently rewriting the draft. Successful adoption selects its actual target in the underlying editor while preserving the shared conversation.

### History and collection archival

Archive choices preview on mouse/pen hover after a brief 100ms dwell and immediately on keyboard focus or click; touch retains click selection. Keep the latest selection's identity and prose paired, ignore late reads, deduplicate in-flight version reads and retain up to twelve immutable bodies within the dialog. A short fade out/in softens content changes without moving the window. Pending reads remain explicit and cannot enable restoration of a previous preview under a new choice. Confirmation freezes selection; focused message input pauses hover so the author can type, while deliberate selection flushes its message and changes the preview. Reduced-motion preference switches previews without animated transitions.

Allow an optional one-line archive message of up to 160 characters, such as 补全主角动机. Show it as the primary list label with archive number/time as secondary context, retaining 存档 N when no message exists. Do not invent messages for older archives or force a naming step during one-click saving. The right preview contains a quiet, directly focusable message input with placeholder 添加存档说明…; omit separate edit entry points, manual save/cancel controls and routine success text. Use muted gray for its resting text and ink black while focused; readonly descriptions retain muted gray. Every keystroke updates the left label immediately. Autosave after a 400ms input pause and flush on blur, Enter, deliberate selection or dismissal; defer writes while an IME composition is active. Keep input usable during writes, serialize requests per archive and prevent older acknowledgements from replacing newer text. Failed writes pause retries and preserve both the input and optimistic left label; expose retry only on failure. A conflict requires an explicit latest-message read before the author continues synchronization. Retain recoverable per-point drafts through dismissal without claiming storage succeeded when it did not, and scope backup cleanup to the matching draft so an older window cannot erase newer input. Archived collections show only persisted messages read-only. Messages annotate manual points only, preserving immutable prose/comments, point identity and timestamp; the preview title uses the saved title rather than substituting a newer entry title.

Place a quiet history-clock icon named 历史存档 in the entry's upper-right tools; enable it only for persisted entries. Reuse PickerDialog's native modal, entrance/exit animation, reduced-motion handling and focus return. Use a white, monochrome paper window up to 960px wide: a 240px chronological manual-save rail beside an independently scrolling, read-only full-text preview. Start the rail directly with its archive choices, without a redundant 手动存档 heading. List only explicit manual points, newest first, labeled 存档 N with their actual save time. Autosave protects current writing and must not populate this list. Never label earlier automatic revisions as manual points. Keep list/content loading failures separate and retryable. Arrow keys, Home and End browse the rail. Below 620px, arrange its choices above the preview; bound every scroll area to the fixed window and keep the footer reachable.

Keep the established two-column history window for browsing and restoration, with only the count and close action in its header. Place the quiet 20px save SVG in a 32px square action at the upper right of the manuscript page, beside 历史存档; do not put the save action inside the history window or use a text pill. Give it the accessible name and tooltip 存档当前内容. It first synchronizes outstanding text/title/comment queues, then creates one explicit restore point with no naming step or modal; acknowledge success only with the brief check inside the icon and an accessible description, without visible success text. Check saved points to avoid duplicating unchanged body/comments, and retain exact create-request identity for uncertain replies, including reload. Never infer success from matching prose. An empty history explains draft autosave versus manual points and points to the page's save icon. A point matching current saved body/comments is marked 当前 and cannot be restored again. Pending saves keep page mutations inert; pending restores keep the history window's close, selection and mutation controls disabled.

Viewing history does not restore anything. An inline confirmation identifies the chosen entry/point/time, explains that restoration replaces body and comments while retaining manual history, and displays actual associated works and the effect on future creation without claiming to rewrite existing chapters. Restore creates no new manual point. Keep the footer limited to right-aligned actions, without persistent explanatory copy; current badges and the archived header state provide their context. A failed draft save still allows read-only browsing, with a clear inline error explaining that saving/restoring points is unavailable until synchronization succeeds. A stale or failed restore retains the author's text and chosen point. Errors remain visible after dismissal. Archived collections allow history browsing but no manual saving or restoration.

Place collection actions under a 32px ellipsis button named 设定集操作 in the page header. Open a compact white, rounded, nonmodal menu beneath the trigger, aligned to its right edge and clamped within the viewport. Reuse the existing context-menu entrance/exit motion, retain the closing menu as inert until removal and support outside dismissal, Escape focus return and keyboard navigation. Keep the editor layout and background unchanged. The first row is 与设定 Agent 协作, which opens the existing collection-wide authoring conversation after the menu exits; remove the redundant manuscript-toolbar collaboration action in this workspace. The second row is 归档设定集 with its archive-box icon for active collections. Archived collections retain the menu's read-only collaboration-history entry but no archive row. Do not add placeholder export actions, an unnecessary search field or a separate options dialog. Additional real operations can join this menu later.

Selecting 归档设定集 waits for the menu to dismiss, then opens the animated archive confirmation. Identify the collection, show real related-work names and explain read-only/future-creation effects; focus 取消 initially, and dismiss through the shared exit lifecycle. Failed related-work reads have a retry action and do not appear as an empty association list. Flush all author queues before archival, keep drafts on failure, and reconcile uncertain replies before showing success. After success, hide the archive-only actions menu and retain the large page title and 已归档 · 只读 status; authors can still browse documents, history and associations. Do not expose a fake unarchive action or add a constant impact notice to the page.

### Associated novels and trash

关联小说 is a quiet text-count button that always opens, including zero. Preserve its last successfully read count during a background refresh instead of replacing it with loading/error text and shifting the header. Distinguish an unknown initial count from a known zero. History/archive impact details must still show pending or failed reads and offer retry; a cached header count cannot authorize restoration. Reuse the shared searchable result/preview window, actual covers, labels, status, full titles and expandable introductions. Loading failures are retryable and never displayed as a zero count. Protect pending document/title writes before animated dismissal and opening the novel details; failed synchronization keeps the window open.

Keep 回到书架 as a quiet text action and 回收站 as a monochrome trash icon in the fixed sidebar footer. The trash icon has an accessible name/title and darkens on hover/focus. A successful deletion shows only the latest operation in a compact 已移至回收站 · 撤销 toast. Do not steal focus; pause dismissal during hover, focus, pending undo or an uncertain result. The shared animated trash dialog shows category and deletion time, searches titles, supports single/batch restore and retains failed items for retry. Preserve original document identity, body, versions and references. Both undo and trash use the same restore path; uncertain network responses are reconciled before claiming success. Archived collections can browse trash with restoration disabled. Do not expose permanent delete, empty-trash or expiration controls.

## Components

### Setting controls and interactive icons

Use shared SettingActionIcon vectors for the setting workspace, its note context menu and its history, archive, trash and search windows. Tool controls are 32px squares containing 20px icons and an accessible name plus tooltip; menu rows use 18px icons with 14px text. Sidebar navigation uses 20px icons with text. Keep 回到书架 and 返回文稿 as text actions, confirmation actions as text buttons and 关联小说 as a stable text/count control. Preserve the large collection title, approved authoring geometry and Gura's separate brand/send controls. The history and manual-save tools form a matched icon pair. Confirmation actions use the same 40px pill height and 14px label size.

All vectors share a 24px viewBox, 1.65px rounded strokes and optical centering. Interactions animate local parts: a clock hand turns, a trash lid lifts, a search handle shifts, dots spread and a save tab presses. Hover and keyboard focus share feedback, press is brief and reversing an interaction returns smoothly. No idle looping or button-box movement. Busy saving changes the tab without an endless animation; a verified save draws a short check with an accessible description and no visible success text. Bind that acknowledgement to the exact saved document/version/title/body/comments snapshot; switching entries or making a new edit removes stale feedback immediately. The sidebar pin uses a broad, complete pushpin whose cap, shoulders and needle transform together: upright when pinned and tilted when unpinned, with a small reversible hover response. Reduced-motion preference removes animated transforms and transitions while retaining static pending/success states and the pin's distinct state poses. Menus and native dialogs retain their existing entrance/exit lifecycle. Keep these setting-scoped controls opt-in when extending generic search/dialog components; other callers retain their current rendering.

Make the active item unmistakable through text, position, and a subtle surface change; do not rely on a colored mark alone. Use a prominent page title, modest section headings, and 14–16 pixel body text with comfortable line height.

## Do's and Don'ts

Use native landmarks and accessible names for the banner, navigation, and main content. Maintain visible keyboard focus with a neutral outline and a clear offset. Meet WCAG AA contrast for normal text over every surface it may occupy. Pair status color with a written label and, where useful, an icon or shape. Do not introduce remote fonts or assets.
