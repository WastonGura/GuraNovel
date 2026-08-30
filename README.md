# GuraNovel

<p align="center">
  <b>简体中文</b> | <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml"><img src="https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11%2B-blue.svg" alt="Python"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg" alt="FastAPI"></a>
  <a href="https://react.dev/"><img src="https://img.shields.io/badge/React-19-61DAFB.svg" alt="React"></a>
  <a href="https://github.com/langchain-ai/langgraph"><img src="https://img.shields.io/badge/LangGraph-1.2.11-FF6F00.svg" alt="LangGraph"></a>
</p>

**GuraNovel** 是一款模块化、高可靠的 **AI 辅助长篇小说创作与审阅工作台平台**。

长篇小说创作不同于单篇短文生成，极易出现设定前后矛盾、剧情断层、文风飘忽以及“AI 自嗨”等问题。GuraNovel 专为解决长篇创作的连贯性与深度审阅而设计，将**立项构思**、**世界观演进**、**章节生产流水线（大纲-起草-多维审阅-修改反馈）**与**读者剧场（Reader Panel）多 Agent 模拟评审**深度整合，让创作者能够牢牢把控长篇故事脉络，实现高质量、确定性的人机协同创作。

---

## 完整创作工作流

GuraNovel 覆盖长篇小说从创意立项到多章节连载审阅的全生命周期：

```text
┌─────────────────┐     ┌──────────────────┐     ┌────────────────────────┐     ┌──────────────────────┐
│  1. 立项与概念   │ ──> │  2. 世界观与设定  │ ──> │  3. 章节生产流水线     │ ──> │  4. 读者剧场多维评审  │
│     多方案比选   │     │     一致性维护   │     │  大纲/起草/审阅/修改反馈│     │   6角色模拟诊断/报告 │
└─────────────────┘     └──────────────────┘     └────────────────────────┘     └──────────────────────┘
```

### 1. 立项与概念筛选（Concept Selection & Ideation）
- **多方向并行构思**：根据核心灵感自动拓展不同风格与题材的立项概念方案；
- **多维度对比评估**：从故事吸引力、长篇延展性、设定可行性等维度综合打分对比；
- **独立项目工作区**：选定方案后一键立项，自动在文件系统中创建具备 POSIX 权限隔离的专属项目工作区。

### 2. 世界观与设定演进（World-Building & Lore Maintenance）
- **结构化设定管理**：角色档案、势力阵营、能力体系、世界观规则及全书大纲统一归档；
- **项目维护模式（Project Maintenance）**：长篇连载中发生设定扩充或微调时，系统自动进行**波及范围影响分析**与**一致性协同确认**，确保故事演进不“吃设定”。

### 3. 章节生产流水线（Chapter Production V2）
- **章节大纲规划**：结合前序章节剧情与世界观设定，自动生成章节细纲，支持创作者在线调整确认；
- **正文草稿起草**：严格依据已锁定大纲与设定约束起草正文；
- **多阶段深度审阅**：正文起草后自动触发多维审查（剧情结构、文风节奏、世界观一致性）；
- **人机协同修改循环**：
  - **修改建议反馈**：创作者可针对审阅意见提出具体修改要求；
  - **手动编辑融合**：创作者可随时直接在线修改正文并保存新版本；
  - **非阻塞警告放行（`accept_warning`）**：对轻微风格建议可确认放行；
  - **阻塞项强制修订（`request_revision`）**：针对关键逻辑漏洞触发定向重写与版本迭代。

### 4. 读者剧场多角色模拟评审（Reader Panel MVP）
- **6 种预设模拟读者画像**：
  - `general_immersive`（普通沉浸读者：关注代入感与情绪流）
  - `low_patience`（低耐心读者：关注开篇钩子与节奏拖沓点）
  - `genre_experienced`（资深类型读者：关注老套套路与设定新意）
  - `character_emotion`（角色关注读者：关注角色动机与情感弧光）
  - `style_sensitive`（文风敏感读者：关注语言表达与描写质感）
  - `newcomer`（新手读者：关注信息过载与理解门槛）
