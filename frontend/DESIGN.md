---
version: alpha
name: GuraNovel creative workbench
description: A calm, local, document-oriented interface for novel drafting and review.
colors:
  canvas: "#F8FBFF"
  surface: "#FFFFFF"
  ink: "#0F172A"
  muted: "#475569"
  border: "#CBD5E1"
  primary: "#2563EB"
  accent-hover: "#1D4ED8"
  accent-soft: "#DBEAFE"
  neutral-soft: "#E2E8F0"
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

Use a blue-tinted white canvas, pure white paper surfaces, deep near-black ink, and the single trustworthy blue accent defined above. Blue is for interactive emphasis, including focus and selected states; selected surfaces use the soft blue token. Keep green, amber, and red strictly semantic: approved, attention, and blocked respectively. Never use color as the only status signal.

## Typography

Use the system UI stack defined above. Use sentence case for labels and concise, concrete wording for actions. Treat draft text as the primary content; interface chrome recedes and never competes with the manuscript.

## Layout

Use only the 4, 8, 12, 16, 24, and 32 pixel spacing tokens. Larger page breathing room may be composed from those steps. Keep a persistent top bar for workspace identity and a compact left rail for the current area. Present relationships in this order: workspace, project, chapter, then active document or review state. Favor a readable central editing column, with supporting metadata in a narrow adjacent region or collapsible detail area.

## Shapes

Prefer thin slate-blue-gray dividers, small corner radii, and dense but legible document rows over decorative cards.

## Bookshelf and creation dialogs

The bookshelf follows the existing monochrome author interface: a white canvas, black text, subdued gray metadata, rounded book covers and soft cover shadows. These surfaces are an explicit exception to the older blue application-shell styling described above. Keep the greeting centered above a content column of at most 1000px, with responsive top spacing of 64–128px so the composition sits higher in the viewport. The page shell stays fixed in the viewport; allocate shelf height from available space and shrink image areas/spacing without losing controls. Keep the three shelf sections compact, with 12–18px track gaps and a content-height cap based on the existing 1:1.2:1 row proportions, heading spacing and card heights. Tall windows must not spread spare height between shelves; keep the accepted card sizes and allow short windows to compress as before. Keep card carousels horizontal, including their edge fade, unclipped leading hover shadow and keyboard access. Do not restore pointer-following halos.

Place a quiet search icon beside settings in the upper-left toolbar. Remove the former bottom search line, hover expansion and associated animations rather than keeping parallel entry points. Ctrl/Cmd+K opens search when another dialog is not already open. Searches operate on loaded works/setting collections, not generated story content or a new backend index. Distinguish a loading/read failure from a valid empty result.

Search windows reuse SearchDialog above the native PickerDialog and its shared entrance/exit/reduced-motion behavior. Adapt the supplied search reference into a monochrome rounded surface: a prominent top query, relevant type filters, a scrollable result list on the left and an independently scrollable read-only preview on the right. Hover/focus changes the preview; clicking or Enter confirms the result after the exit animation. Up/down keys browse enabled results and return to the query; Escape restores the trigger focus. Hide the preview below 700px, preserve the query/footer, and scroll only the results. Provide a clear-query action and ignore IME-confirmation Enter. Search type filters use black backgrounds with white text on hover/keyboard focus; give this rule enough specificity to remain readable under shared button styles. The same surface hosts bookshelf search, setting-collection selection, label search/custom entry and setting-content search. Keep label count/name/duplicate checks and immediate addition; preview selected tags. Content search keeps the prior note/graph/conversation beneath it and returns there on cancellation without modifying documents. Search-result snippets are bounded; the selected preview can show the full text. Inline wiki-reference completion remains an editor control.

