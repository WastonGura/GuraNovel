"""Studio Assistant Agent and Deterministic Assistant Provider for GuraNovel."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.studio_assistant_tools import execute_assistant_tool


class StudioAssistantAgent:
    """Agent that coordinates Gura assistant interactions and bounded tool calls."""

    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self.session = session
        self.project_id = project_id

    async def generate_reply(
        self,
        user_message: str,
        chapter_id: UUID | None = None,
        current_view: str | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """Process user message, invoke bounded read-only tools if needed, and produce response.

        Returns (reply_content, tool_calls, tool_results).
        """
        query = user_message.strip()
        lower_query = query.lower()

        # 1. Check for unauthorized mutating / approval / deletion requests
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
            if keyword in lower_query and ("帮我" in lower_query or "替我" in lower_query or "直接" in lower_query or "自动" in lower_query):
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
                    f"鲨鲨提醒：Gura 助手拥有受限的只读业务与指导权限，**不能**替你执行「{action_name}」等写入或决策操作。\n\n"
                    f"你可以根据界面指引自行在相应面板中操作确认（例如在操作栏中点击对应按钮）。如果你需要查看具体操作方法，可以问我「如何{keyword}」哦！"
                )
                return reply, [tool_call], [tool_res]

        # 2. Check for review / warning queries
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

        # 3. Check for outline queries
        if any(k in lower_query for k in ["大纲", "思路", "剧情走向", "outline"]):
            if any(k in lower_query for k in ["怎么用", "如何", "操作", "步骤"]):
                topic = "outline"
                tool_call = {
                    "id": "call_guidance_outline",
                    "name": "get_software_guidance",
                    "arguments": {"topic": topic},
                }
                res = await execute_assistant_tool(
                    self.session, self.project_id, "get_software_guidance", {"topic": topic}
                )
                tool_res = {"tool_call_id": "call_guidance_outline", "name": "get_software_guidance", "result": res}
                reply = f"这里是大纲操作方法：\n\n{res['guidance']}"
                return reply, [tool_call], [tool_res]
            else:
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

        # 4. Check for draft / prose / word count queries
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

        # 5. Check for restore point queries
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

        # 6. Check for software guidance queries (reader, finalize, general)
        guidance_map = {
            "读者": "reader",
            "读者会": "reader",
            "画像": "reader",
            "定稿": "finalize",
            "发布": "finalize",
            "怎么用": "general",
            "怎么操作": "general",
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

        # 7. Check for project summary queries
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

        # 8. Default friendly shark assistant reply
        reply = (
            "你好！我是 Gura 创作助手 🦈。\n\n"
            "当前正在工程中陪伴你的写作。你可以向我询问：\n"
            "- 软件操作方法（如「怎么用大纲」、「如何处理审阅警告」、「读者会有哪些画像」、「如何本地定稿」）\n"
            "- 当前章节的大纲、草稿字数、审阅报告或历史还原点\n"
            "- 写作思路与情节讨论\n\n"
            "请问你想了解关于什么的帮助？"
        )
        return reply, [], []
