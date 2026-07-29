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

## Components

Make the active item unmistakable through text, position, and a subtle surface change; do not rely on a colored mark alone. Use a prominent page title, modest section headings, and 14–16 pixel body text with comfortable line height.

## Do's and Don'ts

Use native landmarks and accessible names for the banner, navigation, and main content. Maintain visible keyboard focus with the dark-blue focus treatment and a clear offset. Meet WCAG AA contrast for normal text over every surface it may occupy. Pair status color with a written label and, where useful, an icon or shape. Do not introduce remote fonts or assets.