Bookshelf search results use small portrait covers for works and a distinct outlined dossier icon (collection.svg) for setting collections. Reserve the gear for application settings. Align result titles with subdued label chips beneath; show at most three labels and an overflow count in the list, all valid labels in the preview. Remove novel introductions from result rows while retaining introduction matching and adding label matching. The right preview is a white column with a quiet vertical divider: a portrait cover beside the full title/tags/status, followed by introduction and the associated setting collection. Do not place a portrait inside a wide gray banner. Collection previews use the dossier identity, description and actual related works rather than an arbitrary artwork. Reuse CoverImage for responsive thumbnail delivery. SearchDescription clamps introductions/descriptions to four rendered lines, measures overflow and offers expansion only when needed; expanding preserves the full text and scrolls within the preview. Reset expansion and scroll when changing records. Keep long titles/tags contained and preserve the fixed outer page on short desktops.

Novel creation, details and metadata editing share a two-column paper dialog, at most 1120px wide and 760px tall, constrained to the viewport. A large portrait cover and book title occupy the left column. The right column opens directly on the chapter list without directory/information tabs or a redundant heading. Keep the quiet edit action in the footer; while editing, that same position becomes the chapter-directory action. Returning to the directory is a view switch and retains the same live editing state, including after a save failure. Metadata autosaves after a 400ms input pause without leaving the editor. The cover caption mirrors title input immediately. Keep inputs usable during writes; serialize requests, retain newer edits when older responses arrive, and flush before workflow navigation. On dismissal, try synchronization when possible; failed or pending saves must allow an exit after retaining a verified recovery copy. Prefer a durable browser-local backup, with this-tab session storage as fallback; restore drafts when reopening and clear the owned backup after successful synchronization. Preserve late-response backups and deliver completed saves to the parent bookshelf. Expose failures and retry only when needed, distinguishing rejected/invalid data from connectivity failures. Never claim a copy exists when storage writes fail. Do not show a manual save action or routine saved-status text. Do not infer extra banner images or volume-management capabilities from the private design references.

Metadata editing combines the supplied information reference with the accepted large serif editable title (30px), beside a compact neutral status pill. Stack word count and chapter count with the existing size.svg and chapter.svg assets, aligned with the label.svg heading. Keep icon-to-text spacing at 10px, statistic rows and the chapter-to-label gap at 12px, and main sections 24px apart. Give statistic values the same 14px type/20px line box as their labels, with a subtle upward optical correction. Indent chips to align with the label text. Use the same 28px pill height for labels and the add action; two-character labels and the add action share a 46px minimum width. Reuse StudioLabels and its existing search/custom-label picker, duplicate prevention, 8-label limit and 16-character name limit; do not introduce a separate label editor or local-only label persistence path. Reuse PickerDialog for label, setting and cover selection: its native dialog renders through a portal so metadata field styling and parent focus trapping do not interfere. Escape cancels only the picker and restores its trigger focus. Keep a large white introduction textarea with a thin border and restrained corner radius. Place setting selection and platform in the initially collapsed, borderless optional disclosure. Remove genre from both creation and metadata forms; labels provide classification, while the legacy API/data field remains compatible and is not overwritten by these forms. Use a quiet rounded setting trigger and a searchable list with descriptions and a current-selection marker. Creation offers a new blank setting collection; editing offers only existing collections, matching the backend requirement that a work remain bound. Use the dossier identity without a downward chevron. Omit unchanged association fields from metadata PATCH requests; never send a forbidden null. Repair old unsupported unbound recovery selections to the server binding while retaining the other draft text, with an explanatory notice. Do not expose the old native select or a separate association-navigation arrow alongside this picker; open setting workspaces from their bookshelf entry. Keep title, labels and introduction visible in a short desktop window. Creation omits the duplicate comma-separated label input; manage work labels exclusively through the existing StudioLabels picker in metadata editing and its autosave flow. Creation keeps its labeled form and a single right-aligned create action with the outlined book-plus icon; remove the duplicate footer cancel action, retaining the close button, Escape/backdrop cancellation and pending-submit guard. Generate the internal workspace slug once per open form using a UUID; keep it stable through retries and hide the technical identifier/disclosure from authors. Do not change existing workspace identifiers. Both modes share field values and modal/footer geometry.

