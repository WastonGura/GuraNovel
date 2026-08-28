# GuraNovel

<p align="center">
  <a href="README.md">简体中文</a> | <b>English</b>
</p>

<p align="center">
  <a href="https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml"><img src="https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11%2B-blue.svg" alt="Python"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg" alt="FastAPI"></a>
  <a href="https://react.dev/"><img src="https://img.shields.io/badge/React-19-61DAFB.svg" alt="React"></a>
  <a href="https://github.com/langchain-ai/langgraph"><img src="https://img.shields.io/badge/LangGraph-1.2.11-FF6F00.svg" alt="LangGraph"></a>
</p>

**GuraNovel** is a modular, high-reliability **AI-assisted long-form novel creation and review platform**.

Long-form novel authoring differs fundamentally from short-form text generation. It is prone to world-lore contradictions, plot holes, stylistic drift, and ungrounded "AI hallucination". GuraNovel is engineered specifically to ensure long-term plot continuity and rigorous multi-stage review. It unifies **Concept Ideation**, **Setting Evolution**, a **Chapter Production Pipeline (Outline - Drafting - Multi-Stage Review - Revision Loops)**, and a **Reader Panel Multi-Agent Critique System**, enabling authors to maintain full creative authority while benefiting from structured human-in-the-loop AI collaboration.

---

## End-to-End Novel Workflow

GuraNovel covers the entire lifecycle of long-form novel creation from initial concept ideation to ongoing serialized chapter reviews:

```text
┌────────────────────────┐     ┌──────────────────────┐     ┌────────────────────────┐     ┌───────────────────────┐
│ 1. Concept Selection   │ ──> │ 2. Lore & Setting    │ ──> │ 3. Chapter Production  │ ──> │ 4. Multi-Agent Reader │
│    & Multi-Ideation    │     │    Consistency Maint.│     │    Outline/Draft/Review│     │    Panel 6-Persona MVP│
└────────────────────────┘     └──────────────────────┘     └────────────────────────┘     └───────────────────────┘
```

### 1. Concept Selection & Ideation
- **Multi-Direction Parallel Brainstorming**: Automatically generates diverse thematic and stylistic concept proposals based on core story ideas;
- **Multi-Dimensional Comparison**: Evaluates and compares proposals across story appeal, long-form expandability, and setting feasibility;
- **Dedicated Project Workspace**: Instantly creates an isolated project workspace protected by POSIX filesystem permissions upon concept selection.

### 2. World-Building & Lore Maintenance
- **Structured Lore Management**: Character dossiers, factions, magic/power systems, world rules, and the master plot outline are centrally tracked;
- **Project Maintenance Mode**: When settings evolve or expand during serialization, the system automatically runs **impact blast-radius analysis** and **coordinated consistency confirmation** to prevent lore contradictions.

### 3. Chapter Production Pipeline (Chapter Production V2)
- **Chapter Outline Planning**: Automatically drafts chapter outlines grounded in previous chapter events and world lore, supporting online author editing and confirmation;
- **Manuscript Drafting**: Generates chapter prose strictly bound to the locked outline and lore constraints;
- **Multi-Stage In-Depth Review**: Automatically triggers structural integrity checks, stylistic pacing analysis, and world-lore consistency verification;
- **Collaborative Revision Loops**:
  - **Revision Feedback**: Authors can provide concrete correction directives;
  - **Manual Edit Integration**: Authors can directly edit the draft online and persist new version snapshots;
  - **Non-Blocking Warning Proceed (`accept_warning`)**: Acknowledges minor stylistic suggestions without blocking the pipeline;
  - **Blocking Revision Request (`request_revision`)**: Triggers targeted rewrites and iterative version evolution for critical narrative flaws.

### 4. Reader Panel Multi-Agent Critique System (v0.10.0 MVP)
- **6 Diverse Simulated Reader Personas**:
  - `general_immersive` (Everyday immersive reader: focuses on emotional resonance and pacing);
  - `low_patience` (Low-patience reader: focuses on opening hooks and pacing drags);
  - `genre_experienced` (Genre veteran: identifies cliches, tropes, and novel twists);
  - `character_emotion` (Character enthusiast: evaluates character motivations and emotional arcs);
  - `style_sensitive` (Prose critic: scrutinizes phrasing, rhythm, and descriptive texture);
  - `newcomer` (Genre newcomer: spots cognitive overload and world-building barriers).
