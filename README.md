# GuraNovel

<p align="center">
  <a href="#guranovel-简体中文">简体中文</a> | <a href="#guranovel-english">English</a>
</p>

<p align="center">
  <a href="https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml"><img src="https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11%2B-blue.svg" alt="Python"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg" alt="FastAPI"></a>
  <a href="https://react.dev/"><img src="https://img.shields.io/badge/React-19-61DAFB.svg" alt="React"></a>
  <a href="https://github.com/langchain-ai/langgraph"><img src="https://img.shields.io/badge/LangGraph-1.2.11-FF6F00.svg" alt="LangGraph"></a>
</p>

---

# GuraNovel (简体中文)

**GuraNovel** 是一款模块化、高可靠的 AI 辅助长篇小说创作与审阅工作台平台。系统深度融合了 **LangGraph 状态图工作流编排** 与 **Reader Panel（读者剧场）多 Agent 协作评审系统**，具备严格的文档版本控制、事务边界隔离、fail-closed 安全脱敏与全生命周期确定性保障。

---

## 核心特性

- **多概念立项与独立工作区**：支持多方向概念构思、结构化参数管理与基于 POSIX `0700` 权限的独立文件系统安全边界。
- **项目设定维护与世界观演化**：具备一致性分析、波及范围诊断与协同确认机制，支撑角色、世界观和故事主线演进。
- **Chapter Production V2（LangGraph 编排引擎）**：基于 11 节点状态图架构，实现节点级事务隔离、单调递增事件序列（`event_sequence`）、声明令牌 ABA 防护与瞬态故障崩溃恢复。
- **多阶段审阅与修订工作流**：支持大纲/正文起草、结构/文风/世界观深度审阅，区分非阻塞警告通过（`accept_warning`）、修改建议循环、作者手动编辑与阻塞项修订（`request_revision`）。
- **Reader Panel（读者剧场）多 Agent 协作评审**：
  - **多重预设角色**：内置 6 类读者角色（沉浸型、低耐心型、资深类型读者、情感关注型、文风敏感型、新手读者）与 3 类主持人模式。
  - **盲读隔离**：读者仅能访问正文切片，杜绝其他读者身份及评语的上下文污染。
  - **规范化议题提取与盲投**：匿名提炼核心阅读关切，初始盲投隐藏提议者信息。
  - **有界讨论与收敛**：围绕具体议题展开切片限定的多轮讨论，由代码统一控制终止轮次。
  - **少数派风险保留**：独立留存少数派高危风险标记（`minority_high_risk`），独立呈现全员原始分布与目标受众分布。
  - **非篡改原则与编辑交接**：只读诊断机制，绝不直接篡改正文，最终输出结构化编辑交接报告。
- **Fail-Closed 隐私与安全边界**：无正文/无提示词/无堆栈的外显错误边界、严格的 UUID 格式化、ISO-8601 时间戳检查与 SHA-256 数据防篡改校验。

---

## 系统架构

```mermaid
graph TD
    UI[Frontend Workbench<br/>React 19 + TypeScript + Vite] -->|Fail-Closed REST API| API[FastAPI HTTP Layer]
    API --> SVC[Domain Services Layer]
    SVC --> LG[LangGraph Orchestrator<br/>11-Node State Graph]
    SVC --> RP[Reader Panel Engine<br/>Multi-Agent Focus Group]
    LG --> DB[(PostgreSQL 16<br/>SQLAlchemy + Asyncpg)]
    RP --> DB
    SVC --> FS[Project Workspaces<br/>POSIX 0700 Directories]
    LG --> LLM[AI Provider Boundary<br/>Fake / OpenAI-Compatible]
    RP --> LLM
```

---

## 部署与快速启动

GuraNovel 支持以下三种部署运行方式，推荐优先使用 **Docker Compose 全栈一键启动** 或 **本地一键启动脚本**。

### 方式一：Docker Compose 全栈一键部署（推荐）

适合希望一键拉起数据库、后端和前端完整服务的用户：

```bash
# 1. 克隆代码仓库
git clone https://github.com/WastonGura/GuraNovel.git
cd GuraNovel

# 2. 一键启动全部服务（数据库 + 后端 + 前端反向代理）
docker compose up -d --build
```

