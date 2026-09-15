"""Integration tests for Studio Assistant API and bounded read-only tools."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db_session
from app.main import create_app
from app.models import (
    Chapter,
    DocumentSource,
    DocumentType,
    Project,
    ReviewReport,
    StudioRestorePoint,
)
from app.services import DocumentService

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def client(async_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def seed_data(session: AsyncSession, path: Path):
    project = Project(slug=f"assistant-{uuid4()}", title="鲛人传", workspace_root=str(path))
    session.add(project)
    await session.flush()

    chapter = Chapter(project_id=project.id, chapter_number=1, title="深海初遇")
    session.add(chapter)
    await session.commit()

    # Create outline document
    outline_doc = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Outline",
        path="outline.md",
        content="# 场景一：深海之门\n夜幕降临，鲛人潜入遗迹，偶遇调查员。",
        source=DocumentSource.USER,
    )
    chapter.current_outline_document_id = outline_doc.id
    chapter.approved_outline_version_id = outline_doc.current_version_id

    # Create draft document
    draft_doc = await DocumentService(session).create_document(
        project_id=project.id,
        chapter_id=chapter.id,
        document_type=DocumentType.CHAPTER_DRAFT,
        title="Draft",
        path="draft.md",
        content="深海冰冷而寂静。微弱的荧光在珊瑚丛中闪烁，那是鲛人游弋留下的痕迹。",
        source=DocumentSource.USER,
    )
    chapter.current_draft_document_id = draft_doc.id

    # Create restore point
    restore_pt = StudioRestorePoint(
        id=uuid4(),
        chapter_id=chapter.id,
        document_id=draft_doc.id,
        version_id=draft_doc.current_version_id,
        summary="初稿第一版存档",
    )
    session.add(restore_pt)

    # Create review report with a warning
    report = ReviewReport(
        project_id=project.id,
        chapter_id=chapter.id,
        target_document_id=draft_doc.id,
        target_version_id=draft_doc.current_version_id,
        review_mode="editor",
        reviewer_agent_role="责任编辑",
        passed=False,
        summary="整体氛围渲染出色，但节奏略显舒缓。",
        blocking_issues=[],
        warnings=[{"id": "w1", "message": "开篇对话较少，环境描写略显冗长。"}],
        notes=[],
        suggested_actions=["增加两句简短内心独白"],
    )
    session.add(report)
    await session.commit()

    return project, chapter, outline_doc, draft_doc


async def test_assistant_conversation_lifecycle(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)

    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    # 1. Create conversation
    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    assert res.status_code == 200, res.text
    data = res.json()
    conv_id = data["id"]
    assert data["project_id"] == str(project.id)
    assert data["chapter_id"] == str(chapter.id)
    assert data["messages"] == []

    # 2. Get-or-create idempotency
    res_repeat = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    assert res_repeat.status_code == 200
    assert res_repeat.json()["id"] == conv_id

    # 3. List conversations
    res_list = await client.get(base_url)
    assert res_list.status_code == 200
    assert len(res_list.json()) >= 1
    assert any(c["id"] == conv_id for c in res_list.json())

    # 4. Get specific conversation
    res_get = await client.get(f"{base_url}/{conv_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == conv_id


async def test_assistant_blocks_unauthorized_mutations(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    # Create conversation
    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    # Send unauthorized mutation request
    msg_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "帮我直接定稿这一章并发布", "chapter_id": str(chapter.id)},
    )
    assert msg_res.status_code == 200
    data = msg_res.json()
    assert len(data["messages"]) == 2  # user + assistant
    user_msg = data["messages"][0]
    assist_msg = data["messages"][1]

    assert user_msg["role"] == "user"
    assert "帮我直接定稿" in user_msg["content"]

    assert assist_msg["role"] == "assistant"
    assert "受限的只读业务" in assist_msg["content"]
    assert "不能" in assist_msg["content"]
    assert assist_msg["tool_calls"] is not None
    assert assist_msg["tool_calls"][0]["name"] == "get_software_guidance"


async def test_assistant_queries_outline_and_draft_summary(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    # Query outline
    outline_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "本章大纲是什么？", "chapter_id": str(chapter.id)},
    )
    assert outline_res.status_code == 200
    msgs = outline_res.json()["messages"]
    last_msg = msgs[-1]
    assert last_msg["role"] == "assistant"
    assert "已批准锁定" in last_msg["content"]
    assert "深海之门" in last_msg["content"]
    assert last_msg["tool_calls"][0]["name"] == "get_chapter_outline"

    # Query draft
    draft_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "当前草稿写了多少字数？", "chapter_id": str(chapter.id)},
    )
    assert draft_res.status_code == 200
    last_msg = draft_res.json()["messages"][-1]
    assert last_msg["role"] == "assistant"
    assert "草稿情况" in last_msg["content"]
    assert "珊瑚丛" in last_msg["content"]
    assert last_msg["tool_calls"][0]["name"] == "get_chapter_draft_summary"


async def test_assistant_queries_review_and_warnings(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    # Query review reports
    review_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "本章审阅报告有什么警告问题吗？", "chapter_id": str(chapter.id)},
    )
    assert review_res.status_code == 200
    last_msg = review_res.json()["messages"][-1]
    assert last_msg["role"] == "assistant"
    assert "责任编辑" in last_msg["content"]
    assert "环境描写略显冗长" in last_msg["content"]
    assert "接受当前警告并继续审阅" in last_msg["content"]
    assert last_msg["tool_calls"][0]["name"] == "get_chapter_review_reports"


async def test_assistant_rejects_cross_project_access(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    fake_project_id = uuid4()
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    # 1. Non-existent project
    res_fake_proj = await client.post(
        f"/api/v1/projects/{fake_project_id}/assistant/conversations",
        json={"chapter_id": str(chapter.id)},
    )
    assert res_fake_proj.status_code == 404

    # 2. Foreign chapter belonging to another project
    other_proj = Project(slug=f"other-{uuid4()}", title="Other", workspace_root=str(tmp_path / "other"))
    async_session.add(other_proj)
    await async_session.flush()
    other_chapter = Chapter(project_id=other_proj.id, chapter_number=1)
    async_session.add(other_chapter)
    await async_session.commit()

    res_foreign = await client.post(
        base_url,
        json={"chapter_id": str(other_chapter.id)},
    )
    assert res_foreign.status_code == 404


async def test_assistant_explains_buttons_for_current_view(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    # 1. Draft view button guidance
    draft_btn_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "这个按钮有什么用？", "chapter_id": str(chapter.id), "current_view": "Draft"},
    )
    assert draft_btn_res.status_code == 200
    msg = draft_btn_res.json()["messages"][-1]
    assert msg["role"] == "assistant"
    assert "创建还原点" in msg["content"]
    assert "提交审阅" in msg["content"]

    # 2. Review view button guidance
    review_btn_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "当前界面怎么用？", "chapter_id": str(chapter.id), "current_view": "Review"},
    )
    assert review_btn_res.status_code == 200
    msg = review_btn_res.json()["messages"][-1]
    assert msg["role"] == "assistant"
    assert "修改所选" in msg["content"]
    assert "接受当前警告" in msg["content"]

    # 3. Outline view button guidance
    outline_btn_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "界面上的按钮是干什么的？", "chapter_id": str(chapter.id), "current_view": "Outline"},
    )
    assert outline_btn_res.status_code == 200
    msg = outline_btn_res.json()["messages"][-1]
    assert msg["role"] == "assistant"
    assert "生成大纲方案" in msg["content"]
    assert "选择这个方向" in msg["content"]


async def test_assistant_executes_bounded_actions(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    # 1. Create chapter action
    create_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "帮我创建新的一章名为深海秘宝", "chapter_id": str(chapter.id)},
    )
    assert create_res.status_code == 200
    msg = create_res.json()["messages"][-1]
    assert msg["role"] == "assistant"
    assert "创建新章节" in msg["content"]
    assert msg["tool_calls"][0]["name"] == "create_chapter"
    created_res = msg["tool_results"][0]["result"]
    assert created_res["action"] == "create_chapter"
    assert "深海秘宝" in created_res["title"]
    assert f"/projects/{project.id}/studio/" in created_res["target_url"]

    # 2. Navigate view action
    nav_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "跳到审阅页面", "chapter_id": str(chapter.id)},
    )
    assert nav_res.status_code == 200
    msg = nav_res.json()["messages"][-1]
    assert msg["role"] == "assistant"
    assert msg["tool_calls"][0]["name"] == "navigate_view"
    nav_result = msg["tool_results"][0]["result"]
    assert nav_result["action"] == "navigate_view"
    assert nav_result["view"] == "review"
    assert f"/projects/{project.id}/studio/{chapter.id}?view=Create&stage=Review" in nav_result["target_url"]

    # 3. Trigger review action
    review_act_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "帮我提交审阅这一章", "chapter_id": str(chapter.id)},
    )
    assert review_act_res.status_code == 200
    msg = review_act_res.json()["messages"][-1]
    assert msg["role"] == "assistant"
    assert msg["tool_calls"][0]["name"] == "trigger_chapter_review"
    trig_res = msg["tool_results"][0]["result"]
    assert trig_res["action"] == "trigger_review"
    assert trig_res["status"] == "ready"
    assert f"/projects/{project.id}/studio/{chapter.id}?view=Create&stage=Review" in trig_res["target_url"]


async def test_assistant_distinguishes_creation_intent(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    # 1. Negative intent: "不要创建一章"
    neg_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "不要创建一章", "chapter_id": str(chapter.id)},
    )
    assert neg_res.status_code == 200
    msg = neg_res.json()["messages"][-1]
    assert msg["role"] == "assistant"
    assert "不会创建新章节" in msg["content"]
    assert not msg.get("tool_calls")

    # 2. Inquiry intent: "怎么新建章节？"
    inq_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "怎么新建章节？", "chapter_id": str(chapter.id)},
    )
    assert inq_res.status_code == 200
    msg = inq_res.json()["messages"][-1]
    assert msg["role"] == "assistant"
    assert "新建章节的方法如下" in msg["content"]
    assert msg["tool_calls"][0]["name"] == "get_software_guidance"

    # 3. Directive creation intent: "帮我新建一章名为测试章节"
    dir_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "帮我新建一章名为测试章节", "chapter_id": str(chapter.id)},
    )
    assert dir_res.status_code == 200
    msg = dir_res.json()["messages"][-1]
    assert msg["role"] == "assistant"
    assert msg["tool_calls"][0]["name"] == "create_chapter"
    created_res = msg["tool_results"][0]["result"]
    assert created_res["title"] == "测试章节"
    assert created_res["status"] == "created"
    assert f"/projects/{project.id}/studio/" in created_res["target_url"]
    assert "stage=Outline" in created_res["target_url"]

    # 4. Review negative intent: "不要提交审阅"
    rev_neg_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "不要提交审阅", "chapter_id": str(chapter.id)},
    )
    assert rev_neg_res.status_code == 200
    rev_neg_msg = rev_neg_res.json()["messages"][-1]
    assert rev_neg_msg["role"] == "assistant"
    assert "不会为你提交审阅" in rev_neg_msg["content"]
    assert not rev_neg_msg.get("tool_calls")

    # 5. Review inquiry intent: "如何提交审阅？"
    rev_inq_res = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "如何提交审阅？", "chapter_id": str(chapter.id)},
    )
    assert rev_inq_res.status_code == 200
    rev_inq_msg = rev_inq_res.json()["messages"][-1]
    assert rev_inq_msg["role"] == "assistant"
    assert "提交审阅的操作方法如下" in rev_inq_msg["content"]
    assert rev_inq_msg["tool_calls"][0]["name"] == "get_software_guidance"


async def test_assistant_chapter_creation_and_message_deduplication(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    # 1. First send with client_message_id
    req_id = f"client-req-{uuid4()}"
    res1 = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "帮我新建一章名为幂等测试", "chapter_id": str(chapter.id), "client_message_id": req_id},
    )
    assert res1.status_code == 200
    msgs1 = res1.json()["messages"]
    msg_count1 = len(msgs1)
    last_msg1 = msgs1[-1]
    assert last_msg1["tool_calls"][0]["name"] == "create_chapter"

    # 2. Retried send with same client_message_id (e.g. response lost in transit)
    res2 = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "帮我新建一章名为幂等测试", "chapter_id": str(chapter.id), "client_message_id": req_id},
    )
    assert res2.status_code == 200
    msgs2 = res2.json()["messages"]
    assert len(msgs2) == msg_count1

    # 3. New message with new client_message_id should legitimately create chapter without heuristic title blockage
    new_req_id = f"client-req-{uuid4()}"
    res3 = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "帮我新建一章名为幂等测试", "chapter_id": str(chapter.id), "client_message_id": new_req_id},
    )
    assert res3.status_code == 200
    msg3 = res3.json()["messages"][-1]
    assert msg3["tool_calls"][0]["name"] == "create_chapter"
    res_tool = msg3["tool_results"][0]["result"]
    assert res_tool["status"] == "created"
    assert res_tool["title"] == "幂等测试"


async def test_assistant_review_report_version_isolation(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, draft_doc = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    # 1. First version has a review report
    res1 = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "本章审阅报告有什么问题吗？", "chapter_id": str(chapter.id)},
    )
    assert res1.status_code == 200
    msg1 = res1.json()["messages"][-1]
    assert "环境描写略显冗长" in msg1["content"]

    # 2. User creates a new draft version (not reviewed yet)
    new_ver = await DocumentService(async_session).write_document(
        document_id=draft_doc.id,
        content="全新的正文内容，尚未经过审阅。",
        source=DocumentSource.USER,
        expected_current_version_id=draft_doc.current_version_id,
        change_summary="更新第二版正文",
    )
    draft_doc.current_version_id = new_ver.id
    await async_session.commit()

    # 3. Querying review reports now must not falsely show old version's reports
    res2 = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "本章审阅报告有什么问题吗？", "chapter_id": str(chapter.id)},
    )
    assert res2.status_code == 200
    msg2 = res2.json()["messages"][-1]
    assert "当前章节还没有提交过审阅" in msg2["content"]
    assert "环境描写略显冗长" not in msg2["content"]


async def test_assistant_stage_navigation_urls(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    for stage_kw, expected_stage in [
        ("去大纲", "Outline"),
        ("去正文", "Draft"),
        ("去审阅", "Review"),
        ("去读者会", "Reader"),
        ("去定稿", "Final"),
    ]:
        nav_res = await client.post(
            f"{base_url}/{conv_id}/messages",
            json={"content": stage_kw, "chapter_id": str(chapter.id)},
        )
        assert nav_res.status_code == 200
        msg = nav_res.json()["messages"][-1]
        assert msg["tool_calls"][0]["name"] == "navigate_view"
        tool_res = msg["tool_results"][0]["result"]
        assert f"/projects/{project.id}/studio/{chapter.id}?view=Create&stage={expected_stage}" in tool_res["target_url"]


async def test_assistant_reports_real_model_error_transparently(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "openai_compatible_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "openai_compatible_api_key", "sk-invalid-key")
    monkeypatch.setattr(settings, "openai_compatible_model", "custom-model")

    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    # When real LLM responds with HTTP 401 Unauthorized
    import httpx as test_httpx

    orig_post = test_httpx.AsyncClient.post

    async def mock_post(self, url, *args, **kwargs):
        if "api.example.com" in str(url):
            req = test_httpx.Request("POST", "https://api.example.com/v1/chat/completions")
            return test_httpx.Response(401, json={"error": "Invalid API key"}, request=req)
        return await orig_post(self, url, *args, **kwargs)

    monkeypatch.setattr(test_httpx.AsyncClient, "post", mock_post)

    resp = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "你好，能根据当前情节给点灵感吗？", "chapter_id": str(chapter.id)},
    )
    assert resp.status_code == 200
    last_msg = resp.json()["messages"][-1]
    assert "⚠️ 真实模型服务调用失败（HTTP 401" in last_msg["content"]
    assert "已为你切换至本地离线模式" in last_msg["content"]
    assert "Invalid API key" not in last_msg["content"]
    assert "sk-invalid-key" not in last_msg["content"]


async def test_assistant_feedback_revision_preserves_user_feedback(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path
):
    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    base_url = f"/api/v1/projects/{project.id}/assistant/conversations"

    res = await client.post(base_url, json={"chapter_id": str(chapter.id)})
    conv_id = res.json()["id"]

    resp = await client.post(
        f"{base_url}/{conv_id}/messages",
        json={"content": "帮我根据反馈修改：精炼第二段的对话描写", "chapter_id": str(chapter.id)},
    )
    assert resp.status_code == 200
    msg = resp.json()["messages"][-1]
    assert msg["role"] == "assistant"
    tool_call = msg["tool_calls"][0]
    assert tool_call["name"] == "trigger_feedback_revision"
    assert tool_call["arguments"].get("feedback") == "精炼第二段的对话描写"


async def test_trigger_review_and_revision_uuid_and_locators(
    async_session: AsyncSession, tmp_path: Path, monkeypatch
):
    from unittest.mock import AsyncMock, MagicMock
    from uuid import UUID
    from app.services.studio_assistant_tools import (
        tool_trigger_chapter_review,
        tool_trigger_feedback_revision,
    )
    from app.workflows.chapter_production import ChapterProductionStatus
    from app.models.core import WorkflowRun
    from app.models.enums import WorkflowType

    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    mock_req_id = uuid4()
    mock_seg_id = uuid4()

    # Fetch draft document version
    from app.models.core import Document
    draft_doc = await async_session.get(Document, chapter.current_draft_document_id)
    draft_version_id = draft_doc.current_version_id

    # Create a review report with specific evidence_segment_ids targeting the current draft
    report = ReviewReport(
        project_id=project.id,
        chapter_id=chapter.id,
        target_document_id=draft_doc.id,
        target_version_id=draft_version_id,
        review_mode="chapter_editor",
        reviewer_agent_role="editor",
        passed=False,
        summary="测试审阅",
        blocking_issues=[{
            "sequence": 1,
            "code": "PACING_ERROR",
            "severity": "blocking",
            "evidence_segment_ids": [str(mock_seg_id)],
            "rationale": "节奏过慢，需要精简",
            "suggested_action": "精炼段落",
        }],
        warnings=[],
    )
    async_session.add(report)
    await async_session.commit()

    # Mock ChapterProductionV2Service
    mock_state = MagicMock()
    mock_state.status = ChapterProductionStatus.AUTHOR_REVISION
    mock_state.awaiting_user = True
    mock_state.action_request_id = str(mock_req_id)
    mock_state.document_version_id = str(draft_version_id)

    mock_prod = MagicMock()
    mock_prod._locked_state = AsyncMock(return_value=(mock_state, None))
    mock_prod.resolve_author_action = AsyncMock()
    mock_prod.execute_current_review = AsyncMock()
    mock_prod.request_user_feedback_revision = AsyncMock()
    mock_prod.documents = MagicMock()
    mock_prod.documents.derive_chapter_segment_map = AsyncMock()

    mock_comp = MagicMock()
    mock_comp.create_service.return_value = mock_prod

    import app.api.deps
    monkeypatch.setattr(app.api.deps, "get_chapter_production_v2_composition", lambda: mock_comp)

    run = WorkflowRun(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status="running",
    )
    async_session.add(run)
    await async_session.commit()

    # 1. Test tool_trigger_chapter_review: action_request_id must be passed as UUID
    res_rev = await tool_trigger_chapter_review(async_session, project.id, chapter.id)
    assert res_rev["status"] == "triggered"
    assert mock_prod.resolve_author_action.called
    kwargs = mock_prod.resolve_author_action.call_args.kwargs
    assert kwargs["action_request_id"] == mock_req_id
    assert isinstance(kwargs["action_request_id"], UUID)

    # 2. Test tool_trigger_feedback_revision: target_segment_ids derived from review report evidence_segment_ids
    res_feed = await tool_trigger_feedback_revision(
        async_session, project.id, chapter.id, feedback="用户指定的修改意见"
    )
    assert res_feed["status"] == "executed"
    assert mock_prod.request_user_feedback_revision.called
    kwargs_feed = mock_prod.request_user_feedback_revision.call_args.kwargs
    assert kwargs_feed["action_request_id"] == mock_req_id
    assert isinstance(kwargs_feed["action_request_id"], UUID)
    assert kwargs_feed["feedback"] == "用户指定的修改意见"
    assert kwargs_feed["target_segment_ids"] == (mock_seg_id,)


async def test_assistant_review_execution_failure_reported(
    async_session: AsyncSession, tmp_path: Path, monkeypatch
):
    from unittest.mock import AsyncMock, MagicMock
    from app.services.studio_assistant_tools import tool_trigger_chapter_review
    from app.workflows.chapter_production import ChapterProductionStatus
    from app.models.core import WorkflowRun
    from app.models.enums import WorkflowType

    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    mock_req_id = uuid4()

    mock_state = MagicMock()
    mock_state.status = ChapterProductionStatus.AUTHOR_REVISION
    mock_state.awaiting_user = True
    mock_state.action_request_id = str(mock_req_id)

    mock_prod = MagicMock()
    mock_prod._locked_state = AsyncMock(return_value=(mock_state, None))
    mock_prod.resolve_author_action = AsyncMock()
    mock_prod.execute_current_review = AsyncMock(side_effect=RuntimeError("Review execution crashed"))

    mock_comp = MagicMock()
    mock_comp.create_service.return_value = mock_prod

    import app.api.deps
    monkeypatch.setattr(app.api.deps, "get_chapter_production_v2_composition", lambda: mock_comp)

    run = WorkflowRun(
        project_id=project.id,
        chapter_id=chapter.id,
        workflow_type=WorkflowType.CHAPTER_PRODUCTION.value,
        status="running",
    )
    async_session.add(run)
    await async_session.commit()

    res = await tool_trigger_chapter_review(async_session, project.id, chapter.id)
    assert res["status"] == "failed"
    assert "在启动自动审阅执行时失败" in res["message"]


async def test_assistant_no_draft_version_returns_zero_reports(
    async_session: AsyncSession, tmp_path: Path
):
    from app.services.studio_assistant_tools import tool_get_chapter_review_reports

    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    chapter.current_draft_document_id = None
    await async_session.commit()

    res = await tool_get_chapter_review_reports(async_session, project.id, chapter.id)
    assert res["reports_count"] == 0
    assert res["reports"] == []
    assert res["target_version_id"] is None
    assert res["historical_reports_count"] == 1


async def test_assistant_concurrent_same_message_deduplication(
    async_session: AsyncSession, tmp_path: Path
):
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.agents.studio_assistant import StudioAssistantAgent
    from app.api.schemas_studio_assistant import AssistantSendMessageRequest
    from app.services.studio_assistant_service import StudioAssistantService

    project, chapter, _, _ = await seed_data(async_session, tmp_path)
    service = StudioAssistantService(async_session)
    conv = await service.get_or_create_conversation(project.id, chapter.id)

    factory = async_sessionmaker(async_session.bind, expire_on_commit=False)
    client_msg_id = f"client-msg-{uuid4()}"
    payload = AssistantSendMessageRequest(
        content="并发测试消息",
        chapter_id=chapter.id,
        client_message_id=client_msg_id,
    )

    async def send_worker():
        async with factory() as session:
            s = StudioAssistantService(session)
            return await s.send_message(project.id, conv.id, payload)

    with patch.object(
        StudioAssistantAgent,
        "generate_reply",
        new_callable=AsyncMock,
        return_value=("模拟回复", [], []),
    ) as mock_reply:
        res1, res2 = await asyncio.gather(send_worker(), send_worker())

        assert mock_reply.call_count == 1

        async with factory() as verify_session:
            refreshed = await StudioAssistantService(verify_session).get_conversation(
                project.id, conv.id
            )
            user_msgs = [m for m in refreshed.messages if m.role == "user" and m.content == "并发测试消息"]
            assert len(user_msgs) == 1
            assistant_msgs = [m for m in refreshed.messages if m.role == "assistant" and m.content == "模拟回复"]
            assert len(assistant_msgs) == 1