- **Cold-Reading Isolation**: Reader agents only read manuscript segments in complete isolation from peer identities or outputs to avoid echo-chamber bias;
- **Normalized Issue Extraction & Blind Ballots**: The Moderator agent extracts normalized reading concerns; initial voting masks originator identities;
- **Bounded Discussion Turns**: Readers engage in multi-round, segment-bound discussions with code-owned termination to reach clear consensus;
- **Minority-Risk Preservation**: Retains independent high-risk alerts (`minority_high_risk`) and separately presents all-reader vs. target-audience voting distributions;
- **Non-Mutation & Editor Handoff**: The Reader Panel acts strictly as a diagnostic lens—it generates a structured read-only Review Report for editorial decision-making and **never alters manuscript prose**.

---

## Key Highlights

- 🎯 **Guaranteed Long-Form Continuity**: World lore, master outlines, and chapter draft hashes are strictly coupled, eliminating plot holes and power-scaling collapses.
- ✍️ **Author in Creative Control (Human-in-the-Loop)**: AI serves as an assistant and virtual reader panel; all critical outlines, revisions, and final approvals remain firmly in the author's hands.
- 👥 **Realistic Reader-Perspective Diagnostics**: Moves beyond generic, sycophantic LLM polishing to deliver candid, multi-perspective feedback across engagement, pacing, plot twists, and accessibility.
- 🛡️ **Version Snapshots & Ephemeral Crash Recovery**: Every generation, review, and edit produces an immutable version snapshot with monotonic event sequencing and single-step crash recovery.

---

## System Requirements & Constraints

Before deploying or running GuraNovel, please ensure your environment meets the following criteria:

1. **Operating System (POSIX Environment Required)**:
   - Project workspaces require POSIX `0700` filesystem permission boundaries;
   - **The backend must run on Linux, macOS, or WSL2**;
   - *Windows native filesystem paths (e.g. `D:\...`) do not support POSIX permission boundaries and must be run inside WSL2.*
2. **Database Requirement**:
   - **PostgreSQL 16+** with the `pgcrypto` extension enabled (for `gen_random_uuid()`).