- **前端工作台**：打开浏览器访问 [http://localhost:5173](http://localhost:5173)
- **后端 API 文档**：访问 [http://localhost:8000/docs](http://localhost:8000/docs)
- **停止服务**：执行 `docker compose down`

---

### 方式二：本地一键极速启动脚本（开发环境推荐）

适合本地具备 Python 3.11+ 与 Node.js 20+ 环境的开发者：

```bash
# 赋予执行权限并直接运行一键启动脚本
./scripts/start-local.sh
```

该脚本将自动执行以下步骤：
1. 自动启动 PostgreSQL 16 数据库容器（若已安装 Docker）或检测本地 5432 端口；
2. 安装后端依赖并执行数据库迁移（`alembic upgrade head`）；
3. 安装前端依赖并并行拉起 FastAPI（`8000` 端口）与 Vite 前端热重载服务（`5173` 端口）。

---

### 方式三：生产环境裸机 / 虚拟机部署（零 Docker）

如果你希望在私有 Linux 服务器上手动管理部署服务：

#### 1. 安装与配置 PostgreSQL 16
```bash
sudo apt update && sudo apt install -y postgresql-16
sudo -u postgres psql -c "CREATE DATABASE guranovel;"
sudo -u postgres psql -c "CREATE USER postgres WITH ENCRYPTED PASSWORD 'your_secure_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE guranovel TO postgres;"
```

#### 2. 配置并运行后端服务
```bash
cd backend
cp .env.example .env

# 编辑 .env，配置生产数据库连接与工作区目录：
# DATABASE_URL=postgresql+asyncpg://postgres:your_secure_password@localhost:5432/guranovel
# WORKSPACE_BASE_DIR=/var/lib/guranovel/workspaces

# 安装依赖并执行数据库迁移
uv sync --frozen --no-dev
uv run alembic upgrade head

# 使用生产进程管理器（如 systemd 或 supervisor）运行 uvicorn：
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
```

#### 3. 构建并配置前端 Nginx 托管
```bash
cd ../frontend
npm ci
npm run build
```
在 `/etc/nginx/sites-available/guranovel` 中配置 Nginx：
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

## 质量门禁与测试运行

### 后端门禁
```bash
cd backend

# 代码风格与静态分析
uv run ruff check .

# 快速非集成单元测试（1800+ 用例）
uv run pytest -m "not integration"

# Alembic 数据库迁移升级与回滚测试
uv run pytest tests/test_alembic.py

# PostgreSQL 真实数据库集成测试（需要测试数据库）
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/guranovel_test \
  uv run pytest -m integration
```

### 前端门禁
```bash
cd frontend

# ESLint 语法与规范检查
npm run lint

# TypeScript 类型检查与生产构建
npm run build

# Google Design.md 规范与设计系统 Token 检查
npx --no-install @google/design.md lint DESIGN.md

# Vitest 单元与路由测试
npm run test -- --run

# Playwright 浏览器端到端全流程测试
npm run test:e2e
```

---

## 核心系统约束

1. **操作系统权限**：小说工作区采用严格的 POSIX `0700` 文件权限机制，因此**后端服务必须运行在 Linux / macOS / WSL 环境**，不支持 Windows 原生路径权限。
2. **数据库要求**：必须使用 **PostgreSQL 16+**（需具备 `pgcrypto` 扩展支持 `gen_random_uuid()`）。
3. **Python 环境**：Python >= 3.11 及 [`uv`](https://github.com/astral-sh/uv)。

---

## 文档导航

- [系统架构全景文档](docs/architecture.md)：模块关系、数据流图与并发模型。
- [读者剧场验收矩阵](docs/reader-panel-verification.md)：AC-01 至 AC-14 验收标准与自动化证据映射。
- [后端与数据库配置说明](backend/README.md)：PostgreSQL 配置、工作区权限与提供商参数。
- [贡献指南与质量门禁](CONTRIBUTING.md)：分支管理规范与双审查 Agent 审计流程。

---

## 开源协议

本项目采用 GNU 通用公共许可证 v3.0 ([GPL-3.0](LICENSE))。

---
---

# GuraNovel (English)

**GuraNovel** is a robust, modular, and AI-assisted long-form novel creation and review platform. It combines **LangGraph state graph orchestration** and the **Reader Panel multi-agent collaborative critique system** with strict version control, transactional isolation, fail-closed redaction, and deterministic lifecycle guarantees.

---

## Key Features

- **Concept Selection & Workspace Isolation**: Multi-concept ideation, structured parameter configuration, and POSIX `0700` filesystem security boundaries.
- **Setting Evolution & Maintenance**: Consistency analysis, impact assessment, and coordinated revision confirmation for characters, world lore, and plot outlines.
- **Chapter Production V2 (LangGraph Orchestration)**: An 11-node state graph architecture with transactional boundary isolation, monotonic event sequencing (`event_sequence`), claim-token ABA protection, and ephemeral state crash recovery.
- **Multi-Stage Review & Revision Saga**: Deterministic chapter reviews (structure, style, world lore) with separate paths for non-blocking warnings (`accept_warning`), author feedback loops, manual edits, and blocking revisions (`request_revision`).
- **Simulated Reader Panel (v0.10.0 MVP)**:
  - **Persona Diversity**: 6 reader personas (`general_immersive`, `low_patience`, `genre_experienced`, `character_emotion`, `style_sensitive`, `newcomer`) and 3 Moderator modes.
  - **Cold-Reading Isolation**: Readers evaluate manuscript segments independently without exposure to peer identities or outputs.
  - **Normalized Issue Extraction & Blind Ballots**: Anonymous issue extraction with blind initial ballots masking originators.
  - **Bounded Discussion Turns**: Issue-scoped, segment-bound multi-round dialogue with code-owned termination.
  - **Minority-Risk Preservation**: Independent high-risk retention alongside separate raw and target-audience voting distributions.
  - **Editor Handoff & Non-Mutation Guarantee**: Read-only diagnostic reports for author/editor decisions without ever modifying chapter manuscripts.
- **Fail-Closed Privacy & Security Boundaries**: Content-free error handling, strict UUID normalization, calendar-validated ISO-8601 timestamps, SHA-256 content hashes, and zero leakage of raw prompts, novel prose, or credentials.

---

## System Architecture

```mermaid
graph TD
    UI[Frontend Workbench<br/>React 19 + TypeScript + Vite] -->|Fail-Closed REST API| API[FastAPI HTTP Layer]
    API --> SVC[Domain Services Layer]
    SVC --> LG[LangGraph Orchestrator<br/>11-Node State Graph]
    SVC --> RP[Reader Panel Engine<br/>Multi-Agent Focus Group]
    LG --> DB[(PostgreSQL 16<br/>SQLAlchemy + Asyncpg)]
    RP --> DB
    SVC --> FS[Project Workspaces<br/>POSIX 0700 Directories]
    LG --> LLM[AI Provider Boundary<br/>Fake / OpenAI-Compatible]
    RP --> LLM
```

---

## Deployment & Quickstart

GuraNovel provides three deployment options. We recommend using **One-Click Docker Compose** or the **Local Startup Script**.

### Option 1: One-Click Full-Stack Docker Compose (Recommended)

Ideal for running PostgreSQL, the FastAPI backend, and the Nginx frontend in unified containers:

```bash
# 1. Clone the repository
git clone https://github.com/WastonGura/GuraNovel.git
cd GuraNovel

# 2. Launch the full stack (Database + Backend + Frontend)
docker compose up -d --build
```

- **Frontend Workbench**: Open [http://localhost:5173](http://localhost:5173) in your browser.
- **Backend API Docs**: Open [http://localhost:8000/docs](http://localhost:8000/docs).
- **Stop Services**: Run `docker compose down`.

---

### Option 2: One-Click Local Startup Script (Development)

Ideal for local development with Python 3.11+ and Node.js 20+:

```bash
# Make executable and run the startup script
./scripts/start-local.sh
```

This script automatically:
1. Starts the PostgreSQL 16 container via Docker (or verifies local port 5432);
2. Syncs Python dependencies and applies Alembic migrations (`alembic upgrade head`);
3. Installs frontend dependencies and launches FastAPI (`8000`) and Vite dev server (`5173`) concurrently.

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

#### 2. Configure and Start Backend
```bash
cd backend
cp .env.example .env

# Edit .env with your production database credentials and workspace path
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

## Verification & Testing

### Backend Verification
```bash
cd backend

# Linting and static analysis
uv run ruff check .

# Fast non-integration unit tests (1800+ tests)
uv run pytest -m "not integration"

# Alembic migration upgrade/downgrade verification
uv run pytest tests/test_alembic.py

# PostgreSQL integration suite
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

# Design token and guideline linter
npx --no-install @google/design.md lint DESIGN.md

# Vitest component and client unit tests
npm run test -- --run

# Playwright browser end-to-end tests
npm run test:e2e
```

---

## System Constraints

1. **POSIX Filesystem Requirement**: Project workspaces enforce strict POSIX `0700` permissions. The backend **must run on Linux / macOS / WSL**.
2. **Database Version**: Requires **PostgreSQL 16+** with `pgcrypto` (`gen_random_uuid()`).
3. **Python Version**: Python >= 3.11 with [`uv`](https://github.com/astral-sh/uv).

---

## Documentation Links

- [System Architecture](docs/architecture.md): Detailed component relationships, data flows, and concurrency models.
- [Reader Panel Verification Matrix](docs/reader-panel-verification.md): Acceptance criteria mapping (AC-01 through AC-14) and automated test evidence.
- [Backend & Database Setup](backend/README.md): Local PostgreSQL setup, project workspaces, and database migrations.
- [Contributing Guidelines](CONTRIBUTING.md): Code review policies, release verification gates, and repository conventions.

---

## License

This project is licensed under the GNU General Public License v3.0 ([GPL-3.0](LICENSE)).
