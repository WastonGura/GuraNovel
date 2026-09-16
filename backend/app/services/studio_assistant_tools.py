"""Bounded, strictly read-only business tools for Gura Studio Assistant.

Security & authority guarantees:
1. Strictly read-only queries. Never creates, modifies, deletes, approves, restores, or finalizes any resources.
2. Cross-project isolation: every query is strictly scoped to the authenticated conversation's project_id.
3. Parameter validation: checks chapter and project ownership and validates inputs before querying.
"""

from __future__ import annotations

from typing import Any, Sequence
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models import Chapter, Document, Project, ReviewReport, StudioRestorePoint
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
    {
        "name": "create_chapter",
        "description": "在当前作品工程中创建新的一章（安全有界写操作）。",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "章节标题，若不提供则自动分配第 N 章",
                }
            },
        },
    },
    {
        "name": "navigate_view",
        "description": "在工作台界面之间跳转导航（安全有界动作）。",
        "parameters": {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "enum": ["outline", "draft", "review", "reader", "final", "dashboard", "chapters"],
                    "description": "目标页面或阶段",
                },
                "chapter_id": {
                    "type": "string",
                    "description": "目标章节 UUID（可选）",
                },
            },
            "required": ["view"],
        },
    },
    {
        "name": "trigger_chapter_review",
        "description": "协助发起当前章节的审阅流程（安全有界动作）。",
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
        "name": "trigger_feedback_revision",
        "description": "协助根据审阅或读者反馈指引发起正文修订（安全有界动作）。",
        "parameters": {
            "type": "object",
            "properties": {
                "chapter_id": {
                    "type": "string",
                    "description": "章节 UUID，若不提供则使用会话关联章节",
                },
                "feedback": {
                    "type": "string",
                    "description": "用户的具体修改要求或反馈说明",
                },
                "target_segment_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "需要修订的目标段落 UUID 列表，若不指定则自动提取审阅批注中的定位段落",
                },
            },
        },
    },
    {
        "name": "get_setting_context",
        "description": "查询当前小说关联设定集的修订号、哈希与全部设定条目（世界观、势力、地理、历史、角色卡等）。",
        "parameters": {
            "type": "object",
            "properties": {
                "document_type": {
                    "type": "string",
                    "description": "设定文档类型过滤（可选，例如 world_overview, power_system, factions, geography, history, character_profile, glossary）",
                }
            },
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

    # 1. Resolve chapter's current draft document version
    current_draft_version_id = None
    current_draft_version_number = None
    if chapter.current_draft_document_id:
        doc = await session.get(Document, chapter.current_draft_document_id)
        if doc and doc.current_version_id:
            current_draft_version_id = doc.current_version_id
            from app.models.core import DocumentVersion
            ver = await session.get(DocumentVersion, current_draft_version_id)
            if ver is not None:
                current_draft_version_number = ver.version_number

    target_version_id = current_draft_version_id
    target_version_number = current_draft_version_number
    reports: list[ReviewReport] = []

    if target_version_id is not None:
        query = (
            select(ReviewReport)
            .where(
                ReviewReport.chapter_id == chapter.id,
                ReviewReport.project_id == project_id,
                ReviewReport.target_version_id == target_version_id,
            )
            .order_by(ReviewReport.created_at.asc())
        )
        reports = list(await session.scalars(query))

        if target_version_number is None:
            from app.models.core import DocumentVersion
            ver = await session.get(DocumentVersion, target_version_id)
            if ver is not None:
                target_version_number = ver.version_number

    total_count = await session.scalar(
        select(func.count(ReviewReport.id)).where(
            ReviewReport.chapter_id == chapter.id,
            ReviewReport.project_id == project_id,
        )
    ) or 0
    historical_count = max(0, total_count - len(reports))

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
                "target_version_id": str(r.target_version_id) if r.target_version_id else None,
                "target_version_number": target_version_number,
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
        "target_version_id": str(target_version_id) if target_version_id else None,
        "target_version_number": target_version_number,
        "reports_count": len(summaries),
        "historical_reports_count": historical_count,
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


async def tool_create_chapter(
    session: AsyncSession, project_id: UUID, title: str | None = None
) -> dict[str, Any]:
    from app.services.chapter_service import ChapterService

    clean_title = title.strip() if title and isinstance(title, str) and title.strip() else None

    chapter = await ChapterService(session).create_chapter(
        project_id=project_id,
        title=clean_title,
    )
    target_url = f"/projects/{project_id}/studio/{chapter.id}?view=Create&stage=Outline"
    return {
        "action": "create_chapter",
        "chapter_id": str(chapter.id),
        "chapter_number": chapter.chapter_number,
        "title": chapter.title or f"第 {chapter.chapter_number} 章",
        "target_url": target_url,
        "status": "created",
        "message": f"已成功为你创建第 {chapter.chapter_number} 章「{chapter.title or ''}」！你可以前往该章节开始大纲构思或正文创作。",
    }


def tool_navigate_view(
    project_id: UUID, view: str, chapter_id: UUID | None = None
) -> dict[str, Any]:
    clean_view = view.strip().lower() if view else "dashboard"
    valid_views: dict[str, tuple[str, str]] = {
        "outline": (
            "大纲构思",
            f"/projects/{project_id}/studio/{chapter_id}?view=Create&stage=Outline"
            if chapter_id
            else f"/projects/{project_id}",
        ),
        "draft": (
            "正文创作",
            f"/projects/{project_id}/studio/{chapter_id}?view=Create&stage=Draft"
            if chapter_id
            else f"/projects/{project_id}",
        ),
        "review": (
            "编辑审阅",
            f"/projects/{project_id}/studio/{chapter_id}?view=Create&stage=Review"
            if chapter_id
            else f"/projects/{project_id}",
        ),
        "reader": (
            "读者会",
            f"/projects/{project_id}/studio/{chapter_id}?view=Create&stage=Reader"
            if chapter_id
            else f"/projects/{project_id}",
        ),
        "final": (
            "本地定稿",
            f"/projects/{project_id}/studio/{chapter_id}?view=Create&stage=Final"
            if chapter_id
            else f"/projects/{project_id}",
        ),
        "dashboard": ("作品看板", f"/projects/{project_id}"),
        "chapters": ("章节列表", f"/projects/{project_id}"),
    }
    label, url = valid_views.get(clean_view, ("工作台", f"/projects/{project_id}"))
    return {
        "action": "navigate_view",
        "view": clean_view,
        "label": label,
        "target_url": url,
        "chapter_id": str(chapter_id) if chapter_id else None,
        "message": f"正在为你跳转至「{label}」界面。",
    }


async def tool_trigger_chapter_review(
    session: AsyncSession, project_id: UUID, chapter_id: UUID | None
) -> dict[str, Any]:
    chapter = await _resolve_chapter(session, project_id, chapter_id)
    char_count = 0
    if chapter.current_draft_document_id:
        try:
            content_info = await DocumentService(session).read_current_content(
                chapter.current_draft_document_id
            )
            char_count = len(content_info.content.strip())
        except Exception:
            pass

    if char_count == 0:
        return {
            "action": "trigger_review",
            "status": "blocked",
            "chapter_id": str(chapter.id),
            "character_count": 0,
            "message": "当前章节尚未输入正文草稿，无法发起审阅。请先在草稿（Draft）视图中编写正文内容后再提交审阅。",
        }

    from app.models.core import WorkflowRun
    from app.models.enums import WorkflowType
    from app.workflows.chapter_production import ChapterProductionStatus

    project = await session.get(Project, project_id)
    actor_id = (project.owner_id if project else None) or uuid4()

    latest_run = await session.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.project_id == project_id,
            WorkflowRun.chapter_id == chapter.id,
            WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
        )
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
        .limit(1)
    )

    target_url = f"/projects/{project_id}/studio/{chapter.id}?view=Create&stage=Review"
    triggered_action = "ready"
    run_id_str = None

    if latest_run is not None and actor_id is not None:
        run_id_str = str(latest_run.id)
        try:
            from app.api.deps import get_chapter_production_v2_composition

            composition = get_chapter_production_v2_composition()
            prod_service = composition.create_service(session)
            state, _ = await prod_service._locked_state(latest_run)
            if state.status == ChapterProductionStatus.AUTHOR_REVISION and state.awaiting_user and state.action_request_id:
                await prod_service.resolve_author_action(
                    project_id=project_id,
                    chapter_id=chapter.id,
                    workflow_run_id=latest_run.id,
                    action_request_id=UUID(str(state.action_request_id)),
                    actor_user_id=actor_id,
                    decision="accept",
                )
                try:
                    await prod_service.execute_current_review(
                        project_id=project_id,
                        chapter_id=chapter.id,
                        workflow_run_id=latest_run.id,
                        actor_user_id=actor_id,
                    )
                    triggered_action = "review_executed"
                except Exception:
                    triggered_action = "accepted_review_failed"
            elif state.status in (
                ChapterProductionStatus.EDITOR_REVIEW,
                ChapterProductionStatus.CHIEF_FINAL_REVIEW,
                ChapterProductionStatus.LORE_FINAL_REVIEW,
            ) and not state.awaiting_user:
                try:
                    await prod_service.execute_current_review(
                        project_id=project_id,
                        chapter_id=chapter.id,
                        workflow_run_id=latest_run.id,
                        actor_user_id=actor_id,
                    )
                    triggered_action = "review_executed"
                except Exception:
                    triggered_action = "review_failed"
            else:
                triggered_action = "ready"
        except Exception:
            triggered_action = "review_failed"

    if triggered_action == "review_executed":
        status = "triggered"
        msg = f"🦈 已成功为你启动第 {chapter.chapter_number} 章（约 {char_count} 字）的审阅流程！三级编辑正在进行审阅。\n\n👉 [前往审阅面板查看实时进度]({target_url})"
    elif triggered_action == "accepted_review_failed":
        status = "failed"
        msg = f"已完成作者动作确认，但在启动自动审阅执行时失败。你可以点击下方链接前往审阅面板手动重试审阅。\n\n👉 [前往审阅面板]({target_url})"
    elif triggered_action == "review_failed":
        status = "failed"
        msg = f"启动第 {chapter.chapter_number} 章的审阅执行失败。你可以点击下方链接前往审阅面板查看详情并重试。\n\n👉 [前往审阅面板]({target_url})"
    else:
        status = "ready"
        msg = f"第 {chapter.chapter_number} 章正文（约 {char_count} 字）已就绪。你可以点击下方链接前往审阅面板提交审阅。\n\n👉 [前往审阅面板]({target_url})"

    return {
        "action": "trigger_review",
        "status": status,
        "chapter_id": str(chapter.id),
        "workflow_run_id": run_id_str,
        "character_count": char_count,
        "target_url": target_url,
        "message": msg,
    }


