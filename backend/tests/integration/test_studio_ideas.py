from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chapter, Project
from tests.integration.test_studio_restore_points import client  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def seed(session: AsyncSession, path: Path):
    project = Project(slug=f"ideas-{uuid4()}", title="Ideas", workspace_root=str(path))
    session.add(project)
    await session.flush()
    chapter = Chapter(project_id=project.id, chapter_number=1)
    session.add(chapter)
    await session.commit()
    return project, chapter, f"/api/v1/projects/{project.id}/chapters/{chapter.id}/outline/idea"


async def test_idea_persists_is_scoped_and_rejects_stale_overwrite(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    project, chapter, url = await seed(async_session, tmp_path)
    initial = await client.get(url)
    assert initial.status_code == 200, initial.text
    assert initial.json()["revision"] == 0
    payload = {"text": "三种不同的调查方向", "expected_revision": 0, "request_id": str(uuid4())}
    written = await client.put(url, json=payload)
    assert written.status_code == 200, written.text
    assert written.json()["revision"] == 1
    assert (await client.put(url, json=payload)).json() == written.json()
    assert (await client.get(url)).json()["text"] == payload["text"]
    stale = await client.put(url, json={**payload, "request_id": str(uuid4()), "text": "旧标签覆盖"})
    assert stale.status_code == 409
    reused = await client.put(url, json={**payload, "text": "同请求身份不同内容"})
    assert reused.status_code == 409
    assert (await client.get(url.replace(str(project.id), str(uuid4())))).status_code == 404
    assert (await client.get(url)).json()["text"] == payload["text"]
    chapter.current_draft_document_id = uuid4()
    await async_session.commit()
    assert (await client.get(url)).json()["read_only"] is True
    assert (await client.put(url, json={**payload, "expected_revision": 1, "request_id": str(uuid4())})).status_code == 409
