# 设定集与小说解耦重构设计

## 1. 决策

设定集是独立、可持续演进的创作资产。一部小说必须关联一个设定集；一个设定集可以被多部小说使用。

小说始终读取所关联设定集的最新内容。初稿、修订、审阅、读者会和维护任务在开始时记录当时实际读取的文档版本，重试和恢复继续使用同一组版本，不在执行中途切换到更新后的设定。

Studio 不再包含独立的 Detail 页面。小说资料在首页的新建小说表单和小说详情弹层中采集、展示，在项目设置中编辑。现有 Setting UI 改造成独立的设定集编辑器，不再作为小说 Studio 的内部页面。

## 2. 当前实现与重构边界

当前实现有四个需要同时处理的耦合点：

1. `Project` 同时承担小说实体和设定文档所有者；`Document.project_id` 非空，所有设定文档都属于某部小说。
2. Agent 直接按 `Document.project_id` 查询世界观、势力、地理、历史和角色等上下文。
3. `StudioSetting` 仅在预览模式使用 `localStorage`，正式模式为只读，尚未接入后端。
4. Dashboard 已有小说详情弹层和新建小说弹层，但 Studio 内仍保留重复的 Detail 页面；新建表单当前只提交 slug、标题、类型和平台。

这次重构保留现有章节生产状态机、`DocumentVersion`、内容哈希、恢复证据、评论、设定图谱和页面动画。不会重新搭建 Studio，也不会更改已完成工作流所绑定的历史版本。

## 3. 核心不变量

- 每部小说只有一个 `setting_collection_id`。
- 一个设定集可以关联零部或多部小说。
- 修改设定集只影响此后新启动的 Agent 任务，不自动改写正文、报告或正在运行的任务。
- 每次设定保存都创建新的 `DocumentVersion`，并使用 `expected_current_version_id` 防止覆盖并发修改。
- Agent 任务保存它读取的全部设定文档 ID、版本 ID 和内容哈希。
- 同一任务的重试、响应丢失恢复和服务重启恢复必须复用原快照。
- 新任务读取设定集当前版本。
- Agent 可以提出设定修改建议；只有用户明确接受后才能写入设定集。
- 正被小说引用的设定集不能硬删除，只能归档或在小说改绑后删除。
- 删除小说不能级联删除共享设定集。

## 4. 数据模型

### 4.1 新增 `setting_collections`

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键 |
| `owner_id` | 所有者，沿用项目权限模型 |
| `slug` | 所有者范围内唯一的稳定路径标识 |
| `title` | 设定集名称 |
| `description` | 简介 |
| `status` | `active` 或 `archived` |
| `workspace_root` | 设定集自己的文件工作区 |
| `revision` | 每次成功写入后递增，仅用于展示和变化检测 |
| `metadata` | 封面等非核心展示数据 |
| `created_at` / `updated_at` | 时间戳 |

### 4.2 修改 `projects`

新增 `setting_collection_id` 外键，删除设定集时使用 `RESTRICT`。迁移完成后设为非空。

小说资料继续由 `Project` 保存：标题、类型、目标平台保留现有字段；简介、封面和标签先保留在已存在的 `metadata` 中，避免为纯展示字段扩大数据库迁移。等字段需要独立索引或约束时再提升为列。

### 4.3 修改 `documents`

新增可空的 `setting_collection_id`，并将 `project_id` 改为可空。数据库约束要求二者有且只有一个非空：

```text
project_id IS NOT NULL XOR setting_collection_id IS NOT NULL
```

同时增加以下约束：

- `chapter_id IS NOT NULL` 时必须同时具有 `project_id`，不能属于设定集。
- 小说文档继续使用 `(project_id, path)` 唯一约束。
- 设定文档使用 `(setting_collection_id, path)` 部分唯一索引。

`DocumentVersion` 不新增第二套版本表。它继续保存不可变内容版本、父版本、来源、Agent、工作流、哈希和快照路径。

### 4.4 文档所有权划分

设定集拥有可复用的世界知识：