async def tool_trigger_feedback_revision(
    session: AsyncSession,
    project_id: UUID,
    chapter_id: UUID | None,
    feedback: str | None = None,
    target_segment_ids: Sequence[str | UUID] | None = None,
) -> dict[str, Any]:
    chapter = await _resolve_chapter(session, project_id, chapter_id)
    reports_info = await tool_get_chapter_review_reports(session, project_id, chapter.id)
    has_blocking = reports_info.get("has_blocking", False)
    has_warnings = reports_info.get("has_warnings", False)
    target_url = f"/projects/{project_id}/studio/{chapter.id}?view=Create&stage=Draft"

    from app.models.core import WorkflowRun
    from app.models.enums import WorkflowType
    from app.workflows.chapter_production import ChapterProductionStatus

    project = await session.get(Project, project_id)
    actor_id = (project.owner_id if project else None) or uuid4()

    latest_run = await session.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.project_id == project_id,
            WorkflowRun.chapter_id == chapter.id,
            WorkflowRun.workflow_type == WorkflowType.CHAPTER_PRODUCTION.value,
        )
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
        .limit(1)
    )

    executed_revision = False
    if latest_run is not None and actor_id is not None:
        try:
            from app.api.deps import get_chapter_production_v2_composition

            composition = get_chapter_production_v2_composition()
            prod_service = composition.create_service(session)
            state, _ = await prod_service._locked_state(latest_run)
            if state.status == ChapterProductionStatus.AUTHOR_REVISION and state.awaiting_user and state.action_request_id:
                if chapter.current_draft_document_id and state.document_version_id:
                    targets: tuple[UUID, ...] = ()
                    if target_segment_ids:
                        valid_targets = []
                        for s in target_segment_ids:
                            try:
                                valid_targets.append(UUID(str(s)))
                            except (ValueError, TypeError):
                                pass
                        targets = tuple(valid_targets)
                    else:
                        extracted_sids: list[UUID] = []
                        for r in reports_info.get("reports", []):
                            for issue in (r.get("blocking_issues") or []) + (r.get("warnings") or []):
                                for sid in issue.get("evidence_segment_ids") or []:
                                    try:
                                        u = UUID(str(sid))
                                        if u not in extracted_sids:
                                            extracted_sids.append(u)
                                    except (ValueError, TypeError):
                                        pass
                        if extracted_sids:
                            targets = tuple(extracted_sids)
                        else:
                            segments = await prod_service.documents.derive_chapter_segment_map(
                                project_id=project_id,
                                chapter_id=chapter.id,
                                document_id=chapter.current_draft_document_id,
                                version_id=UUID(state.document_version_id),
                            )
                            targets = tuple(s.segment_id for s in segments.segments)

                    if feedback and feedback.strip():
                        revision_feedback = feedback.strip()
                    else:
                        rationales: list[str] = []
                        for r in reports_info.get("reports", []):
                            for issue in (r.get("blocking_issues") or []) + (r.get("warnings") or []):
                                rat = issue.get("rationale") or issue.get("suggested_action")
                                if rat and rat not in rationales:
                                    rationales.append(str(rat).strip())
                        if rationales:
                            revision_feedback = "根据审阅批注进行修订：" + "；".join(rationales[:5])
                        else:
                            revision_feedback = "根据审阅批注与读者反馈进行修订，纠正不符合设定或剧情走向之处。"

                    if targets:
                        await prod_service.request_user_feedback_revision(
                            project_id=project_id,
                            chapter_id=chapter.id,
                            workflow_run_id=latest_run.id,
                            action_request_id=UUID(str(state.action_request_id)),
                            actor_user_id=actor_id,
                            feedback=revision_feedback,
                            target_segment_ids=targets,
                        )
                        executed_revision = True
        except Exception:
            pass

    if executed_revision:
        return {
            "action": "trigger_feedback_revision",
            "status": "executed",
            "chapter_id": str(chapter.id),
            "target_url": target_url,
            "message": f"🦈 已成功为你启动第 {chapter.chapter_number} 章的反馈修订流程！Writer 正在结合反馈意见生成新稿。\n\n👉 [前往草稿界面查看]({target_url})",
        }

    if has_blocking or has_warnings:
        kind = "阻塞项" if has_blocking else "警告项"
        return {
            "action": "trigger_feedback_revision",
            "status": "guided",
            "chapter_id": str(chapter.id),
            "target_url": target_url,
            "message": (
                f"🦈 本章审阅发现了{kind}！\n\n"
                "你可以前往草稿界面，在右侧审阅批注栏中勾选需要修改的问题，点击「修改选中的问题」发起修订；"
                "或者直接在正文编辑器中自行调整。\n\n"
                f"👉 [前往草稿界面修订]({target_url})"
            ),
        }
    return {
        "action": "trigger_feedback_revision",
        "status": "no_issues",
        "chapter_id": str(chapter.id),
        "target_url": target_url,
        "message": f"当前章节未发现未解决的阻塞审阅问题。你可以继续写作或前往读者会环节。\n\n👉 [前往草稿界面]({target_url})",
    }


