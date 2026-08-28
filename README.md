# GuraNovel

[![CI](https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml/badge.svg)](https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2.11-FF6F00.svg)](https://github.com/langchain-ai/langgraph)

**GuraNovel** is a robust, modular, and AI-assisted long-form novel creation platform. It integrates multi-agent collaborative critique, deterministic workflow orchestration, strict version control, and privacy-preserving data boundaries to assist authors throughout the entire novel writing and review lifecycle.

---

## Key Features

- **Concept Selection & Project Workspace**: Multi-concept ideation, structured parameter management, and isolated file-based workspaces with strict POSIX path-security boundaries.
- **Project Maintenance & Setting Evolution**: Checkpointed consistency analysis, impact assessment, and coordinated revision confirmation for characters, world lore, and plot outlines.
- **Chapter Production V2 (LangGraph Orchestration)**: An 11-node state graph engine with transactional boundary isolation, monotonic event sequencing (`WorkflowEvent.event_sequence`), claim-token ABA protection, and ephemeral state crash recovery.
- **Multi-Stage Review & Revision Saga**: Deterministic chapter reviews (structure, style, world lore) with separate paths for non-blocking warnings (`accept_warning`), author feedback loops, manual edits, and blocking revisions (`request_revision`).
- **Simulated Reader Panel (v0.10.0 MVP)**: A multi-agent reader focus group simulating distinct reader personas (`general_immersive`, `low_patience`, `genre_experienced`, `character_emotion`, `style_sensitive`, `newcomer`) and Moderator synthesis modes:
  - **Cold-Reading Isolation**: Readers evaluate manuscript segments independently without exposure to peer identities or outputs.
  - **Normalized Issue Extraction & Blind Balloting**: Anonymous issue identification with blind initial ballots masking originators.
  - **Bounded Discussion Turns**: Issue-scoped, segment-bound multi-round dialogue with code-owned termination.
  - **Minority-Risk Preservation**: Independent high-risk retention alongside separate raw and target-audience voting distributions.
  - **Editor Handoff & Non-Mutation Guarantee**: Produces structured, non-approved diagnostic reports for author/editor decisions without ever modifying chapter manuscripts.
- **Fail-Closed Privacy & Security Boundaries**: Content-free error handling, strict UUID normalization, calendar-validated ISO-8601 timestamps, SHA-256 content hashes, and zero leakage of raw prompts, novel prose, or credentials.

---

## Architecture Overview

```mermaid
graph TD
    UI[Frontend Workbench<br/>React 19 + TypeScript] -->|Fail-Closed REST API| API[FastAPI HTTP Layer]
    API --> SVC[Domain Services Layer]
    SVC --> LG[LangGraph Orchestrator<br/>11-Node State Graph]
    SVC --> RP[Reader Panel Engine<br/>Multi-Agent Focus Group]
    LG --> DB[(PostgreSQL 16<br/>SQLAlchemy + Asyncpg)]
    RP --> DB
    SVC --> FS[Project Workspaces<br/>POSIX 0700 Directories]
    LG --> LLM[AI Provider Boundary<br/>Fake / OpenAI-Compatible]
    RP --> LLM
```

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.0 (Asyncpg), PostgreSQL 16 (`pgcrypto`), Alembic, LangGraph 1.2.11, Pydantic v2.
- **Frontend**: React 19, TypeScript, Vite, React Router 7, WAI-ARIA accessible design system (`@google/design.md` verified).

---

## Quickstart

### Prerequisites

- [Docker](https://www.docker.com/) & Docker Compose
- [Python 3.11+](https://www.python.org/) and [`uv`](https://github.com/astral-sh/uv)
- [Node.js 20+](https://nodejs.org/) and `npm`

### 1. Start the Local Database

```bash
docker compose up -d db
```

Verify that PostgreSQL is healthy on `127.0.0.1:5432`:

```bash
docker compose ps
```

### 2. Configure and Start the Backend

```bash
cd backend
cp .env.example .env

# Apply database migrations
uv run alembic upgrade head

# Run the FastAPI server
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### 3. Start the Frontend Workbench

```bash
cd ../frontend
npm install
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173) in your browser to access the workbench.

---

## Verification & Testing

GuraNovel enforces comprehensive quality gates across backend, frontend, and database layers.

### Backend Verification

```bash
cd backend

# Code formatting and linting
uv run ruff check .

# Fast non-integration unit tests (1800+ tests)
uv run pytest -m "not integration"

# Alembic migration upgrade/downgrade verification
uv run pytest tests/test_alembic.py

# PostgreSQL integration suite (requires dedicated test database)
docker compose exec -T db createdb -U postgres guranovel_test 2>/dev/null || true
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/guranovel_test \
  uv run pytest -m integration
```

### Frontend Verification

```bash
cd frontend

# Code linting
npm run lint

# TypeScript compilation and production build
npm run build

# Design system token and guideline linter
npx --no-install @google/design.md lint DESIGN.md

# Vitest component and client unit tests
npm run test -- --run

# Playwright browser end-to-end tests
npm run test:e2e
```

---

## Documentation

- [System Architecture](docs/architecture.md): Detailed component relationships, data flows, and concurrency models.
- [Reader Panel Verification Matrix](docs/reader-panel-verification.md): Acceptance criteria mapping (AC-01 through AC-14) and automated test evidence.
- [Backend & Database Setup](backend/README.md): Local PostgreSQL setup, project workspaces, and database migrations.
- [Contributing Guidelines](CONTRIBUTING.md): Code review policies, release verification gates, and repository conventions.

---

## License

This project is licensed under the terms of the GNU General Public License v3.0 ([GPL-3.0](LICENSE)).