- `WORLD_OVERVIEW`
- `POWER_SYSTEM`
- `FACTIONS`
- `GEOGRAPHY`
- `HISTORY`
- `CHARACTER_PROFILE`
- `GLOSSARY`

小说保留故事自身内容：

- `PITCH`、`SYNOPSIS`、`STYLE_GUIDE`、`MAIN_CAST`
- 全书、分卷和章节大纲
- 章节草稿、终稿和摘要
- 伏笔、未解决线索、审阅、读者会和维护产物

现有设定编辑器中的每个条目继续对应一个版本化文档，条目分类保存在 `Document.metadata.category`。图谱边由 `[[标题]]` 引用实时派生，不增加独立边表。

## 5. 后端服务边界

### 5.1 `SettingCollectionService`

负责：

- 创建、读取、更新、归档和列出设定集；
- 列出使用该设定集的小说；
- 校验所有权和归档状态；
- 创建、更新和恢复设定文档；
- 每次成功修改后递增 `setting_collections.revision`。

底层内容保存继续调用 `DocumentService`，不复制文件写入、版本冲突和哈希校验逻辑。

### 5.2 `SettingContextResolver`

所有 Agent 不再自行按 `Document.project_id` 查找设定。统一调用：

```python
resolve_for_project(project_id, allowed_types) -> SettingContextBundle
```

返回内容包括：

- `setting_collection_id`
- `collection_revision`
- 每份文档的 `document_id`
- `version_id`
- `content_hash`
- 类型和经过验证的内容

首批替换范围：

- 项目构思与概念生成；
- 初稿和反馈修订；
- Editor、Chief、Lore 顺序审阅；
- Reader 六类画像与主持人；
- 项目维护和全局 Gura 助手。

### 5.3 Agent 快照规则

任务开始时解析一次最新设定，并把每个文档版本写入现有请求快照或 checkpoint。之后遵循：

| 场景 | 使用的设定 |
| --- | --- |
| 新建任务 | 设定集最新版本 |
| 同一任务重试 | 原任务快照 |
| 响应丢失恢复 | 服务器记录的原任务快照 |
| 服务重启恢复 | checkpoint 中的原任务快照 |
| 用户重新发起一次任务 | 重新读取最新版本 |

恢复校验需要确认快照文档属于任务记录的设定集，但不能因为设定集已经更新而拒绝旧任务，也不能偷偷替换成新版本。

### 5.4 从小说反哺设定集

章节生产和维护 Agent 可以返回 `SettingChangeProposal`，包含目标文档、基准版本、建议内容和理由。界面逐条展示：

- 接受：使用基准版本进行乐观并发写入；
- 已发生变化：要求基于最新版本重新生成或人工合并；
- 拒绝：记录决定，不修改设定集。

章节定稿、审阅通过或 Reader 结束都不能自动写入设定集。

## 6. API 设计

新增：

```text
GET    /setting-collections
POST   /setting-collections
GET    /setting-collections/{id}
PATCH  /setting-collections/{id}
POST   /setting-collections/{id}/archive
GET    /setting-collections/{id}/projects
GET    /setting-collections/{id}/documents
POST   /setting-collections/{id}/documents
```

文档内容、版本列表、写入和恢复继续复用现有 `/documents/{id}/...` 接口。响应中的文档所有权改为显式联合类型：小说文档返回 `project_id`，设定文档返回 `setting_collection_id`。

修改：

```text
POST  /projects
PATCH /projects/{id}
```

创建小说请求增加：

```json
{
  "title": "小说名",
  "slug": "stable-slug",
  "genre": "类型",
  "target_platform": "平台",
  "setting_collection_id": "uuid",
  "metadata": {
    "introduction": "简介",
    "cover": "受控资源引用",
    "labels": ["标签"]
  }
}
```

没有设定集时，新建小说弹层提供“同时创建空白设定集”，后端仍以一次事务生成设定集并绑定小说，保证数据库中不存在无设定集小说。

## 7. 前端信息架构

### 7.1 首页

首页保留现有小说卡片、搜索、动画和详情弹层，增加独立的设定集区域：

