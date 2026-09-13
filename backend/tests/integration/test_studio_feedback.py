import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas_studio import COMMENT_COLORS
from app.models import DocumentSource, DocumentType, StudioFeedback, StudioRestorePoint, WorkflowRun
from app.services import DocumentService
from tests.integration import test_studio_restore_points as restore_points

client = restore_points.client
seed = restore_points.seed

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def comment(index=0, **changes):
    return dict(id=str(uuid4()), start=0, end=8, quote="original", color=COMMENT_COLORS[index % 12],
                text="修改意见", submitted=False, orphaned=False) | changes


def write_payload(version, comments, revision=0, requirements="通用要求"):
    return dict(request_id=str(uuid4()), expected_current_version_id=str(version),
                expected_revision=revision, comments=comments, requirements=requirements)


async def test_restore_point_captures_feedback_and_backs_up_before_atomic_restore(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, document, points = await seed(async_session, tmp_path)
    url = points.replace("restore-points", "feedback/draft")
    version = document.current_version_id
    original = [comment(), comment(1)]
    assert (await client.put(url, json=write_payload(version, original))).status_code == 200
    point_request = dict(request_id=str(uuid4()), expected_current_version_id=str(version))
    point = (await client.post(points, json=point_request)).json()
    snapshot_url = f"{points}/{point['id']}/feedback"
    snapshot = (await client.get(snapshot_url)).json()
    assert snapshot["available"] and snapshot["comments"] == original and snapshot["requirements"] == "通用要求"
    assert (await client.get(snapshot_url.replace(str(chapter.project_id), str(uuid4())))).status_code == 404
    assert (await client.get(f"{points}/{uuid4()}/feedback")).status_code == 404
    # Mutable edits after the point must not change its immutable feedback.
    changed = [dict(original[1], text="后来补充")]
    assert (await client.put(url, json=write_payload(version, changed, 1, "后来的要求"))).status_code == 200
    saved = await DocumentService(async_session).write_document(
        document_id=document.id, content="before original", source=DocumentSource.USER,
        expected_current_version_id=version,
    )
    before = (await client.get(url)).json()
    assert before["comments"][0]["start"] == 7
    assert (await client.post(points, json=point_request)).json() == point
    assert (await client.get(snapshot_url)).json() == snapshot
    request = dict(request_id=str(uuid4()), expected_current_version_id=str(saved.id))
    restored = await client.post(f"{points}/{point['id']}/restore", json=request)
    assert restored.status_code == 200
    current = (await client.get(url)).json()
    assert current["comments"] == original and current["requirements"] == "通用要求"
    assert current["source_version_id"] == restored.json()["id"] and current["revision"] == 3
    entries = (await client.get(points)).json()
    backup = next(item for item in entries if item["summary"] == "恢复前自动备份")
    retained = (await client.get(f"{points}/{backup['id']}/feedback")).json()
    assert retained["comments"] == before["comments"] and retained["requirements"] == "后来的要求"
    assert backup["version_id"] == str(saved.id)
    # A stale feedback editor must not overwrite the restored state.
    assert (await client.put(url, json=write_payload(restored.json()["id"], changed, 2))).status_code == 409
    assert (await client.post(f"{points}/{point['id']}/restore", json=request)).json() == restored.json()
    assert (await client.get(url)).json() == current
    assert len((await client.get(points)).json()) == 2
    edited = await client.put(url, json=write_payload(restored.json()["id"], changed, 3, "恢复后继续修改"))
    assert edited.status_code == 200
    assert (await client.post(f"{points}/{point['id']}/restore", json=request)).json() == restored.json()
    assert (await client.get(url)).json() == edited.json()


async def test_restore_failure_rolls_back_feedback_backup_and_prose_together(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path, monkeypatch,
):
    from app.workspace.markdown_store import MarkdownStore

    _, document, points = await seed(async_session, tmp_path)
    url = points.replace("restore-points", "feedback/draft")
    version = document.current_version_id
    point = (await client.post(points, json=dict(request_id=str(uuid4()), expected_current_version_id=str(version)))).json()
    assert (await client.put(url, json=write_payload(version, [comment()]))).status_code == 200
    saved = await DocumentService(async_session).write_document(
        document_id=document.id, content="current original", source=DocumentSource.USER,
        expected_current_version_id=version,
    )
    before = (await client.get(url)).json()
    original_write = MarkdownStore.write

    def fail_snapshot(store, path, content):
        if path != "draft.md" and content == "original":
            raise OSError("Injected disk failure")
        return original_write(store, path, content)

    monkeypatch.setattr(MarkdownStore, "write", fail_snapshot)
    request = dict(request_id=str(uuid4()), expected_current_version_id=str(saved.id))
    failed = await client.post(f"{points}/{point['id']}/restore", json=request)
    assert failed.status_code == 500
    assert "Injected disk failure" not in failed.text
    assert (await client.get(url)).json() == before
    assert len((await client.get(points)).json()) == 1
    assert (tmp_path / "draft.md").read_text() == "current original"
    await async_session.refresh(document)
    assert document.current_version_id == saved.id


async def test_legacy_point_does_not_invent_feedback_or_discard_current_comments(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, document, points = await seed(async_session, tmp_path)
    point = StudioRestorePoint(id=uuid4(), chapter_id=chapter.id, document_id=document.id,
                               version_id=document.current_version_id, summary="旧存档")
    async_session.add(point)
    await async_session.commit()
    snapshot = (await client.get(f"{points}/{point.id}/feedback")).json()
    assert snapshot["available"] is False and snapshot["comments"] == []
    url = points.replace("restore-points", "feedback/draft")
    comments = [comment()]
    assert (await client.put(url, json=write_payload(document.current_version_id, comments))).status_code == 200
    request = dict(request_id=str(uuid4()), expected_current_version_id=str(document.current_version_id))
    assert (await client.post(f"{points}/{point.id}/restore", json=request)).status_code == 200
    current = (await client.get(url)).json()
    assert current["comments"] == comments and current["requirements"] == "通用要求"


async def test_feedback_roundtrip_order_submission_and_immutable_snapshot(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, document, points = await seed(async_session, tmp_path)
    url = points.replace("restore-points", "feedback/draft")
    empty = (await client.get(url)).json()
    assert empty["revision"] == 0 and not empty["comments"] and not empty["read_only"]
    comments = [comment(), comment(1)]
    payload = write_payload(document.current_version_id, comments)
    saved = await client.put(url, json=payload)
    assert saved.status_code == 200
    assert saved.json()["comments"] == comments
    assert (await client.put(url, json=payload)).json() == saved.json()
    changed = [dict(comments[1], text="先处理这条"), comments[0]]
    response = await client.put(url, json=write_payload(document.current_version_id, changed, 1, "新的通用要求"))
    assert response.status_code == 200
    assert (await client.get(url)).json()["comments"] == changed
    request = dict(request_id=str(uuid4()), expected_current_version_id=str(document.current_version_id),
                   expected_revision=2, comment_ids=[changed[0]["id"]])
    submitted = await client.post(url + "/submissions", json=request)
    assert submitted.status_code == 200
    snapshot = submitted.json()
    assert snapshot["requirements"] == "新的通用要求"
    assert snapshot["comments"] == [dict(changed[0], submitted=True)]
    current = (await client.get(url)).json()
    assert current["revision"] == 3 and current["comments"][0]["submitted"]
    assert not current["comments"][1]["submitted"]
    # Removing/editing mutable comments must not alter submitted task evidence.
    assert (await client.put(url, json=write_payload(document.current_version_id, [changed[1]], 3, "之后的要求"))).status_code == 200
    assert (await client.get(url + "/submissions/" + snapshot["id"])).json() == snapshot
    assert (await client.post(url + "/submissions", json=request)).json() == snapshot
    assert (await client.post(url + "/submissions", json=request | {"expected_revision": 4})).status_code == 409
    assert (await client.get(url.replace(str(chapter.project_id), str(uuid4())) + "/submissions/" + snapshot["id"])).status_code == 404


async def test_count_color_readonly_and_scope_constraints(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, document, points = await seed(async_session, tmp_path)
    url = points.replace("restore-points", "feedback/draft")
    comments = [comment(index) for index in range(12)]
    version = document.current_version_id
    assert (await client.put(url, json=write_payload(version, comments))).status_code == 200
    assert (await client.put(url, json=write_payload(version, comments + [comment()], 1))).status_code == 422
    duplicate = comments[:-1] + [comment(color=comments[0]["color"])]
    assert (await client.put(url, json=write_payload(version, duplicate, 1))).status_code == 422
    assert (await client.put(url, json=write_payload(uuid4(), comments, 1))).status_code == 409
    assert (await client.put(url, json=write_payload(version, comments, 0))).status_code == 409
    assert (await client.put(url, json=write_payload(version, comments, 1) | {"document_id": str(uuid4())})).status_code == 422
    wrong = url.replace(str(chapter.project_id), str(uuid4()))
    assert (await client.get(wrong)).status_code == 404
    assert (await client.put(wrong, json=write_payload(version, [], 1))).status_code == 404
    run = WorkflowRun(project_id=chapter.project_id, chapter_id=chapter.id,
                      workflow_type="chapter_production", status="DRAFTING", awaiting_user=False)
    async_session.add(run)
    await async_session.commit()
    assert (await client.get(url)).json()["read_only"]
    assert (await client.put(url, json=write_payload(version, [], 1))).status_code == 409
    assert len((await client.get(url)).json()["comments"]) == 12


async def test_over_limit_history_can_be_edited_and_deleted_but_not_extended(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, document, points = await seed(async_session, tmp_path)
    url = points.replace("restore-points", "feedback/draft")
    version = document.current_version_id
    assert (await client.put(url, json=write_payload(version, [comment()]))).status_code == 200
    state = await async_session.get(StudioFeedback, (chapter.id, "draft"))
    state.comments = [comment(index) for index in range(13)]
    await async_session.commit()
    history = (await client.get(url)).json()["comments"]
    assert len(history) == 13
    history[0]["text"] = "历史条目仍可编辑"
    assert (await client.put(url, json=write_payload(version, history, 1))).status_code == 200
    assert (await client.put(url, json=write_payload(version, history + [comment()], 2))).status_code == 422
    assert (await client.put(url, json=write_payload(version, history[1:], 2))).status_code == 200
    assert len((await client.get(url)).json()["comments"]) == 12


async def test_anchors_use_utf16_and_changed_quotes_are_not_rebound_elsewhere(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    _, document, points = await seed(async_session, tmp_path)
    url = points.replace("restore-points", "feedback/draft")
    service = DocumentService(async_session)
    original = await service.write_document(document_id=document.id, content="🙂original and tail",
                                           source=DocumentSource.USER, expected_current_version_id=document.current_version_id)
    invalid = comment(start=0, end=1, quote="🙂")
    assert (await client.put(url, json=write_payload(original.id, [invalid]))).status_code == 422
    selected = comment(start=2, end=10)
    assert (await client.put(url, json=write_payload(original.id, [selected]))).status_code == 200
    later = await service.write_document(document_id=document.id, content="prefix 🙂original and tail",
                                        source=DocumentSource.USER, expected_current_version_id=original.id)
    projected = (await client.get(url)).json()
    assert projected["source_version_id"] == str(later.id)
    assert projected["comments"][0]["start"] == 9 and not projected["comments"][0]["orphaned"]
    deleted = await service.write_document(document_id=document.id, content="🙂changed and tail\noriginal",
                                          source=DocumentSource.USER, expected_current_version_id=later.id)
    orphan = (await client.get(url)).json()["comments"][0]
    assert orphan["start"] == orphan["end"] == 0 and orphan["orphaned"]
    assert orphan["quote"] == "original"
    # A stale client cannot silently move an existing marker to another occurrence.
    forged = dict(selected, start=19, end=27, quote="original")
    saved = await client.put(url, json=write_payload(deleted.id, [forged], 1))
    assert saved.status_code == 200 and saved.json()["comments"][0]["orphaned"]


async def test_submission_and_edit_are_serialized_by_revision(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    _, document, points = await seed(async_session, tmp_path)
    url = points.replace("restore-points", "feedback/draft")
    comments = [comment()]
    payload = write_payload(document.current_version_id, comments)
    duplicates = await asyncio.gather(*(client.put(url, json=payload) for _ in range(2)))
    assert all(response.status_code == 200 for response in duplicates)
    assert duplicates[0].json() == duplicates[1].json()
    request = dict(request_id=str(uuid4()), expected_current_version_id=str(document.current_version_id),
                   expected_revision=1, comment_ids=[comments[0]["id"]])
    responses = await asyncio.gather(
        client.post(url + "/submissions", json=request),
        client.put(url, json=write_payload(document.current_version_id, [dict(comments[0], text="修改中")], 1)),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    if responses[0].status_code == 200:
        assert responses[0].json()["comments"][0]["text"] == "修改意见"
        assert (await client.post(url + "/submissions", json=request)).json() == responses[0].json()


async def test_regions_are_independent_and_submission_flags_are_server_owned(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    chapter, document, points = await seed(async_session, tmp_path)
    url = points.replace("restore-points", "feedback/draft")
    assert (await client.get(url.replace("draft", "outline"))).json()["read_only"]
    assert (await client.put(url, json=write_payload(document.current_version_id, [comment(submitted=True)]))).status_code == 422
    payload = write_payload(document.current_version_id, [], requirements="只提交通用要求")
    assert (await client.put(url, json=payload)).status_code == 200
    request = dict(request_id=str(uuid4()), expected_current_version_id=str(document.current_version_id),
                   expected_revision=1, comment_ids=[])
    response = await client.post(url + "/submissions", json=request)
    assert response.status_code == 200 and response.json()["requirements"] == "只提交通用要求"
    assert (await client.get(url.replace("draft", "outline") + "/submissions/" + response.json()["id"])).status_code == 404
    outline = await DocumentService(async_session).create_document(
        project_id=chapter.project_id, chapter_id=chapter.id, document_type=DocumentType.CHAPTER_SELECTED_OUTLINE,
        title="Outline", path="outline.md", content="outline", source=DocumentSource.USER,
    )
    chapter.current_outline_document_id = outline.id
    await async_session.commit()
    outline_url = url.replace("draft", "outline")
    note = comment(end=7, quote="outline")
    saved = await client.put(outline_url, json=write_payload(outline.current_version_id, [note], requirements="大纲要求"))
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    submitted = await client.post(outline_url + "/submissions", json=dict(
        request_id=str(uuid4()), expected_current_version_id=str(outline.current_version_id),
        expected_revision=1, comment_ids=[note["id"]],
    ))
    assert submitted.status_code == 200 and submitted.json()["comments"] == [dict(note, submitted=True)]
    current_draft = (await client.get(url)).json()
    assert current_draft["revision"] == 2 and current_draft["comments"] == [] and current_draft["requirements"] == "只提交通用要求"


async def test_unsaved_selection_can_be_retained_only_as_an_explicitly_orphaned_comment(
    client: httpx.AsyncClient, async_session: AsyncSession, tmp_path: Path,
):
    _, document, points = await seed(async_session, tmp_path)
    url = points.replace("restore-points", "feedback/draft")
    orphan = comment(start=0, end=0, quote="Selected text changed before its first save", orphaned=True)
    saved = await client.put(url, json=write_payload(document.current_version_id, [orphan]))
    assert saved.status_code == 200 and saved.json()["comments"] == [orphan]
    assert (await client.get(url)).json()["comments"][0]["orphaned"]
    assert (await client.put(url, json=write_payload(document.current_version_id, [comment(start=1, end=2, orphaned=True)], 1))).status_code == 422
