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