- 小说卡片：点击打开现有 `NovelDetails` 弹层；
- 设定集卡片：点击进入独立设定集编辑器；
- 小说详情弹层：显示封面、简介、标签、章节、字数、状态和所用设定集；
- 新建小说弹层：采集同一组小说资料并选择设定集；
- 新建设定集：创建后进入设定编辑器。

所有长列表和表单继续在指定容器内部滚动，首页与弹层外层不产生整页滚动。

### 7.2 路由

目标路由：

```text
/                                      首页
/?project={project_id}                 打开小说详情弹层
/setting-collections/{id}              设定集编辑器
/projects/{project_id}                 小说工作台
/projects/{project_id}/studio          无章节时的创作入口
/projects/{project_id}/studio/{chapter_id}?stage=...
```

兼容跳转：

- 原 `?view=Detail` 跳转到 `/?project={project_id}`；
- 原 `?view=Setting` 跳转到小说绑定的设定集；
- 原 `?view=Create` 保持现有 Studio，并只保留创作阶段导航。

### 7.3 Studio

- 删除 `Detail` surface、简介本地状态和 Detail 页样式。
- 移出 `StudioSetting` surface。
- 顶部设置按钮改为打开当前小说绑定的设定集。
- `Setting / Detail / Create` 页面轮播收敛为单一创作工作区，因此移除小说页面左右箭头。
- 保留 Outline、Draft、Review、Reader、Final 阶段、章节侧边栏、评论、存档、动画和全局助手。

### 7.4 设定集编辑器

将现有 `StudioSetting` 提取为路由页面并接入真实 API：

- 保留条目列表、正文编辑、`[[引用]]`、图谱、搜索、评论和 Agent 讨论 UI；
- 正式模式从后端读取条目，不再固定只读；
- 自动保存使用 `expected_current_version_id`；
- 保存冲突时保留本地草稿，展示重新读取和人工合并入口；
- 顶部显示设定集名称、当前 revision 和正在使用它的小说数量；
- Agent 提案继续逐条接受，未接入的动作不得显示成功。

## 8. 安全迁移

不能把现有设定文档直接从项目改挂到设定集，否则历史工作流中的 `project_id + document_id + version_id` 证据会失效。

迁移步骤：

1. 添加新表、可空外键和双所有权约束，暂不改变读取路径。
2. 为每个现有项目创建一个同名私人设定集。
3. 将现有项目设定文档的当前内容复制为设定集文档 v1；在 metadata 中记录旧文档和版本 ID。
4. 设置 `projects.setting_collection_id`。
5. 旧项目设定文档保留原 ID、版本和文件，只标记 `legacy_setting_context=true`，禁止新写入。
6. 新任务切换到 `SettingContextResolver`；已经开始的任务继续使用原 checkpoint。
7. 确认所有项目完成回填后，将 `projects.setting_collection_id` 改为非空。
8. 保留旧文档用于历史报告、恢复和审计，不在本次重构中删除。

## 9. Issue 拆分与顺序

### #281：设定集模型与无损迁移

- 新增 `setting_collections`；
- 增加 Project 和 Document 外键与约束；
- 回填私人设定集并复制当前设定；
- 保留旧证据。

验收：迁移前后的小说、章节、历史报告和未完成工作流均可读取；删除小说不会删除设定集。

### #282：设定集 CRUD 与版本化文档 API

- 实现 `SettingCollectionService` 和 API；
- 复用 `DocumentService` 的写入、版本、恢复和冲突保护；
- 增加权限与归档规则。

验收：同一设定集可被两部小说引用；并发旧版本写入返回冲突且不覆盖数据。

### #283：统一 Agent 设定上下文与快照

- 实现 `SettingContextResolver`；
- 替换各 Agent 的项目设定查询；
- 将 collection、document、version 和 hash 纳入重试与恢复校验。

验收：设定更新后，新任务读到新内容；旧任务重试仍使用旧快照；不得混用两个 revision。

### #284：独立设定集编辑器

