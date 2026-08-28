FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    WORKSPACE_BASE_DIR=/data/workspaces \
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/guranovel

# Install dependencies first for better layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Copy application source code
COPY . .
RUN uv sync --frozen

# Ensure workspace data directory exists
RUN mkdir -p /data/workspaces

EXPOSE 8000

# Apply Alembic migrations and launch FastAPI server
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
