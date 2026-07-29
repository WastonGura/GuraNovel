# GuraNovel Agent Instructions

## CodeGraph

This repository uses a local CodeGraph index for structural code intelligence.

- For architecture questions, cross-file flows, symbol discovery, or change-impact analysis, run `codegraph explore "<question>"` before starting a broad `rg`/file-reading loop.
- Treat returned source as already read. If CodeGraph reports stale or pending files, read those files directly or run `codegraph sync`.
- Use `codegraph affected <changed-files...>` to help select focused tests, but keep the repository's canonical frontend and backend verification gates authoritative.
- Fall back to `rg` and direct file inspection when CodeGraph is unavailable, uninitialized, or does not cover the required non-code artifact.
- The `.codegraph/` index is local generated state. Never commit it.
- Disable telemetry in automated or agent-driven runs with `CODEGRAPH_TELEMETRY=0`.