- 提取并复用现有 Setting 组件；
- 接入真实读取、保存、历史和冲突恢复；
- 保留图谱、评论和提案交互。

验收：刷新和历史导航不丢数据；页面整体不滚动；保存失败不显示成功。

### #285：首页与小说创建流程

- 增加设定集区域；
- 扩充新建小说表单并要求绑定设定集；
- 完善现有小说详情弹层；
- 支持同时创建空白设定集。

验收：创建后小说资料和设定集绑定均可重新读取；同名新请求不被错误去重。

### #286：移除 Studio Detail/Setting 页面

- 删除两个 surface 和页面轮播；
- 添加旧 URL 兼容跳转；
- 设置按钮跳转到关联设定集。

验收：旧链接不进入空白页；Create 全流程、侧边栏、存档和助手无回归。

### #287：设定反哺、整版回归与交付记录

- 接入用户确认后的 `SettingChangeProposal`；
- 完成跨小说影响提示；
- 更新交付记录、发布说明和真实模型测试。

验收：Agent 不会未经确认改写设定；真实模型报告明确绑定模型、输入快照和设定 revision。

## 10. 本轮明确不做

- 一部小说绑定多个设定集；
- 设定集分支、合并和自动冲突解决；
- 设定更新后自动重写旧章节；
- 根据正文自动接受设定变化；
- 删除历史项目设定文档。

这些能力只有在真实的系列共创或多人协作需求出现后再扩展。

## 11. 重构交付与验收完成记录

### 11.1 Issue #287 设定反哺与生命周期安全

- **提案数据契约 (`SettingChangeProposal`)**：定义并严格校验 `id`、`setting_collection_id`、`target_document_id`、`base_version_id`、`title`、`category`、`proposed_content`、`reason`、`source_task`（包含 `novel_id`/`novel_title`/`agent_role`/`chapter_id`/`run_id`）与状态流转；
- **乐观并发控制 (OCC) 与冲突防御**：
  - 后端服务与路由 (`POST /api/v1/setting-collections/{id}/proposals/apply`) 强制要求 `expected_current_version_id == base_version_id`；
  - 发生并发修改时返回 `409 Conflict`，绝不静默覆盖最新设定文档；
  - 前端对话交互提示「「{标题}」已发生变化，请重新生成或人工合并。」并保留提案为待处理状态；
- **跨小说影响明确告知**：
  - 当设定集关联多部小说时，提案卡片与工作区明确展示黄色风险提示条：`共享设定变更将影响关联小说后续任务：{关联小说列表}`；
- **零自动静默写入**：
  - 各类 Agent 生成、章节初稿定稿、评审、读者反馈或项目维护阶段，禁止自动将设定建议直接提交或应用到设定集，必须且仅能由作者在工作区中显式点击确认。

### 11.2 自动化回归与工程门禁汇总

- **后端单元测试**：`tests/test_setting_change_proposals.py` 与 `tests/test_setting_context_resolver.py` 等 34 项单测全部通过；
- **后端集成测试**：基于容器化 PostgreSQL 运行 `tests/integration/test_setting_proposal_integration.py`、`test_setting_collection_database_migration.py`、`test_setting_collection_document_mutations.py`、`test_setting_collection_routes.py`、`test_setting_context_snapshots.py` 等 22 项测试全部通过；
- **后端代码规范**：`uv run ruff check .` 0 警告 0 报错；
- **前端单元测试**：Vitest 覆盖 29 个测试套件，共 526 项测试全部通过（包含 `StudioSetting.test.tsx` 25 项、`SettingCollectionWorkspace.test.tsx` 7 项、`client.test.ts` 35 项等）；
- **前端代码规范与构建**：`npm run lint` 检查通过，`npm run build` 打包构建成功；
- **Playwright 端到端测试**：13 项 E2E 真实浏览器测试全部通过，严格维持视口 100dvh 无整页滚动与核心交互链路正常；
- **设定集重构系列 Issue 全部闭环**：#281、#282、#283、#284、#285、#286、#287 顺序落地并完整验证。

