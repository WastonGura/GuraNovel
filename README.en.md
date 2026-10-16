# GuraNovel

[简体中文](README.md) · [GitHub Actions](https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml) · [GPLv3](LICENSE)

GuraNovel is an AI writing workbench for individual novel authors. **v0.12.0 targets a desktop personal trial**. Check [GitHub Releases](https://github.com/WastonGura/GuraNovel/releases) for publication status.

## Delivered workflow

Create a novel → save chapter ideas → generate three outline alternatives → compare, select, edit and approve an exact version → draft and revise prose → editorial review → optional Reader → finalize locally.

The bookshelf, Studio and setting collections are the official interface. Old links redirect; legacy V1 tasks remain read-only. Ideas and comments persist with revision/version checks and local recovery copies. Unmatched comment anchors are explicitly marked. Outline batches and context versions are retained; selecting creates an independent editable outline, and prose generation locks its approved version. Setting documents support editing, soft deletion and restoration with identity/history preserved.

Editor, Chief Editor and Lore provide staged reviews. Optional Reader advice never rewrites prose. Local finalization saves within this service and does not publish externally. Models can omit edits, miss contradictions, overrun length targets or return invalid structured output; multiple reviewers do not guarantee correctness. See the [desktop acceptance record](docs/acceptance/v0.12.0-desktop.md) for actual calls, failures and limitations. The default **fake Provider** produces deterministic workflow examples, not evidence of model quality.

Setting discussions, new concept/maintenance interfaces, legacy continuation, full historical recovery, mobile support, cover/introduction fixes, volumes, assistant consistency, exports/EPUB, collaboration and public deployment are deferred. See the [plan](docs/development-plan-2026-10.md), [interface notes](docs/frontend-interface.md) and [#313](https://github.com/WastonGura/GuraNovel/issues/313).

## Docker quickstart

Install Docker Engine/Desktop and Compose. On Windows use Docker Desktop Linux containers; the backend relies on POSIX workspace permissions.

```bash
git clone https://github.com/WastonGura/GuraNovel.git
cd GuraNovel
git rev-parse HEAD
docker compose up -d --build --wait
docker compose ps
```

After release, check out `v0.12.0` to pin the version. Record the tested commit. No API key is needed for the default examples; Reader is off. Open the [workbench](http://127.0.0.1:5173) or [API docs](http://127.0.0.1:8000/docs).

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8000/api/v1/health
curl --noproxy '*' -fsS http://127.0.0.1:5173/api/v1/health
```

Use `curl.exe` in PowerShell. Ports bind to loopback and PostgreSQL is not published. `GURANOVEL_BIND_ADDRESS=0.0.0.0` exposes application ports to other hosts; keep the default loopback binding for this trial. Public deployment is not covered by acceptance. Set `GURANOVEL_FRONTEND_PORT` / `GURANOVEL_BACKEND_PORT` in the root `.env` if occupied. That file configures Compose interpolation; model settings use the separate `backend/.env`.

```bash
docker compose logs --tail=100 backend frontend db
docker compose down
```

`docker compose down` retains volumes; `docker compose down -v` permanently deletes the database and workspaces. Never use it for upgrades. If Docker is unavailable, check Docker Desktop Linux/WSL integration or the Linux Docker service.

## Real models

Copy `backend/.env.example` to `backend/.env` and configure your service. This file is excluded from Git and Docker build contexts. Base Compose does not forward it automatically; use:

```bash
docker compose -f docker-compose.yml -f docker-compose.models.example.yml up -d --build --wait
```

Configure in `backend/.env`:

```dotenv
CHAPTER_GENERATION_PROVIDER=openai_compatible
CHAPTER_PRODUCTION_PROVIDER=openai_compatible
READER_PANEL_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://your-provider.example/v1
OPENAI_COMPATIBLE_API_KEY=replace-with-your-private-key
OPENAI_COMPATIBLE_MODEL=your-supported-model
OPENAI_COMPATIBLE_TIMEOUT_SECONDS=120
```

The override enables quick Reader; change its `READER_PANEL_MODE` to `off` to disable it. Container database/workspace settings still take precedence over sample host paths. Never commit credentials.

When supported, optionally set `OPENAI_COMPATIBLE_REASONING_EFFORT=none|low|medium|high|max`; otherwise it is omitted. Sampling used `deepseek-flash`, `none`, 120 seconds; other models may differ. See [DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/). After a lost/timeout response, inspect the original task before recovery instead of creating duplicate model work.

## Local development

Use Linux/macOS/WSL, Python 3.11+, uv, reachable PostgreSQL 16, and Node.js 20+/npm. Configure `backend/.env` with a writable POSIX `WORKSPACE_BASE_DIR` and local `DATABASE_URL`. Default Compose does not expose its database to the host, so prepare a separate reachable database.

```bash
cd backend
uv sync --frozen --all-groups
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Use Red → Green → Refactor for new behaviors. Existing implementations receive regression coverage, not retrospective TDD claims. Run focused checks locally using uv; GitHub Actions runs Ruff, architecture/unit, PostgreSQL integration/migrations, frontend lint/unit/types/build/design/Chromium, version consistency and Docker author workflow gates.

Use issue branches → PR → review → squash merge; never push main directly. See [CONTRIBUTING.md](CONTRIBUTING.md), the [development log](docs/development-log.md) and [release/upgrade notes](docs/releases/v0.12.0.md). Database and workspace data must be backed up/restored together.

Backend: FastAPI, SQLAlchemy, PostgreSQL and versioned workspaces. Frontend: React, TypeScript and Vite. Licensed under [GPLv3](LICENSE).
