"""Studio Assistant Agent and Deterministic Assistant Provider for GuraNovel."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.studio_assistant_tools import execute_assistant_tool

VIEW_BUTTON_GUIDES: dict[str, str] = {
    "outline": (
        "【大纲构思阶段（Outline）按钮与操作指南】\n"
        "- 「生成大纲方案」：根据你输入的故事想法（Idea），调用 Agent 生成 3 组不同剧情走向的候选大纲。\n"
        "- 「换一组」：重新刷新当前候选大纲的排列与思路推荐。\n"
        "- 「选择这个方向」：选中满意的大纲方案，锁定为本章正式大纲，并自动解锁正文写作。\n"
        "- 「确认大纲并进入正文」：确认大纲编辑态内容，保存后切换至正文草稿（Draft）编写。\n"
        "- 「保存大纲反馈」：在大纲编辑态下保留你的修改意见备注。"
    ),
    "draft": (
        "【正文创作阶段（Draft）按钮与操作指南】\n"
        "- 「创建还原点」（底部操作栏）：永久保存当前正文与批注的还原点快照。\n"
        "- 「提交审阅」（底部操作栏）：正文完成后提交责任编辑、主编和设定编辑审查。\n"
        "- 「划词批注」（正文选中文字时）：在所选文本上标记并添加具体修改意见。\n"
        "- 「发送写作要求」（右侧面板）：提交填写的本章整体写作指导要求与已保存批注。\n"
        "- 「返回当前正文」（查看历史还原点时）：退出快照预览，回到最新正文草稿。\n"
        "- 「从此存档继续写作」（查看历史还原点时）：将历史还原点正文与批注回滚恢复为当前最新草稿。"
    ),
    "review": (
        "【编辑审阅阶段（Review）按钮与操作指南】\n"
        "- 「修改所选问题 / 修改所选 N 项并重新审阅」：勾选审查报告中的阻塞项或警告，让 RevisionAgent 针对性修改正文。\n"
        "- 「接受当前警告并继续审阅」：认可编辑提示的警告并选择按当前正文继续向下推进。\n"
        "- 「进入读者环节」：审阅通过或无阻塞后，推进至 6 位多偏好读者研读环节。\n"
        "- 「显示大纲 / 显示要求或审阅」（灯泡按钮）：在右侧面板切换查看大纲参考或审阅报告详情。"
    ),
    "reader": (
        "【读者会阶段（Reader）按钮与操作指南】\n"
        "- 「邀请 / 取消」：勾选或取消邀请 1 至 6 位不同画像的读者（剧情党、角色党、设定党、情感党、文字党、休闲读者）。\n"
        "- 「开始阅读」：选定读者后开启研读室，读者将给出第一印象报告并在讨论室交流。\n"
        "- 「跳过读者环节 / 结束旁观，进入终稿」：结束读者反馈讨论，进入最终定稿阶段。"
    ),
    "final": (
        "【本地定稿阶段（Final）按钮与操作指南】\n"
        "- 「本地定稿」：将本章当前版本固化锁定为只读终稿（COMPLETED）。注意：定稿操作仅在本地工程生效，绝不会对外发布。"
    ),
    "detail": (
        "【小说详情页面（Detail）指南】\n"
        "- 展示当前小说的封面、简介、总字数、总章节数及题材标签。"
    ),
    "setting": (
        "【小说设置页面（Setting）指南】\n"
        "- 调整当前小说的基本设置、世界观背景和偏好选项。"
    ),
    "dashboard": (
        "【作品工程工作台按钮指南】\n"
        "- 「新建章节」：在当前作品中创建新的一章。\n"
        "- 「开始构思」：启动作品初始概念方案生成与网格选择。\n"
        "- 「项目维护」：对整个作品的历史章节与世界观进行全局一致性维护。"
    ),
    "projectworkspace": (
        "【作品工程工作台按钮指南】\n"
        "- 「新建章节」：在当前作品中创建新的一章。\n"
        "- 「开始构思」：启动作品初始概念方案生成与网格选择。\n"
        "- 「项目维护」：对整个作品的历史章节与世界观进行全局一致性维护。"
    ),
    "chapterworkspace": (
        "【章节工作台按钮指南】\n"
        "- 包含本章的阶段导航（大纲、草稿、审阅、读者会、定稿）及正文工作区。"
    ),
}


class StudioAssistantAgent:
    """Agent that coordinates Gura assistant interactions and bounded tool calls."""

    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self.session = session
        self.project_id = project_id

    async def _try_real_llm_reply(
        self,
        user_message: str,
        chapter_id: UUID | None,
        current_view: str | None,
        history: list[dict[str, str]] | None,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]] | None:
        """Call configured OpenAI-compatible model if available, returning (reply, calls, results)."""
        from app.core.config import settings
        import httpx

        base_url = settings.openai_compatible_base_url
        api_key_secret = settings.openai_compatible_api_key
        api_key = (
            api_key_secret.get_secret_value()
            if hasattr(api_key_secret, "get_secret_value")
            else str(api_key_secret) if api_key_secret else None
        )
        model = settings.openai_compatible_model
        if not base_url or not api_key or not model:
            return None

        norm_view = (current_view or "Dashboard").strip()
        view_guide = VIEW_BUTTON_GUIDES.get(norm_view.lower(), "")
        system_prompt = (
            f"你是 Gura 创作助手（鲨鲨 🦈），专为小说作者提供写作指导、界面指引与协助。\n"
            f"当前工程 ID: {self.project_id}\n"
            f"用户所在界面视图为: {norm_view}。\n"
        )
        if view_guide:
            system_prompt += f"\n当前视图的按钮与操作说明如下：\n{view_guide}\n"

        system_prompt += (
            "\n【业务权限与安全原则】\n"
            "1. 你拥有安全的业务指导与有界操作能力：你可以提供操作方法指导，也可以在用户明确要求时协助创建新章节、跳转导航或发起审阅。\n"
            "2. 敏感决策防呆保护：你绝对不能替用户修改正文内容、替用户写正文、批准大纲、删除章节或执行本地定稿发布。若用户提出这类敏感修改要求，请礼貌拒绝并指导用户在界面上自主操作。\n"
            "3. 行文风格亲切幽默，以鲨鲨口吻（可带 🦈 表情）耐心地协助创作者。\n"
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            for item in history[-8:]:
                if item.get("role") in ("user", "assistant") and item.get("content"):
                    messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": user_message})

        try:
            timeout = settings.openai_compatible_timeout_seconds or 25.0
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                resp = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": 0.7,
                    },
                )
                if not resp.is_success:
                    if resp.status_code == 401:
                        category = "鉴权失败（API Key 无效或未授权）"
                    elif resp.status_code == 403:
                        category = "访问受限（无权限访问该模型资源）"
                    elif resp.status_code == 429:
                        category = "调用受限（速率限制或配额不足）"
                    elif resp.status_code >= 500:
                        category = "上游服务暂时不可用"
                    else:
                        category = "服务响应异常"
                    reply = (
                        f"⚠️ 真实模型服务调用失败（HTTP {resp.status_code}：{category}）。\n\n"
                        f"已为你切换至本地离线模式。你可以继续询问界面操作指南或使用快捷业务协助。"
                    )
                    return reply, [], []
                data = resp.json()
                reply = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if not reply:
                    reply = "⚠️ 真实模型返回了空内容。已为你切换至本地离线模式。"
                    return reply, [], []
                return reply, [], []
        except Exception as exc:
            if isinstance(exc, httpx.TimeoutException):
                category = "请求超时（上游响应超时）"
            elif isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
                category = "网络连接失败（无法连接到模型网关）"
            else:
                category = f"网络或系统异常（{type(exc).__name__}）"
            reply = (
                f"⚠️ 真实模型网络调用异常（{category}）。\n\n"
                f"已为你切换至本地离线模式。你可以继续询问界面操作指南或使用快捷业务协助。"
            )
            return reply, [], []

    async def generate_reply(
        self,
        user_message: str,
        chapter_id: UUID | None = None,
        current_view: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """Process user message, invoke bounded tools if needed, and produce response.

        Returns (reply_content, tool_calls, tool_results).
        """
        query = user_message.strip()
        lower_query = query.lower()

        # 1. Check for unauthorized mutating / approval / deletion requests (Safety Guard)
        forbidden_actions = [
            ("定稿", "本地定稿"),
            ("发布", "本地定稿"),
            ("批准大纲", "大纲批准"),
            ("通过大纲", "大纲批准"),
            ("接受警告", "接受警告"),
            ("删除", "删除操作"),
            ("修改正文", "正文编辑"),
            ("替我写", "正文创作"),
            ("替我改", "正文修改"),
        ]
        for keyword, action_name in forbidden_actions:
            if keyword in lower_query and (
                "帮我" in lower_query or "替我" in lower_query or "直接" in lower_query or "自动" in lower_query
            ):
                tool_call = {
                    "id": "call_guidance_perms",
                    "name": "get_software_guidance",
                    "arguments": {"topic": "permissions"},
                }
                tool_res = {
                    "tool_call_id": "call_guidance_perms",
                    "name": "get_software_guidance",
                    "result": await execute_assistant_tool(
                        self.session, self.project_id, "get_software_guidance", {"topic": "permissions"}
                    ),
                }
                reply = (
                    f"鲨鲨提醒：Gura 助手拥有受限的只读业务与安全指导权限，**不能**替你执行「{action_name}」等写入或决策操作。\n\n"
                    f"你可以根据界面指引自行在相应面板中操作确认（例如在操作栏中点击对应按钮）。如果你需要查看具体操作方法，可以问我「如何{keyword}」哦！"
                )
                return reply, [tool_call], [tool_res]

        # Intent classification helpers
        def _is_negative(text: str) -> bool:
            neg_markers = ["不要", "别", "取消", "不用", "无需", "免了", "不需要", "请勿", "切勿", "别建", "别加", "先不", "暂停", "不提交", "不创建", "不审阅", "不修改"]
            return any(neg in text for neg in neg_markers)

        def _is_inquiry(text: str) -> bool:
            inq_markers = ["怎么", "如何", "怎样", "什么是", "怎么做", "如何做", "怎样做", "教程", "指引", "步骤", "说明"]
            if any(inq in text for inq in inq_markers):
                return True
            if ("?" in text or "？" in text) and not any(imp in text for imp in ["帮我", "请帮我", "立即", "马上", "现在就"]):
                return True
            return False

        # 2. Check for bounded action: create chapter
        create_markers = [
            "创建一章", "新建章节", "创建新的一章", "帮我建一章", "建一章",
            "加一章", "增加章节", "新建一章", "创建章节", "帮我创建章节",
        ]
        if any(k in lower_query for k in create_markers):
            if _is_negative(lower_query):
                reply = "🦈 好的，收到！已为你取消，不会创建新章节。如果你需要了解如何创建章节或有其他构思需求，随时告诉我哦！"
                return reply, [], []

            if _is_inquiry(lower_query):
                tool_call = {
                    "id": "call_guidance_create_chapter",
                    "name": "get_software_guidance",
                    "arguments": {"topic": "outline"},
                }
                res = await execute_assistant_tool(
                    self.session, self.project_id, "get_software_guidance", {"topic": "outline"}
                )
                tool_res = {"tool_call_id": "call_guidance_create_chapter", "name": "get_software_guidance", "result": res}
                reply = (
                    "🦈 新建章节的方法如下：\n\n"
                    "1. **界面操作**：在「作品看板」或章节目录面板中，点击「+ 新建章节」按钮即可快速创建。\n"
                    "2. **快捷协助**：你也可以直接对我下达明确指令，例如「帮我新建一章名为第一章」，我就会立刻为你创建好并提供跳转链接！"
                )
                return reply, [tool_call], [tool_res]

            title_arg = None
            for marker in ["名为", "标题为", "叫"]:
                if marker in query:
                    parts = query.split(marker, 1)
                    if len(parts) > 1 and parts[1].strip():
                        title_arg = parts[1].strip("。！？，\n\"'“” ")
                        break

            tool_call = {
                "id": "call_create_chapter",
                "name": "create_chapter",
                "arguments": {"title": title_arg} if title_arg else {},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "create_chapter", tool_call["arguments"]
            )
            tool_res = {"tool_call_id": "call_create_chapter", "name": "create_chapter", "result": res}
            reply = (
                f"🦈 好的！已为你成功创建新章节【第 {res['chapter_number']} 章】「{res.get('title') or ''}」！\n\n"
                f"你可以点击下方快捷链接前往该章节开始大纲构思或正文创作：\n"
                f"👉 [前往第 {res['chapter_number']} 章]({res['target_url']})"
            )
            return reply, [tool_call], [tool_res]

        # 3. Check for bounded action: navigate view
        nav_keywords = ["跳到", "跳转", "切换到", "进入", "去大纲", "去草稿", "去正文", "去审阅", "去读者会", "去定稿", "去终稿", "返回看板", "回到看板", "返回目录"]
        if any(k in lower_query for k in nav_keywords) and any(
            v in lower_query for v in ["大纲", "草稿", "正文", "审阅", "读者", "定稿", "终稿", "看板", "目录", "主页"]
        ):
            target_view = "dashboard"
            if "大纲" in lower_query:
                target_view = "outline"
            elif "审阅" in lower_query:
                target_view = "review"
            elif "读者" in lower_query:
                target_view = "reader"
            elif "定稿" in lower_query or "终稿" in lower_query:
                target_view = "final"
            elif "草稿" in lower_query or "正文" in lower_query:
                target_view = "draft"

            tool_call = {
                "id": "call_navigate_view",
                "name": "navigate_view",
                "arguments": {"view": target_view, "chapter_id": str(chapter_id) if chapter_id else None},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "navigate_view", tool_call["arguments"], chapter_id
            )
            tool_res = {"tool_call_id": "call_navigate_view", "name": "navigate_view", "result": res}
            reply = (
                f"🦈 好的，正在为你准备前往「{res['label']}」界面：\n"
                f"👉 [点击跳转至 {res['label']}]({res['target_url']})"
            )
            return reply, [tool_call], [tool_res]

        # 4. Check for bounded action: trigger chapter review
        review_markers = ["提交审阅", "开始审阅", "帮我审阅", "发起审阅", "去审阅", "审阅这一章", "审阅本章"]
        if any(k in lower_query for k in review_markers) or ("审阅" in lower_query and ("提交" in lower_query or "发起" in lower_query)):
            if _is_negative(lower_query):
                reply = "🦈 好的，收到！我不会为你提交审阅本章。你可以继续在草稿（Draft）中润色修改，准备就绪后再提交。"
                return reply, [], []

            if _is_inquiry(lower_query):
                tool_call = {
                    "id": "call_guidance_review",
                    "name": "get_software_guidance",
                    "arguments": {"topic": "review"},
                }
                res = await execute_assistant_tool(
                    self.session, self.project_id, "get_software_guidance", {"topic": "review"}
                )
                tool_res = {"tool_call_id": "call_guidance_review", "name": "get_software_guidance", "result": res}
                reply = (
                    "🦈 提交审阅的操作方法如下：\n\n"
                    "1. **界面操作**：在「正文草稿（Draft）」完成写作后，点击右上角的「提交审阅」按钮即可启动三级编辑审阅流程。\n"
                    "2. **快捷协助**：你也可以直接对我下达明确指令「帮我提交审阅这一章」，我将为你启动审阅流程并输出跳转链接！"
                )
                return reply, [tool_call], [tool_res]

            tool_call = {
                "id": "call_trigger_review",
                "name": "trigger_chapter_review",
                "arguments": {"chapter_id": str(chapter_id)} if chapter_id else {},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "trigger_chapter_review", tool_call["arguments"], chapter_id
            )
            tool_res = {"tool_call_id": "call_trigger_review", "name": "trigger_chapter_review", "result": res}
            reply = res["message"]
            return reply, [tool_call], [tool_res]

        # 5. Check for bounded action: trigger feedback revision
        revision_markers = ["根据反馈修改", "根据反馈修订", "修改审阅问题", "帮我修改草稿", "修改选中的问题", "执行修改", "发起修订"]
        if any(k in lower_query for k in revision_markers) or ("修订" in lower_query and ("反馈" in lower_query or "审阅" in lower_query)):
            if _is_negative(lower_query):
                reply = "🦈 好的，收到！我不会对当前草稿发起修订。你可以自行在正文编辑器中进行调整。"
                return reply, [], []

            if _is_inquiry(lower_query):
                reply = (
                    "🦈 反馈修订的操作方法如下：\n\n"
                    "1. **界面操作**：在草稿（Draft）视图右侧的审阅批注栏中，勾选需要处理的问题，点击「修改选中的问题」发起自动修订。\n"
                    "2. **快捷协助**：你也可以直接对我下达明确指令「帮我根据反馈修订」，我将协助你发起自动修订流程！"
                )
                return reply, [], []

            # Extract user feedback instruction from query if provided
            user_feedback = user_message
            for marker in revision_markers:
                if marker in user_feedback:
                    parts = user_feedback.split(marker, 1)
                    after = parts[1].lstrip("：:，, \t\n")
                    if after:
                        user_feedback = after
                    break

            arguments: dict[str, Any] = {"chapter_id": str(chapter_id)} if chapter_id else {}
            if user_feedback and user_feedback != user_message:
                arguments["feedback"] = user_feedback
            elif user_feedback:
                arguments["feedback"] = user_feedback

            tool_call = {
                "id": "call_trigger_feedback_revision",
                "name": "trigger_feedback_revision",
                "arguments": arguments,
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "trigger_feedback_revision", tool_call["arguments"], chapter_id
            )
            tool_res = {"tool_call_id": "call_trigger_feedback_revision", "name": "trigger_feedback_revision", "result": res}
            reply = res["message"]
            return reply, [tool_call], [tool_res]

        # 5. Check for button / view-specific guidance (Context Grounding on current_view)
        button_keywords = [
            "按钮", "这个按钮", "按钮有什么用", "按钮是干什么的", "界面怎么用",
            "当前界面", "当前页面", "怎么操作", "如何操作", "现在做什么", "下一步",
            "页面怎么用", "操作说明", "功能说明", "怎么用", "如何使用",
        ]
        if any(k in lower_query for k in button_keywords):
            clean_view = (current_view or "").strip().lower()
            topic = "general"
            if "outline" in clean_view or "大纲" in lower_query:
                topic = "outline"
            elif "draft" in clean_view or "草稿" in lower_query or "正文" in lower_query:
                topic = "draft"
            elif "review" in clean_view or "审阅" in lower_query:
                topic = "review"
            elif "reader" in clean_view or "读者" in lower_query:
                topic = "reader"
            elif "final" in clean_view or "定稿" in lower_query:
                topic = "finalize"

            tool_call = {
                "id": "call_guidance_buttons",
                "name": "get_software_guidance",
                "arguments": {"topic": topic},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "get_software_guidance", {"topic": topic}
            )
            tool_res = {"tool_call_id": "call_guidance_buttons", "name": "get_software_guidance", "result": res}

            guide_text = VIEW_BUTTON_GUIDES.get(clean_view) or VIEW_BUTTON_GUIDES.get(topic) or res.get("guidance", "")
            view_display = current_view or "当前"
            reply = (
                f"🦈 鲨鲨为你找到了「{view_display}」界面的主要按钮与操作说明：\n\n"
                f"{guide_text}\n\n"
                f"如果有不清楚的具体操作，随时告诉我哦！"
            )
            return reply, [tool_call], [tool_res]

        # 5.5 Check for feedback revision queries
        if any(k in lower_query for k in ["修订", "修改问题", "根据反馈修改", "处理建议", "修复问题", "修稿"]):
            tool_call = {
                "id": "call_trigger_feedback_revision",
                "name": "trigger_feedback_revision",
                "arguments": {"chapter_id": str(chapter_id)} if chapter_id else {},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "trigger_feedback_revision", tool_call["arguments"], chapter_id
            )
            tool_res = {
                "tool_call_id": "call_trigger_feedback_revision",
                "name": "trigger_feedback_revision",
                "result": res,
            }
            return res["message"], [tool_call], [tool_res]

        # 6. Check for review / warning queries
        if any(k in lower_query for k in ["审阅", "警告", "修改建议", "阻塞", "报错", "问题", "review", "warning"]):
            tool_call = {
                "id": "call_review_reports",
                "name": "get_chapter_review_reports",
                "arguments": {"chapter_id": str(chapter_id)} if chapter_id else {},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "get_chapter_review_reports", tool_call["arguments"], chapter_id
            )
            tool_res = {
                "tool_call_id": "call_review_reports",
                "name": "get_chapter_review_reports",
                "result": res,
            }
            reports = res.get("reports", [])
            if not reports:
                reply = "当前章节还没有提交过审阅哦。点击界面上的「提交审阅」按钮，责任编辑、主编和设定编辑就会依次为你进行审查！"
            else:
                lines = [f"本章最新审阅状态（共 {len(reports)} 条报告）："]
                for r in reports:
                    status_str = "✅ 通过" if r.get("passed") else "⚠️ 未通过/有警告"
                    lines.append(f"- **{r.get('reviewer_agent_role', '编辑')}** ({r.get('review_mode')}): {status_str} — {r.get('summary')}")
                    if r.get("blocking_issues"):
                        lines.append(f"  - 阻塞项 ({len(r['blocking_issues'])}): " + "；".join(b.get("message", str(b)) for b in r["blocking_issues"]))
                    if r.get("warnings"):
                        lines.append(f"  - 警告项 ({len(r['warnings'])}): " + "；".join(w.get("message", str(w)) for w in r["warnings"]))
                if res.get("has_warnings") and not res.get("has_blocking"):
                    lines.append("\n💡 提示：当前仅存在警告，你可以点击「接受当前警告并继续审阅」，或者勾选具体问题点击「修改选中的问题」让 RevisionAgent 针对性修改。")
                elif res.get("has_blocking"):
                    lines.append("\n🛑 提示：存在阻塞项，需要勾选问题后点击「修改选中的问题」进行修订，通过后方可推进。")
                reply = "\n".join(lines)
            return reply, [tool_call], [tool_res]

        # 7. Check for outline queries
        if any(k in lower_query for k in ["大纲", "思路", "剧情走向", "outline"]):
            tool_call = {
                "id": "call_chapter_outline",
                "name": "get_chapter_outline",
                "arguments": {"chapter_id": str(chapter_id)} if chapter_id else {},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "get_chapter_outline", tool_call["arguments"], chapter_id
            )
            tool_res = {"tool_call_id": "call_chapter_outline", "name": "get_chapter_outline", "result": res}
            status_desc = "已批准锁定" if res.get("is_approved") else "讨论/草拟中（未批准）"
            text_preview = res.get("outline_text") or "（暂无大纲正文）"
            reply = (
                f"【{res.get('title')}】的大纲状态：**{status_desc}**\n\n"
                f"大纲内容如下：\n```markdown\n{text_preview}\n```\n"
                f"在大纲页面确认心仪方案后点击「选择这个方向」即可锁定并开始正文创作！"
            )
            return reply, [tool_call], [tool_res]

        # 8. Check for draft / prose / word count queries
        if any(k in lower_query for k in ["草稿", "正文", "字数", "写了多少", "draft", "prose"]):
            tool_call = {
                "id": "call_chapter_draft",
                "name": "get_chapter_draft_summary",
                "arguments": {"chapter_id": str(chapter_id)} if chapter_id else {},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "get_chapter_draft_summary", tool_call["arguments"], chapter_id
            )
            tool_res = {"tool_call_id": "call_chapter_draft", "name": "get_chapter_draft_summary", "result": res}
            reply = (
                f"【{res.get('title')}】当前草稿情况：\n"
                f"- 字数统计：约 **{res.get('character_count', 0)}** 字\n"
                f"- 章节状态：`{res.get('status')}`\n"
                f"- 正文开头摘录：\n> {res.get('excerpt') or '（暂无草稿内容）'}\n\n"
                f"草稿每两秒会自动在后台保存，你也可以随时在「版本存档」中保存手动还原点。"
            )
            return reply, [tool_call], [tool_res]

        # 9. Check for restore point queries
        if any(k in lower_query for k in ["还原点", "存档", "恢复", "版本", "restore"]):
            tool_call = {
                "id": "call_restore_points",
                "name": "list_chapter_restore_points",
                "arguments": {"chapter_id": str(chapter_id)} if chapter_id else {},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "list_chapter_restore_points", tool_call["arguments"], chapter_id
            )
            tool_res = {"tool_call_id": "call_restore_points", "name": "list_chapter_restore_points", "result": res}
            pts = res.get("restore_points", [])
            if not pts:
                reply = "本章暂无手动还原点。你可以在右上角「版本存档」中输入备注并点击「保存还原点」来永久备份当前正文和批注。"
            else:
                lines = [f"本章共有 {len(pts)} 个手动还原点："]
                for p in pts[:5]:
                    lines.append(f"- **{p['summary']}** ({p['created_at'][:19] if p['created_at'] else ''})")
                reply = "\n".join(lines)
            return reply, [tool_call], [tool_res]

        # 10. Check for guidance queries (reader, finalize)
        guidance_map = {
            "读者": "reader",
            "读者会": "reader",
            "画像": "reader",
            "定稿": "finalize",
            "发布": "finalize",
            "帮助": "general",
            "介绍": "general",
        }
        for kw, topic in guidance_map.items():
            if kw in lower_query:
                tool_call = {
                    "id": f"call_guidance_{topic}",
                    "name": "get_software_guidance",
                    "arguments": {"topic": topic},
                }
                res = await execute_assistant_tool(
                    self.session, self.project_id, "get_software_guidance", {"topic": topic}
                )
                tool_res = {"tool_call_id": f"call_guidance_{topic}", "name": "get_software_guidance", "result": res}
                reply = res["guidance"]
                return reply, [tool_call], [tool_res]

        # 11. Check for project summary queries
        if any(k in lower_query for k in ["项目", "作品", "小说", "总览", "project"]):
            tool_call = {
                "id": "call_project_summary",
                "name": "get_project_summary",
                "arguments": {},
            }
            res = await execute_assistant_tool(
                self.session, self.project_id, "get_project_summary", {}
            )
            tool_res = {"tool_call_id": "call_project_summary", "name": "get_project_summary", "result": res}
            reply = (
                f"当前小说工程【{res.get('title')}】（{res.get('slug')}）：\n"
                f"- 总章节数：{res.get('chapter_count')} 章\n"
                f"- 工程创建时间：{res.get('created_at')[:10] if res.get('created_at') else '未知'}\n\n"
                f"有什么我可以帮你的？你可以询问当前章节大纲、正文字数、审阅报告或操作指南。"
            )
            return reply, [tool_call], [tool_res]

        # 12. Attempt real LLM provider reply if configured
        llm_reply = await self._try_real_llm_reply(
            user_message=user_message,
            chapter_id=chapter_id,
            current_view=current_view,
            history=history,
        )
        if llm_reply is not None:
            return llm_reply

        # 13. Default friendly shark assistant reply
        view_note = f"（当前你在「{current_view}」界面）" if current_view else ""
        reply = (
            f"你好！我是 Gura 创作助手 🦈{view_note}。\n\n"
            "我可以为你提供以下协助：\n"
            "- 软件操作方法（如「这个按钮有什么用」、「怎么用大纲」、「如何处理审阅警告」、「如何定稿」）\n"
            "- 安全业务协助（如「帮我创建新的一章」、「跳到审阅页面」、「发起审阅」）\n"
            "- 当前章节的大纲、草稿字数、审阅报告或历史还原点查询\n\n"
            "请问你想了解什么？"
        )
        return reply, [], []