async def tool_get_setting_context(
    session: AsyncSession,
    project_id: UUID,
    document_type: str | None = None,
) -> dict[str, Any]:
    from app.services.setting_context_resolver import SettingContextResolver
    resolver = SettingContextResolver(session)
    allowed = [document_type] if document_type else None
    bundle = await resolver.resolve_for_project(project_id, allowed_types=allowed)
    return {
        "project_id": str(project_id),
        "setting_collection_id": str(bundle.setting_collection_id),
        "collection_revision": bundle.collection_revision,
        "bundle_hash": bundle.bundle_hash,
        "document_count": len(bundle.documents),
        "documents": [
            {
                "document_id": str(d.document_id),
                "version_id": str(d.version_id),
                "type": d.document_type,
                "title": d.title,
                "path": d.path,
                "content_hash": d.content_hash,
                "content_preview": d.content[:200] + ("..." if len(d.content) > 200 else ""),
            }
            for d in bundle.documents
        ],
    }


async def execute_assistant_tool(
    session: AsyncSession,
    project_id: UUID,
    tool_name: str,
    arguments: dict[str, Any],
    default_chapter_id: UUID | None = None,
) -> dict[str, Any]:
    """Execute a business tool and return the result dictionary."""
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
    elif tool_name == "get_setting_context":
        document_type = arguments.get("document_type")
        return await tool_get_setting_context(session, project_id, document_type=document_type)
    elif tool_name == "create_chapter":
        title = arguments.get("title")
        return await tool_create_chapter(session, project_id, title)
    elif tool_name == "navigate_view":
        view = str(arguments.get("view", "dashboard"))
        return tool_navigate_view(project_id, view, target_chapter_id)
    elif tool_name == "trigger_chapter_review":
        return await tool_trigger_chapter_review(session, project_id, target_chapter_id)
    elif tool_name == "trigger_feedback_revision":
        feedback = arguments.get("feedback")
        target_segment_ids = arguments.get("target_segment_ids")
        return await tool_trigger_feedback_revision(
            session,
            project_id,
            target_chapter_id,
            feedback=feedback,
            target_segment_ids=target_segment_ids,
        )
    else:
        raise ValidationError(f"Unknown assistant tool: {tool_name}")
