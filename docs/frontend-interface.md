# 前端入口与旧链接兼容

当前应用只保留书架、Studio 创作区和独立设定集界面。旧表单工作台的页面组件、导航和按钮已移除；后端工作流、文档版本、API 和已有数据继续保留。

| 正式入口 | 用途 |
|---|---|
| `/`、`/?project=:projectId` | 书架与小说详情 |
| `/projects/:projectId/studio/:chapterId?` | 五阶段创作区 |
| `/setting-collections/:settingCollectionId` | 独立设定集 |
| `/preview/studio` | 相同 Studio 界面的示例模式 |

旧项目链接使用 replace 跳到书架并打开对应详情；旧章节链接跳到同一小说和章节的 Studio。旧读者链接携带 document、version 和 session 到 Studio，服务端返回的身份必须与链接一致。旧正文版本与当前版本不一致时显示提示，不把当前报告冒充旧报告；用户可打开当前章节继续。

旧构思和项目维护链接回到对应小说详情，并说明目前没有新版操作入口。它们不会启动、批准或取消后端任务。

章节任务列表中保留的旧版 `awaiting_approval`、`completed`、`rejected` 状态仅用于识别历史任务。新版可只读查看已有大纲和正文，不会把旧任务当成 V2 任务操作；正文修改、还原和审阅按钮受限，并说明任务操作尚待迁移。其他未知状态仍按无效响应处理。

## 尚待接入的能力

初始大纲生成和作者编辑由 [#299](https://github.com/WastonGura/GuraNovel/issues/299) 跟进。构思与项目维护的新版操作入口尚未提供；已有任务和历史数据仍可通过后端 API 读取。文档版本恢复仍使用现有服务，设定历史入口由 [#311](https://github.com/WastonGura/GuraNovel/issues/311) 跟进。

前端测试以新的路由、正文保存、审阅和读者接口为准；移除旧页面专属测试，保留 API 契约测试，并覆盖旧链接重定向、浏览器后退和版本/会话不匹配。

本文件描述源代码变更。已有 Docker 镜像需要从修改后的 checkout 重新构建，不能把本地源码调整视为镜像已经更新。