The portrait cover is a keyboard-accessible change trigger in both creation and details. Use a pure #e4e4e4 CSS surface for a missing/default work cover in creation, details, bookshelf/recent cards and search. Persist the explicit cover-default metadata value for new works and reset choices; retain explicitly selected presets/uploads/links. This default must allocate no image request, decode, loading shimmer or resize observer. Open the shared picker with a default-cover reset and the existing eight cover assets, a current-selection marker, local PNG/JPEG/WebP upload (at most 5 MB) and a validated HTTP(S) image-link option. Only choose a linked image after it decodes successfully; cancellation/load errors preserve the previous cover. Selecting a cover updates the preview immediately and uses the existing metadata autosave; creation saves it with the create action. Bookshelf/recent cards and reopened dialogs resolve the saved metadata cover before using the existing artwork fallback. Size the picker grid to keep its controls reachable in a short window. Match every preset thumbnail to the live large-cover frame aspect ratio, including viewport and title-wrap changes while the picker is open. Use that same frame ratio for the shared crop/contain decision and portrait focal position so selection predicts the actual cover, including intentional letterboxing for wide art. Do not animate object-fit, object-position or intrinsic aspect-ratio when replacing image content. Local uploads use persistent backend image assets, then the same metadata-save flow. Upload errors/limits preserve the previous cover; closing aborts pending upload work and ignores late responses. Do not embed image bytes in project metadata.

All photo surfaces on the bookshelf, collection cards, detail cover and cover picker reuse CoverImage. Measure the actual container and offer 160/320/640/1200-width candidates so the browser accounts for device pixel density; lazy-load clipped/offscreen cards and prioritize the first recent cover/detail. Keep fixed image geometry, asynchronously decode replacements before swapping out the last ready image, and reveal the initial image gently. Preserve aspect ratio: card thumbnails crop toward the upper part of portraits, while a large preview or picker uses contain when the aspect mismatch is substantial. Use neutral placeholders, a bounded original-image fallback when an optimized asset is unavailable, and retry failed loads on reconnect. Respect reduced motion. Never stretch a photo to a different ratio.

Preset WebP derivatives and their generated source manifest use content-fingerprinted filenames for immutable caching; regenerate from the existing originals with `cd backend && uv run python ../scripts/prepare_cover_assets.py`. Uploaded covers use the same width ladder through the image API; older uploads lazily acquire and retain derivatives, with ETag/304 revalidation. Direct external image links benefit from lazy loading/decoding but keep the host-provided bytes; do not silently proxy arbitrary URLs through the backend.

Setting-collection creation retains its compact dialog, at most 600px wide. Both dialog types use white paper, restrained ink and soft blurred backdrops. Labels use a medium 13px weight and inputs regular 14px text. Quiet text actions darken and underline on hover; compact dark primary actions sit at the bottom right. Use visible monochrome keyboard focus. Scroll only the designated body so the cover, close control and footer remain fixed in short desktop windows. In narrow windows, use a compact cover/title header over the same internally scrolling content. Native modal behavior isolates background controls, focuses the title field for creation and the close control for read-only details, returns focus on dismissal and prevents dismissal/repeated writes during submission. Failed saves retain fields and focus a recoverable error.

All creation/detail dialogs and label/setting/cover pickers share useDialogMotion: use a short opacity/scale/vertical entrance and fade the backdrop. Picker selection completes through the same close handler, including uploaded-image completion. Route close, cancel, Escape and backdrop dismissal through the same exit lifecycle: retain the native modal until its 240ms exit completes, make the closing content inert and restore focus only after removal. Use a bounded fallback if the end event is lost. Respect reduced motion by disabling panel/backdrop animations and dismissing immediately. Switching between the metadata form and chapter directory does not dismiss the dialog.

## Components

Make the active item unmistakable through text, position, and a subtle surface change; do not rely on a colored mark alone. Use a prominent page title, modest section headings, and 14–16 pixel body text with comfortable line height.

## Do's and Don'ts

Use native landmarks and accessible names for the banner, navigation, and main content. Maintain visible keyboard focus with the dark-blue focus treatment and a clear offset. Meet WCAG AA contrast for normal text over every surface it may occupy. Pair status color with a written label and, where useful, an icon or shape. Do not introduce remote fonts or assets.
