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

## Development checks

```bash
cd backend
uv sync --all-groups
uv run ruff check .
uv run pytest
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