3. **Language Toolchains (for local execution)**:
   - **Python**: `>= 3.11` with [`uv`](https://github.com/astral-sh/uv);
   - **Node.js**: `>= 20` and `npm` (for the frontend);
   - **Docker**: Docker & Docker Compose (for containerized deployment).

---

## Quickstart & Deployment

GuraNovel offers three deployment pathways. We recommend **One-Click Full-Stack Docker Compose** or the **Local Startup Script**.

### Option 1: One-Click Full-Stack Docker Compose (Recommended)

Ideal for launching PostgreSQL, the FastAPI backend, and the Nginx frontend in a unified containerized setup:

```bash
# 1. Clone the repository
git clone https://github.com/WastonGura/GuraNovel.git
cd GuraNovel

# 2. Build and launch all services
docker compose up -d --build
```

- **Frontend Workbench**: Open [http://localhost:5173](http://localhost:5173) in your browser.
- **Backend API Docs**: Open [http://localhost:8000/docs](http://localhost:8000/docs).
- **Stop Services**: Run `docker compose down`.

> [!TIP]
> **Docker Troubleshooting**:
> If you encounter `failed to connect to the docker API at unix:///var/run/docker.sock`:
> - **Windows WSL2 Users**: Open **Docker Desktop** on Windows and check `Settings -> Resources -> WSL Integration` to ensure your Linux distro is enabled.
> - **Linux Native Users**: Run `sudo service docker start` in your terminal to start the Docker daemon.

---

### Option 2: One-Click Local Startup Script (Development)

Ideal for local developers with Python 3.11+ and Node.js 20+ installed:

```bash
# Make executable and run the startup script
./scripts/start-local.sh
```

This script automatically:
1. Starts the PostgreSQL 16 container via Docker (or verifies local port 5432);
2. Installs Python dependencies and runs database migrations (`alembic upgrade head`);
3. Installs Node dependencies and concurrently launches FastAPI (`8000`) and the Vite dev server (`5173`).

---

### Option 3: Production Bare Metal / VM Deployment (Zero Docker)

For manual deployment on a dedicated Linux server:

#### 1. Install & Configure PostgreSQL 16
```bash
sudo apt update && sudo apt install -y postgresql-16
sudo -u postgres psql -c "CREATE DATABASE guranovel;"
sudo -u postgres psql -c "CREATE USER postgres WITH ENCRYPTED PASSWORD 'your_secure_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE guranovel TO postgres;"
```

#### 2. Configure and Run Backend
```bash
cd backend
cp .env.example .env

# Edit .env with your production database credentials and workspace path:
# DATABASE_URL=postgresql+asyncpg://postgres:your_secure_password@localhost:5432/guranovel
# WORKSPACE_BASE_DIR=/var/lib/guranovel/workspaces

# Install dependencies and apply migrations
uv sync --frozen --no-dev
uv run alembic upgrade head

# Run uvicorn via systemd or supervisor
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
```

#### 3. Build and Serve Frontend via Nginx
```bash
cd ../frontend
npm ci
npm run build
```
Configure Nginx (`/etc/nginx/sites-available/guranovel`):
```nginx
server {
    listen 80;
    server_name your-novel-domain.com;

    location / {
        root /path/to/GuraNovel/frontend/dist;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    location /api/v1/ {
        proxy_pass http://127.0.0.1:8000/api/v1/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## Technical Architecture & Under the Hood

```mermaid
graph TD
    UI[Frontend Workbench<br/>React 19 + TypeScript + Vite] -->|Fail-Closed REST API| API[FastAPI HTTP Routing Layer]
    API --> SVC[Domain Services Layer]
    SVC --> LG[LangGraph Orchestrator<br/>11-Node State Graph]
    SVC --> RP[Reader Panel Engine<br/>Multi-Agent Focus Group]
    LG --> DB[(PostgreSQL 16<br/>SQLAlchemy + Asyncpg)]
    RP --> DB
    SVC --> FS[Project Workspaces<br/>POSIX 0700 Directories]
    LG --> LLM[Model Provider Boundary<br/>Fake / OpenAI-Compatible]
    RP --> LLM
```

- **Backend Architecture**:
  - **State Graph Orchestration**: Built on **LangGraph 1.2.11** featuring an 11-node state graph with transactional boundary isolation and monotonic event sequencing (`WorkflowEvent.event_sequence`).
  - **Asynchronous Persistence**: Powered by **SQLAlchemy 2.0 (Asyncpg)** connection pooling with strict version-bound snapshots and SHA-256 hash checks.
  - **Model Abstraction Boundary**: Supports deterministic Mock/Fake providers for fast test suites and standard OpenAI-Compatible LLM endpoints.
- **Frontend Architecture**:
  - **Stack**: React 19, TypeScript, Vite, React Router 7.
  - **Design System**: Fully compliant with WAI-ARIA accessibility standards, verified by `@google/design.md`.
  - **Fail-Closed Contracts**: Strict client-side decoders ensuring automatic redaction of internal stack traces, private paths, and raw novel text.

---

## Verification & Testing

### Backend Gates
```bash
cd backend

# Code linting & formatting
uv run ruff check .

# Fast non-integration unit tests (1800+ tests)
uv run pytest -m "not integration"

# Alembic migration upgrade & rollback tests
uv run pytest tests/test_alembic.py

# PostgreSQL integration suite
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/guranovel_test \
  uv run pytest -m integration
```

### Frontend Gates
```bash
cd frontend

# Code linting
npm run lint

# TypeScript check & production build
npm run build

# Design system guideline validation
npx --no-install @google/design.md lint DESIGN.md

# Vitest component & route tests
npm run test -- --run

# Playwright browser end-to-end tests
npm run test:e2e
```

---

## Documentation Navigation

- [System Architecture](docs/architecture.md): Detailed component relationships, data flows, and concurrency models.
- [Reader Panel Verification Matrix](docs/reader-panel-verification.md): Acceptance criteria mapping (AC-01 through AC-14) and automated test evidence.
- [Backend & Database Setup](backend/README.md): Local PostgreSQL setup, project workspaces, and database migrations.
- [Contributing Guidelines](CONTRIBUTING.md): Code review policies, release verification gates, and repository conventions.

---

## License

This project is licensed under the GNU General Public License v3.0 ([GPL-3.0](LICENSE)).
