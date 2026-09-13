"""Bounded, strictly read-only business tools for Gura Studio Assistant.

Security & authority guarantees:
1. Strictly read-only queries. Never creates, modifies, deletes, approves, restores, or finalizes any resources.
2. Cross-project isolation: every query is strictly scoped to the authenticated conversation's project_id.
3. Parameter validation: checks chapter and project ownership and validates inputs before querying.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models import Chapter, Project, ReviewReport, StudioRestorePoint
from app.services.document_service import DocumentService


SOFTWARE_GUIDES: dict[str, str] = {
    "outline": (
        "【大纲阶段指南】\n"
        "1. 在输入框中输入你的灵感思路，点击「生成大纲方案」可获取三种不同剧情走向的候选方案。\n"
        "2. 点击「换一组」可以刷新方案排序。\n"
        "3. 选定心仪的大纲后点击「选择这个方向」，系统将独立批准并锁定该大纲版本，随后自动解锁进入正文初稿阶段。"
    ),
    "draft": (
        "【草稿阶段指南】\n"
        "1. 可以在正文区域自由键入小说正文，右侧面板可填写本章总体写作要求。\n"
        "2. 在正文中划词可弹出行内批注圆点，支持选择标记颜色并添加具体修改意见。\n"
        "3. 系统每隔 2 秒会自动执行静默保存，输入时状态栏会显示保存进度。\n"
        "4. 可随时在右上角「版本存档」中创建手动还原点，保存当前正文与批注的快照。"
    ),
    "review": (
        "【审阅阶段指南】\n"
        "1. 点击「提交审阅」后，系统将自动按顺序依次唤起责任编辑（Editor）、主编（Chief Editor）与设定编辑（Lore）三道审阅关卡。\n"
        "2. 审阅完成后会汇总展示审查结果：\n"
        "   - 若无阻碍项且无警告，将直接进入定稿就绪（REVISION_READY）状态；\n"
        "   - 若存在阻塞问题（Block），必须通过修订修复后方能继续；\n"
        "   - 若存在审阅警告（Warning），可选择「接受当前警告并继续审阅」或针对选定问题发起修订。"
    ),
    "warning": (
        "【警告处理指南】\n"
        "1. 审阅警告代表非阻断性的叙事或文笔建议（如情节略有平淡、部分用词稍显重复）。\n"
        "2. 如果认可编辑的警告并希望按当前正文继续推进，可点击审阅面板中的「接受当前警告并继续审阅」。\n"
        "3. 如果希望修改，可以在报告中勾选对应的问题项，点击「修改选中的问题」发起修订。"
    ),
    "restore_point": (
        "【版本存档与还原指南】\n"
        "1. 在右上角「版本存档」面板中输入存档备注，点击「保存还原点」可创建永久存档。\n"
        "2. 还原点不仅记录当时正文的不可变版本，还会完整记录当时的批注与写作要求。\n"
        "3. 在历史还原点列表中点击「恢复此版本」，将安全恢复至该状态，并在恢复前自动建立当前草稿的备份。"
    ),
    "reader": (
        "【读者会指南】\n"
        "1. 读者会提供了六种不同偏好的读者画像：剧情党、角色党、设定党、情感党、文字党、休闲读者。\n"
        "2. 你可以按需勾选 1 至 6 位读者进行邀请，并设定阅读偏好预算。\n"
        "3. 读者将进行真实段落研读、给出第一印象报告并在讨论室中展开交流，为你提供客观的多角度反馈。\n"
        "4. 读者会仅提供模拟反馈，绝不会擅自修改或批准你的正文。"
    ),
    "finalize": (
        "【本地定稿指南】\n"
        "1. 当审阅完成或满意后，点击右上角「本地定稿」按钮。\n"
        "2. 系统弹出二次确认对话框，确认后会将当前最新审阅版本固化为只读终稿（COMPLETED）。\n"
        "3. 请放心：本地定稿仅在当前本地工程数据库内固化版本，绝不会发布到任何外部公开平台。"
    ),
    "permissions": (
        "【助手权限说明】\n"
        "Gura 助手拥有严格受限的只读业务查询权限。我可以帮你查询大纲、正文摘要、审阅报告、还原点与操作指南，"
        "但绝对无法替你执行修改正文、批准大纲、确认警告或定稿发布等写操作。这些核心决策必须由你在操作界面中自主确认。"
    ),
    "general": (
        "【Gura Studio 概览】\n"
        "Gura Studio 是专为小说创作者打造的工作台，流程包含：大纲构思（Outline）→ 正文创作（Draft）→ 编辑审阅（Review）→ 读者会（Reader Panel）→ 本地定稿（Final）。\n"
        "如需查询具体模块用法，请随时问我，例如「大纲怎么用」、「如何处理警告」、「读者会有哪些人」等。"
    ),
}

AVAILABLE_ASSISTANT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_project_summary",
        "description": "获取当前小说的工程基本信息（标题、标识、总章节数等）。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_chapter_outline",
        "description": "获取指定章节的大纲内容及大纲批准状态。",
        "parameters": {
            "type": "object",
            "properties": {
                "chapter_id": {
                    "type": "string",
                    "description": "章节 UUID，若不提供则使用会话关联章节",
                }
            },
        },
    },
    {
        "name": "get_chapter_draft_summary",
        "description": "获取指定章节的草稿字数、创作状态及开头正文摘录。",
        "parameters": {
            "type": "object",
            "properties": {
                "chapter_id": {
                    "type": "string",
                    "description": "章节 UUID，若不提供则使用会话关联章节",
                }
            },
        },
    },
    {
        "name": "get_chapter_review_reports",
        "description": "获取指定章节的最新审阅报告（责任编辑、主编、设定编辑反馈，包含阻塞项与警告项）。",
        "parameters": {
            "type": "object",
            "properties": {
                "chapter_id": {
                    "type": "string",
                    "description": "章节 UUID，若不提供则使用会话关联章节",
                }
            },
        },
    },
    {
        "name": "list_chapter_restore_points",
        "description": "列出指定章节的历史手动还原点存档摘要。",
        "parameters": {
            "type": "object",
            "properties": {
                "chapter_id": {
                    "type": "string",
                    "description": "章节 UUID，若不提供则使用会话关联章节",
                }
            },
        },
    },
    {
        "name": "get_software_guidance",
        "description": "查询软件各模块的操作方法（大纲生成、草稿写作、审阅警告、版本还原、读者会、定稿等）。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": [
                        "outline",
                        "draft",
                        "review",
                        "warning",
                        "restore_point",
                        "reader",
                        "finalize",
                        "permissions",
                        "general",
                    ],
                    "description": "需要查询的指导主题",
                }
            },
            "required": ["topic"],
        },
    },
]


async def _resolve_chapter(
    session: AsyncSession, project_id: UUID, chapter_id: UUID | None
) -> Chapter:
    if chapter_id is None:
        raise ValidationError("No chapter_id provided.")
    chapter = await session.scalar(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
    )
    if chapter is None:
        raise NotFoundError("Chapter not found in this project.")
    return chapter


async def tool_get_project_summary(session: AsyncSession, project_id: UUID) -> dict[str, Any]:
    project = await session.scalar(select(Project).where(Project.id == project_id))
    if project is None:
        raise NotFoundError("Project not found.")
    chapter_count = await session.scalar(
        select(func.count()).select_from(Chapter).where(Chapter.project_id == project_id)
    )
    return {
        "project_id": str(project.id),
        "title": project.title,
        "slug": project.slug,
        "chapter_count": int(chapter_count or 0),
        "created_at": project.created_at.isoformat() if project.created_at else None,
    }


async def tool_get_chapter_outline(
    session: AsyncSession, project_id: UUID, chapter_id: UUID | None
) -> dict[str, Any]:
    chapter = await _resolve_chapter(session, project_id, chapter_id)
    outline_text = ""
    if chapter.current_outline_document_id:
        try:
            content_info = await DocumentService(session).read_current_content(
                chapter.current_outline_document_id
            )
            outline_text = content_info.content
        except Exception:
            outline_text = ""

    return {
        "chapter_id": str(chapter.id),
        "chapter_number": chapter.chapter_number,
        "title": chapter.title or f"第 {chapter.chapter_number} 章",
        "status": chapter.status,
        "is_approved": chapter.approved_outline_version_id is not None,
        "approved_version_id": (
            str(chapter.approved_outline_version_id)
            if chapter.approved_outline_version_id
            else None
        ),
        "outline_text": outline_text,
    }


async def tool_get_chapter_draft_summary(
    session: AsyncSession, project_id: UUID, chapter_id: UUID | None
) -> dict[str, Any]:
    chapter = await _resolve_chapter(session, project_id, chapter_id)
    draft_text = ""
    if chapter.current_draft_document_id:
        try:
            content_info = await DocumentService(session).read_current_content(
                chapter.current_draft_document_id
            )
            draft_text = content_info.content
        except Exception:
            draft_text = ""

    char_count = len(draft_text.strip())
    return {
        "chapter_id": str(chapter.id),
        "chapter_number": chapter.chapter_number,
        "title": chapter.title or f"第 {chapter.chapter_number} 章",
        "status": chapter.status,
        "has_draft": bool(chapter.current_draft_document_id),
        "character_count": char_count,
        "excerpt": draft_text[:400] + ("..." if len(draft_text) > 400 else ""),
    }


async def tool_get_chapter_review_reports(
    session: AsyncSession, project_id: UUID, chapter_id: UUID | None
) -> dict[str, Any]:
    chapter = await _resolve_chapter(session, project_id, chapter_id)
    reports = list(
        await session.scalars(
            select(ReviewReport)
            .where(
                ReviewReport.chapter_id == chapter.id,
                ReviewReport.project_id == project_id,
            )
            .order_by(ReviewReport.created_at.desc())
            .limit(10)
        )
    )

    summaries = []
    has_blocking = False
    has_warnings = False
    for r in reports:
        blocking_cnt = len(r.blocking_issues) if isinstance(r.blocking_issues, list) else 0
        warning_cnt = len(r.warnings) if isinstance(r.warnings, list) else 0
        if blocking_cnt > 0:
            has_blocking = True
        if warning_cnt > 0:
            has_warnings = True

        summaries.append(
            {
                "report_id": str(r.id),
                "review_mode": r.review_mode,
                "reviewer_agent_role": r.reviewer_agent_role,
                "passed": r.passed,
                "summary": r.summary,
                "blocking_issues_count": blocking_cnt,
                "warnings_count": warning_cnt,
                "notes_count": len(r.notes) if isinstance(r.notes, list) else 0,
                "blocking_issues": r.blocking_issues if isinstance(r.blocking_issues, list) else [],
                "warnings": r.warnings if isinstance(r.warnings, list) else [],
                "suggested_actions": (
                    r.suggested_actions if isinstance(r.suggested_actions, list) else []
                ),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )

    return {
        "chapter_id": str(chapter.id),
        "chapter_number": chapter.chapter_number,
        "status": chapter.status,
        "reports_count": len(summaries),
        "has_blocking": has_blocking,
        "has_warnings": has_warnings,
        "reports": summaries,
    }


async def tool_list_chapter_restore_points(
    session: AsyncSession, project_id: UUID, chapter_id: UUID | None
) -> dict[str, Any]:
    chapter = await _resolve_chapter(session, project_id, chapter_id)
    points = list(
        await session.scalars(
            select(StudioRestorePoint)
            .where(StudioRestorePoint.chapter_id == chapter.id)
            .order_by(StudioRestorePoint.created_at.desc())
            .limit(20)
        )
    )
    return {
        "chapter_id": str(chapter.id),
        "count": len(points),
        "restore_points": [
            {
                "id": str(p.id),
                "summary": p.summary,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "has_feedback": p.feedback_snapshot is not None,
            }
            for p in points
        ],
    }


def tool_get_software_guidance(topic: str) -> dict[str, Any]:
    clean_topic = topic.strip().lower() if topic else "general"
    guide = SOFTWARE_GUIDES.get(clean_topic, SOFTWARE_GUIDES["general"])
    return {
        "topic": clean_topic,
        "guidance": guide,
    }


async def execute_assistant_tool(
    session: AsyncSession,
    project_id: UUID,
    tool_name: str,
    arguments: dict[str, Any],
    default_chapter_id: UUID | None = None,
) -> dict[str, Any]:
    """Execute a strictly read-only business tool and return the result dictionary."""
    target_chapter_id = default_chapter_id
    if "chapter_id" in arguments and arguments["chapter_id"]:
        try:
            target_chapter_id = UUID(str(arguments["chapter_id"]))
        except ValueError as err:
            raise ValidationError(f"Invalid chapter_id: {arguments['chapter_id']}") from err

    if tool_name == "get_project_summary":
        return await tool_get_project_summary(session, project_id)
    elif tool_name == "get_chapter_outline":
        return await tool_get_chapter_outline(session, project_id, target_chapter_id)
    elif tool_name == "get_chapter_draft_summary":
        return await tool_get_chapter_draft_summary(session, project_id, target_chapter_id)
    elif tool_name == "get_chapter_review_reports":
        return await tool_get_chapter_review_reports(session, project_id, target_chapter_id)
    elif tool_name == "list_chapter_restore_points":
        return await tool_list_chapter_restore_points(session, project_id, target_chapter_id)
    elif tool_name == "get_software_guidance":
        topic = str(arguments.get("topic", "general"))
        return tool_get_software_guidance(topic)
    else:
        raise ValidationError(f"Unknown assistant tool: {tool_name}")