- **盲读隔离**：读者 Agent 仅能读取正文切片，完全隔离其他读者身份与评语，避免先入为主；
- **规范化议题提取与盲投**：由主持人 Agent 规范化归纳核心阅读关切，初始投票隐藏提议者信息；
- **有界多轮讨论**：针对争议切片展开深入讨论，由代码统一控制终止轮次与收敛结论；
- **少数派高危风险预警**：独立留存少数派高危警示（`minority_high_risk`），分别呈现全员原始分布与目标受众分布；
- **只读诊断与非篡改原则**：读者剧场仅作为辅助诊断工具，生成单份只读的编辑交接报告供创作者决策，**绝不直接擅自修改小说正文**。

---

## 核心特色与亮点

- 🎯 **长篇一致性保障**：从世界观设定、全书大纲到章节正文版本哈希严格绑定，彻底告别长篇小说“前后打架”、“战力崩溃”。
- ✍️ **创作者牢牢主导（Human-in-the-Loop）**：AI 扮演助手与读者群，关键大纲确认、修改建议输入、版本定稿等关键节点完全由创作者掌控。
- 👥 **真实读者视角诊断**：跳出单一大模型“过度迎合”的泛泛润色，通过多读者画像从阅读快感、节奏、毒点、理解门槛等多角度出具直言不讳的诊断报告。
- 🛡️ **严格的版本快照与防崩溃恢复**：每一次生成、审阅与修改均具备精确的版本哈希与事务快照，支持随时回滚与故障状态单步恢复。

---

## 环境要求与运行约束

在部署与运行 GuraNovel 之前，请确认满足以下环境条件：

1. **操作系统（必须为 POSIX 环境）**：
   - 本系统项目工作区依赖 POSIX `0700` 文件权限隔离机制；
   - **后端必须运行在 Linux / macOS / WSL2 环境**；
   - *Windows 原生路径（如 `D:\...`）不支持权限隔离边界，请在 WSL2 中运行。*
2. **数据库支持**：
   - 必须使用 **PostgreSQL 16+**（需具备 `pgcrypto` 扩展支持 `gen_random_uuid()`）。
