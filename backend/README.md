# GuraNovel backend

## Local PostgreSQL

From the repository root, start the local PostgreSQL 16 database:

```bash
docker compose up -d db
```

Check that it is healthy:

```bash
docker compose ps
```

The local database uses `postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/guranovel`.
These are development-only defaults.

## Project workspaces

Project workspaces are created only beneath `WORKSPACE_BASE_DIR`. The local-development
default is `./workspaces` (relative to the backend working directory); set it in `.env`
to use another POSIX/Linux/WSL directory. Windows-native filesystems are not supported.

## PostgreSQL integration tests

Integration tests are marked separately and never use the development database. Create the
dedicated test database once, then run them with an explicit URL:

```bash
# From the repository root
docker compose up -d db
docker compose exec -T db createdb -U postgres guranovel_test

cd backend
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/guranovel_test \
  uv run pytest -m integration
```

`TEST_DATABASE_URL` is required and its database name must end in `_test`. The harness applies
Alembic `head` and truncates mapped application tables between tests.

If Docker Desktop does not forward Compose ports into WSL, run the same command from a temporary
container on the Compose network instead:

```bash
docker run --rm --network guranovel_default -v "$PWD/backend:/app" -w /app \
  -e TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/guranovel_test \
  -e UV_PROJECT_ENVIRONMENT=/tmp/guranovel-venv \
  ghcr.io/astral-sh/uv:python3.11-bookworm-slim \
  sh -c 'uv sync --frozen --all-groups && uv run pytest -m integration'
```

## Development checks

```bash
cd backend
uv sync --all-groups
uv run ruff check .
uv run pytest
uv run pytest -m integration  # requires TEST_DATABASE_URL
```

## Database migrations

Set `DATABASE_URL` to an async SQLAlchemy PostgreSQL URL. The example configuration uses a local database:

```bash
cd backend
cp .env.example .env
# Edit DATABASE_URL if needed.
uv run alembic upgrade head
```

To tear down the local database while keeping its data:

```bash
cd ..
docker compose down
```

To remove the database data as well, use `docker compose down -v`.

Useful commands:

```bash
# Apply all migrations
uv run alembic upgrade head

# Revert the latest migration
uv run alembic downgrade -1

# Print PostgreSQL migration SQL without connecting
uv run alembic upgrade head --sql

# Create a new migration after changing models
uv run alembic revision --autogenerate -m "describe schema change"
```

The initial migration enables PostgreSQL `pgcrypto`, because the schema uses `gen_random_uuid()` for UUID primary keys.
