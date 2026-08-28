#!/usr/bin/env bash
set -euo pipefail

# GuraNovel one-click local startup script
echo "=== Starting GuraNovel Local Environment ==="

# 1. Start PostgreSQL via Docker if available
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "[1/4] Ensuring PostgreSQL is running..."
  docker compose up -d db
else
  echo "[1/4] Docker not found; assuming local PostgreSQL is already running on localhost:5432"
fi

# 2. Setup backend
echo "[2/4] Setting up backend and applying database migrations..."
cd backend
if [ ! -f .env ]; then
  cp .env.example .env
fi
uv sync --frozen
uv run alembic upgrade head
cd ..

# 3. Setup frontend
echo "[3/4] Setting up frontend..."
cd frontend
if [ ! -d node_modules ]; then
  npm ci
fi
cd ..

# 4. Launch backend and frontend concurrently
echo "[4/4] Starting backend (http://127.0.0.1:8000) and frontend (http://127.0.0.1:5173)..."
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT

(cd backend && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) &
(cd frontend && npm run dev) &

wait
