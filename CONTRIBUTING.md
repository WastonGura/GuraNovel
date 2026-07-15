# Contributing to GuraNovel

GuraNovel uses an issue-driven pull request workflow.

## Branch strategy

The repository does not use a `dev` branch.

- `main` is the stable branch.
- Work happens on short-lived issue branches.

Branch naming:

```text
feature/<issue-number>-<short-name>
fix/<issue-number>-<short-name>
docs/<issue-number>-<short-name>
ci/<issue-number>-<short-name>
refactor/<issue-number>-<short-name>
test/<issue-number>-<short-name>
```

Examples:

```text
feature/12-backend-scaffold
fix/27-document-version-conflict
ci/31-backend-github-actions
```

## Commit convention

Use Conventional Commits:

```text
type(scope): short description
```

Common types:

- `feat`
- `fix`
- `docs`
- `test`
- `ci`
- `refactor`
- `chore`
- `perf`

Examples:

```text
feat(backend): scaffold FastAPI app
feat(document): add document version service
fix(workflow): prevent force approving blocking issues
ci(backend): add ruff and pytest workflow
```

## Issue-driven development

Every non-trivial change should start from a GitHub issue.

A good issue includes:

- Goal
- Scope
- Acceptance criteria
- Test plan
- Out of scope

Each PR should normally close exactly one issue. Use this in the PR body:

```text
Closes #<issue-number>
```

## Pull request requirements

Before review, a PR should:

- Have a clear title using Conventional Commit style.
- Link its issue with `Closes #N`.
- Explain the implementation summary.
- Include a test plan.
- Pass CI.
- Avoid unrelated scope creep.

## Review policy

A PR can be merged when:

- It satisfies the issue acceptance criteria.
- CI passes.
- Review does not find blocking correctness, security, or maintainability issues.

Use squash merge for feature branches, then delete the branch.

## Local/private files

The following are intentionally not committed:

- `docs/design/` private design drafts and planning notes.
- Real `workspaces/` novel projects and version snapshots.
- `.env` files and local caches.

Use public docs such as `docs/architecture.md` later for cleaned-up design documentation.
