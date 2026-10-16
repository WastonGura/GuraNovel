# GuraNovel

**An AI novel-writing workbench, from chapter ideas to a local final draft.**

[简体中文](README.md) · [v0.12.0 trial release](https://github.com/WastonGura/GuraNovel/releases/tag/v0.12.0) · [GitHub Actions](https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml) · [GPLv3](LICENSE)

GuraNovel helps individual authors compare outlines, edit prose, request revisions and review a chapter in one workspace. You choose the outline, decide which edits to accept and confirm the final draft; AI supplies alternatives and feedback.

The current release is **v0.12.0, a desktop personal trial (prerelease)**. Try the complete chapter workflow on your computer without an API key, then connect a real model to evaluate generation and revision quality.

![GuraNovel Studio: a real-model trial chapter finalized locally and displayed read-only](docs/acceptance/images/v0.12.0-real-trial.png)

*Studio showing a locally finalized real-model trial chapter. See the [desktop acceptance record](docs/acceptance/v0.12.0-desktop.md) for the process and quality limitations.*

[Quickstart](#docker-quickstart) · [Your first chapter](#your-first-chapter) · [Real models](#real-models) · [Limitations and roadmap](#limitations-and-roadmap) · [Development](#local-development) · [Documentation](#documentation)

## What you can do

**Create a novel → save chapter ideas → generate three outlines → compare, select and edit → approve the outline → draft and revise → review → optional Reader → finalize locally**

| Area | Available now |
| --- | --- |
| Novels and settings | Bookshelf, Studio and independent setting collections; edit, soft-delete and restore setting documents while preserving identity and history |
| Ideas and outlines | Autosaved ideas; three alternatives per batch; compare, select, edit or generate another batch while retaining previous batches |
| Prose and revisions | Draft from the approved outline; edit directly, comment on selected text and submit revision requirements with source-version tracking |
| Reviews | Staged Editor, Chief Editor and Lore reviews; optional Reader feedback |
| Saving and finalization | Server persistence, conflict checks and local recovery copies; read-only final drafts |

Outline approval binds an exact version. Drafting locks that outline; subsequent changes use prose revision. Reader provides diagnostics without rewriting prose. **Local finalization saves within this service and does not publish externally.**

## Docker quickstart

Use **Docker Engine / Docker Desktop with Docker Compose**. On Windows, select Linux containers; native Windows is not a supported backend deployment environment.

### 1. Start the trial release

For a new installation, pin the published version:

```bash
git clone --branch v0.12.0 --depth 1 https://github.com/WastonGura/GuraNovel.git
cd GuraNovel
git rev-parse HEAD
docker compose up -d --build --wait
docker compose ps
```

Open the **[workbench](http://127.0.0.1:5173)** or [API docs](http://127.0.0.1:8000/docs). The first build downloads images and dependencies.

The default **fake Provider** returns deterministic examples without an API key; Reader is off. Use it to try the workflow and persistence. For AI-generated writing, follow [Real models](#real-models).

> Existing installations: read the [upgrade and backup notes](docs/releases/v0.12.0.md#从-v0110-升级) first. Keep the original Compose project name, volumes and private configuration.

### 2. Check health

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8000/api/v1/health
curl --noproxy '*' -fsS http://127.0.0.1:5173/api/v1/health
```

Use `curl.exe` in PowerShell. Both endpoints should report healthy with version `0.12.0`.

### 3. Inspect logs and stop safely

```bash
docker compose logs --tail=100 backend frontend db
docker compose down
```

`docker compose down` retains data volumes. Restart with the same Compose project in the same directory to continue. **`docker compose down -v` permanently deletes the database and workspaces; never use it for upgrades.** Database and workspace files must be backed up and restored together.

## Your first chapter

1. Create a novel on the bookshelf, enter Studio and add a chapter. Edit its setting collection if needed.
2. Enter chapter ideas, wait for saving to complete and generate outline alternatives.
3. Compare the three options, select one and edit it. Generating another batch retains previous batches.
4. Approve the outline and generate prose. Edit directly, comment on selected text or submit revision requirements.
5. Submit for review and address the feedback. With real models configured, optionally invite Reader or skip that step.
6. Confirm local finalization to save the chapter as a read-only final draft.

If saving or generation fails, inspect the save indicator and original task before retrying. A timeout does not necessarily mean the server did nothing. On a version conflict, preserve your local text and compare it with the server before continuing.

## Real models

### 1. Configure the provider

Copy `backend/.env.example` to `backend/.env` (`cp` in Bash, `Copy-Item` in PowerShell), then configure:

```dotenv
CHAPTER_GENERATION_PROVIDER=openai_compatible
CHAPTER_PRODUCTION_PROVIDER=openai_compatible
READER_PANEL_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://your-provider.example/v1
OPENAI_COMPATIBLE_API_KEY=replace-with-your-private-key
OPENAI_COMPATIBLE_MODEL=your-supported-model
OPENAI_COMPATIBLE_TIMEOUT_SECONDS=120
```

`backend/.env` is excluded from Git and Docker build contexts. Never commit credentials. Real-model requests send the necessary prose and setting context to your configured provider and may incur charges.

### 2. Start with the model override

Base Compose does not automatically load `backend/.env`. Use the supplied override:

```bash
docker compose -f docker-compose.yml -f docker-compose.models.example.yml up -d --build --wait
```

The override enables `quick` Reader; change its `READER_PANEL_MODE` to `off` to disable it. Base Compose still controls the container database and workspace paths. Use the same `-f` combination for subsequent status, logs and stop commands.

If supported, optionally set `OPENAI_COMPATIBLE_REASONING_EFFORT` to `none`, `low`, `medium`, `high` or `max`; otherwise the parameter is omitted. Sampling used `deepseek-flash`, `none` and a 120-second timeout. Other models require separate compatibility, latency and quality verification.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Docker is unavailable | Start Docker Desktop and check Linux containers / WSL integration, or check the Docker service on Linux |
| Port 5173 or 8000 is occupied | Set `GURANOVEL_FRONTEND_PORT` / `GURANOVEL_BACKEND_PORT` in the root `.env`, restart and use those ports for access and health checks |
| Examples still appear after configuring a key | Use the model override and set both `CHAPTER_GENERATION_PROVIDER` and `CHAPTER_PRODUCTION_PROVIDER` to `openai_compatible` |
| Generation times out or returns invalid output | Inspect the original task and logs before retrying through the UI to avoid duplicate model work |

The root `.env` configures Compose interpolation, including ports; `backend/.env` configures models and the backend. Application ports bind to `127.0.0.1` and PostgreSQL is not exposed to the host. `GURANOVEL_BIND_ADDRESS=0.0.0.0` exposes application ports to other hosts; this trial was accepted for local use.

## Limitations and roadmap

Models can omit edits, miss contradictions, overrun length targets or return invalid structured output and English reviews. Multiple reviewers passing a chapter does not guarantee correctness. Two real-model samples reached local finalization, including one actual Reader session. Workflow completion and content quality are reported separately in the [acceptance record](docs/acceptance/v0.12.0-desktop.md).

| Planned work | Tracking |
| --- | --- |
| Setting discussions and full historical recovery | [#301](https://github.com/WastonGura/GuraNovel/issues/301), [#311](https://github.com/WastonGura/GuraNovel/issues/311) |
| Mobile support, novel metadata and volumes | [#304](https://github.com/WastonGura/GuraNovel/issues/304), [#307](https://github.com/WastonGura/GuraNovel/issues/307), [#308](https://github.com/WastonGura/GuraNovel/issues/308) |
| Assistant consistency and exports | [#310](https://github.com/WastonGura/GuraNovel/issues/310), [#312](https://github.com/WastonGura/GuraNovel/issues/312) |
| Concept / maintenance interfaces, legacy continuation and broader acceptance | [Development plan](docs/development-plan-2026-10.md), [#313](https://github.com/WastonGura/GuraNovel/issues/313) |

The bookshelf, Studio and setting collections form a single official interface. Old workbench links redirect; legacy V1 tasks remain read-only. Unavailable controls are disabled or explain their limits. Collaboration, public deployment, external publishing and EPUB are outside this release.

## Local development

Use a full checkout of `main` and an issue branch for development; the shallow tag clone above is for a pinned trial. Stack: **React / TypeScript / Vite + FastAPI / SQLAlchemy / PostgreSQL + versioned workspace files**.

Use Linux/macOS/WSL, Python 3.11+, uv, reachable PostgreSQL 16 and Node.js 20.19+ or 22.12+/npm (Docker/CI uses Node 22). Configure `backend/.env` with a local `DATABASE_URL` and writable POSIX `WORKSPACE_BASE_DIR`. Default Compose does not expose its database, so prepare a separate reachable database for local development.

From the repository root:

```bash
cd backend
uv sync --frozen --all-groups
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, also from the repository root:

```bash
cd frontend
npm ci
npm run dev
```

The frontend proxies to `127.0.0.1:8000` by default. Use **Red → Green → Refactor** for new behaviors; existing implementations receive regression coverage. Run focused tests locally and use uv for Python installation and execution.

**GitHub Actions** runs the full gates: Ruff, architecture/unit, PostgreSQL integration/migrations, frontend lint/unit/types/build/design/Chromium, version consistency and Docker author workflow. Use **issue branches → PR → review → Squash merge**, with no direct pushes to `main`. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

| Document | Contents |
| --- | --- |
| [Release and upgrade notes](docs/releases/v0.12.0.md) | Changes, migrations, paired backups and recovery |
| [Desktop acceptance](docs/acceptance/v0.12.0-desktop.md) | Docker, interaction checks, real-model sampling and limitations |
| [Interface guide](docs/frontend-interface.md) | Official routes, compatibility and capability boundaries |
| [Development plan](docs/development-plan-2026-10.md) | Milestones, deferred scope and acceptance |
| [Development log](docs/development-log.md) | Implementation, merges, validation and releases, dated in Tokyo time |
| [Architecture](docs/architecture.md) | Backend structure, data and workflows |
| [Contributing](CONTRIBUTING.md) | Branches, commits, TDD, PRs and checks |

Licensed under the [GNU GPL v3](LICENSE).