3. **语言与工具链（本地原生运行时）**：
   - **Python**：`>= 3.11`，使用 [`uv`](https://github.com/astral-sh/uv) 管理依赖；
   - **Node.js**：`>= 20` 及 `npm`（若在本地运行前端）；
   - **Docker**：Docker & Docker Compose（用于一键容器化部署）。

---

## 快速开始与一键部署

GuraNovel 提供三种部署方式，推荐使用 **Docker Compose 全栈一键启动** 或 **本地一键启动脚本**。

### 方式一：Docker Compose 全栈一键部署（推荐）

适合希望一键拉起数据库、后端与前端完整环境的用户：

```bash
# 1. 克隆代码仓库
git clone https://github.com/WastonGura/GuraNovel.git
cd GuraNovel

# 2. 构建并启动全部服务，等待数据库和后端通过健康检查
docker compose up -d --build --wait
```

首次构建需要下载基础镜像并安装依赖，可能耗时数分钟；后续构建会复用 Docker 缓存。命令成功返回后，可通过以下方式确认服务状态。健康检查显式绕过 `HTTP_PROXY`/`ALL_PROXY`，避免本机代理截获回环请求并返回 `502`：

```bash
docker compose ps
curl --noproxy '*' -fsS http://127.0.0.1:8000/api/v1/health
curl --noproxy '*' -fsS http://127.0.0.1:5173/api/v1/health
```

- **前端工作台**：打开浏览器访问 [http://localhost:5173](http://localhost:5173)
- **后端 API 文档**：访问 [http://localhost:8000/docs](http://localhost:8000/docs)

前后端端口默认仅监听宿主机的 `127.0.0.1`，PostgreSQL 端口不会暴露到宿主机。如 `5173` 或 `8000` 已被占用，可在启动时覆盖公开端口：

```bash
GURANOVEL_FRONTEND_PORT=15173 GURANOVEL_BACKEND_PORT=18000 \
  docker compose up -d --build --wait
```

如确需允许局域网或服务器外部访问，可显式修改监听地址：

```bash
GURANOVEL_BIND_ADDRESS=0.0.0.0 docker compose up -d --build --wait
```

> [!WARNING]
> 监听 `0.0.0.0` 会向外部网络开放前后端端口。服务器部署时应配置防火墙，并通过带 TLS 的反向代理对外提供服务。

查看启动或运行日志：

```bash
docker compose logs --tail=100 backend frontend db
```

停止容器但保留数据库和工作区数据：

```bash
docker compose down
```

> [!WARNING]
> `docker compose down -v` 会永久删除 Compose 管理的 PostgreSQL 数据和项目工作区卷，仅在确实需要清空所有数据时使用。

> [!TIP]
> **Docker 常见问题排查**：
> 若执行出现 `failed to connect to the docker API at unix:///var/run/docker.sock`：
> - **Windows WSL2 用户**：请打开 Windows 上的 **Docker Desktop**，并在 `Settings -> Resources -> WSL Integration` 中勾选开启当前 WSL 发行版。
> - **Linux 原生用户**：请在终端执行 `sudo service docker start` 启动 Docker 服务。
> 若提示 `address already in use`，请停止占用端口的程序，或使用上方的端口覆盖变量重新启动。

---

### 方式二：本地一键极速启动脚本（开发环境推荐）

适合本地已安装 Python 3.11+ 和 Node.js 20+ 的开发者：

```bash
# 赋予执行权限并直接运行一键脚本
./scripts/start-local.sh
```

该脚本将全自动处理以下步骤：
1. 自动启动 PostgreSQL 16 数据库容器（或检测本地 5432 端口）；
2. 自动安装后端 Python 依赖并执行数据库迁移（`alembic upgrade head`）；
3. 自动安装前端 Node 依赖并并行拉起 FastAPI（`8000` 端口）与 Vite 前端热重载服务（`5173` 端口）。

---

### 方式三：生产环境裸机 / 虚拟机部署（零 Docker 原生部署）

适合在专属 Linux 云服务器上进行生产化托管：

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

# 安装依赖并应用迁移
uv sync --frozen --no-dev
uv run alembic upgrade head

# 使用生产进程管理器（如 systemd / supervisor / pm2）启动 uvicorn：
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

## 技术架构与底层实现

```mermaid
graph TD
    UI[前端工作台<br/>React 19 + TypeScript + Vite] -->|Fail-Closed REST API| API[FastAPI HTTP 路由层]
    API --> SVC[领域服务层 Domain Services]
    SVC --> LG[LangGraph 编排引擎<br/>11 节点状态图]
    SVC --> RP[读者剧场引擎 Reader Panel<br/>多角色模拟评测]
    LG --> DB[(PostgreSQL 16<br/>SQLAlchemy + Asyncpg)]
    RP --> DB
    SVC --> FS[项目工作区 Workspaces<br/>POSIX 0700 权限隔离]
    LG --> LLM[模型接入边界<br/>Fake / OpenAI-Compatible]
    RP --> LLM
```

- **后端架构**：
  - **状态图编排引擎**：基于 **LangGraph 1.2.11** 搭建的 11 节点状态图，具备节点级事务边界隔离与单调递增事件序列（`WorkflowEvent.event_sequence`）。
  - **高并发持久化**：采用 **SQLAlchemy 2.0 (Asyncpg)** 异步连接池，严格执行数据版本与哈希一致性校验。
  - **模型接入抽象**：统一支持 Mock/Fake（用于确定性自动化测试）与标准 OpenAI Compatible 协议。
- **前端架构**：
  - **技术栈**：React 19、TypeScript、Vite、React Router 7。
  - **设计系统规范**：严格遵循 WAI-ARIA 无障碍规范并通过 `@google/design.md` 自动化质量校验。
  - **Fail-Closed 数据契约**：前端 API 客户端严格执行数据解码校验，杜绝内部错误堆栈、敏感路径与未经处理的正文泄露。

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

## 文档导航

- [系统架构全景文档](docs/architecture.md)：详细模块关系、数据流图与并发模型。
- [读者剧场验收矩阵](docs/reader-panel-verification.md)：AC-01 至 AC-14 验收标准与自动化证据映射。
- [后端与数据库配置说明](backend/README.md)：PostgreSQL 配置、工作区权限与提供商参数。
- [贡献指南与质量门禁](CONTRIBUTING.md)：分支管理规范与双审查 Agent 审计流程。

---

## 开源协议

本项目采用 GNU 通用公共许可证 v3.0 ([GPL-3.0](LICENSE))。
