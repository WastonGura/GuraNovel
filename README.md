# GuraNovel

[English](README.en.md) · [GitHub Actions](https://github.com/WastonGura/GuraNovel/actions/workflows/ci.yml) · [GPLv3](LICENSE)

GuraNovel 是供个人作者使用的 AI 小说创作工作台。**v0.12.0 的交付范围是桌面个人试写**；发布状态以 [GitHub Releases](https://github.com/WastonGura/GuraNovel/releases) 为准。

## 当前可以做什么

创建作品 → 保存章节思路 → 生成三个大纲方案 → 比较、选择、编辑并确认 → 生成与修订正文 → 审阅 → 可选 Reader → 本地定稿。

- 正式入口为书架、Studio 和独立设定集。旧工作台链接兼容跳转，历史 V1 任务暂时只读。
- 思路与评论保存到服务端，使用修订/版本校验处理冲突；本地副本用于失败恢复。正文变化后无法匹配的评论会明确标记。
- 每批三个大纲保留任务、输入和上下文版本；“换一组”保留历史批次。选中的大纲独立保存，确认绑定准确版本，起草后锁定。
- 设定文档支持直接编辑、软删除和恢复，保留身份与历史。批量操作逐项反馈。
- 正文可编辑、提交修订并经 Editor、Chief Editor、Lore 审阅；可选 Reader 提供只读诊断，不改写正文。本地定稿保存在当前服务中，不会外部发布。

模型仍可能漏改、漏检矛盾、超出长度目标或返回无效结构。多角色审阅不保证正确，作者需要检查内容。两个人工小说样本的实际用量、耗时和失败见[桌面验收记录](docs/acceptance/v0.12.0-desktop.md)。默认 **fake Provider** 只返回确定性示例，用于体验流程，不代表实际模型质量。

设定讨论、新构思/维护界面、旧任务续办、完整历史恢复入口、手机适配、封面/简介修复、分卷、助手统一、导出/EPUB、多人协作和公开部署留待后续版本。未接入的界面能力会禁用或说明限制。见[开发计划](docs/development-plan-2026-10.md)、[前端入口说明](docs/frontend-interface.md)和 [#313](https://github.com/WastonGura/GuraNovel/issues/313)。

## Docker 启动（推荐）

需要 Docker Engine/Desktop 与 Compose。Windows 使用 Docker Desktop 的 Linux 容器；后端工作区依赖 POSIX 权限，原生 Windows 不作为服务部署环境。

```bash
git clone https://github.com/WastonGura/GuraNovel.git
cd GuraNovel
git rev-parse HEAD
docker compose up -d --build --wait
docker compose ps
```

发布后可先切到 `v0.12.0` 标签固定版本；验收时请记录实际提交。默认无需模型密钥，Reader 关闭。打开 [工作台](http://127.0.0.1:5173) 或 [API 文档](http://127.0.0.1:8000/docs)。

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8000/api/v1/health
curl --noproxy '*' -fsS http://127.0.0.1:5173/api/v1/health
```

PowerShell 可用 `curl.exe`。前后端仅绑定 `127.0.0.1`；PostgreSQL 不向宿主机开放端口。端口占用时，在根目录 `.env` 中设置 `GURANOVEL_FRONTEND_PORT` 和 `GURANOVEL_BACKEND_PORT` 后重新启动。这个根目录文件用于 Compose 参数；模型配置位于另一个文件 `backend/.env`。

```bash
docker compose logs --tail=100 backend frontend db
docker compose down
```

`down` 保留数据卷；`down -v` 会永久删除数据库和正文工作区，升级时不要使用。Windows 无法连接 Docker 时，先启动 Docker Desktop 并检查 Linux/WSL Integration；Linux 检查 Docker 服务状态。

## 使用真实模型

先复制 `backend/.env.example` 为 `backend/.env`，填写服务商配置；该文件被 Git 和 Docker 构建上下文忽略。普通 Compose 不会自动转发这个文件，使用随仓库提供的覆盖文件：

```bash
docker compose -f docker-compose.yml -f docker-compose.models.example.yml up -d --build --wait
```

在 `backend/.env` 中设置：

```dotenv
CHAPTER_GENERATION_PROVIDER=openai_compatible
CHAPTER_PRODUCTION_PROVIDER=openai_compatible
READER_PANEL_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://your-provider.example/v1
OPENAI_COMPATIBLE_API_KEY=replace-with-your-private-key
OPENAI_COMPATIBLE_MODEL=your-supported-model
OPENAI_COMPATIBLE_TIMEOUT_SECONDS=120
```

覆盖文件启用 `quick` Reader；不需要时将其 `READER_PANEL_MODE` 改为 `off`。Compose 仍使用容器数据库和工作区，不采用示例 `.env` 的宿主机数据库路径。不要提交实际密钥。

服务商支持时可设置 `OPENAI_COMPATIBLE_REASONING_EFFORT=none|low|medium|high|max`；省略则不发送该参数。抽测使用 `deepseek-flash`、`none` 和 120 秒，不能推断其他模型也有相同延迟或质量。[DeepSeek 思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)说明其参数。长调用遇到连接超时后，应先查看原任务状态，再按原身份恢复；不要直接创建重复生成任务。

## 本地开发与检查

本地后端服务使用 Linux/macOS/WSL、Python 3.11+、uv 和可访问的 PostgreSQL 16；前端使用 Node.js 20+ 与 npm。准备 `backend/.env`，配置可写的 POSIX `WORKSPACE_BASE_DIR` 和本地 `DATABASE_URL`。Compose 默认数据库没有宿主机端口，本地服务需要单独准备数据库。

```bash
cd backend
uv sync --frozen --all-groups
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

另一个终端：

```bash
cd frontend
npm ci
npm run dev
```

开发采用 TDD，新增行为先确认有效 Red，再实现 Green 和整理。已有实现补充的是回归测试。本地只运行改动对应的定向测试；完整门禁由 Actions 执行：Ruff、架构/单元、真实 PostgreSQL 集成与迁移、前端 lint/单元/类型/构建/设计规范、Chromium、版本一致性和 Docker 作者流程。Python 命令使用 `uv run`，安装使用 `uv sync --frozen --all-groups`。

按 issue 分支 → PR → review → Squash merge 交付，不直接推送 main。规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。[开发日志](docs/development-log.md)区分实现、合并和发布；[v0.12.0 发布说明](docs/releases/v0.12.0.md)包含升级、备份与限制。

后端使用 FastAPI、SQLAlchemy、PostgreSQL 和版本化工作区，前端为 React/TypeScript/Vite。数据库和工作区共同构成持久数据，备份与恢复必须配对。许可证为 [GPLv3](LICENSE)。
