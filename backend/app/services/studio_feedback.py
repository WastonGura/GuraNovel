"""Version-bound comments and immutable submission evidence for Studio."""

from copy import deepcopy
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas_studio import FeedbackRegion, FeedbackSubmitRequest, FeedbackWriteRequest
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import Chapter, Document, StudioFeedback, StudioFeedbackSubmission
from app.services.document_service import DocumentService, DocumentVersionConflictError
from app.services.studio_scope import ensure_studio_writable, studio_chapter


class FeedbackInvalidError(AppError):
    status_code = 422
    code = "studio_feedback_invalid"
    default_message = "The feedback contains an invalid comment or anchor."


def utf16_slice(text: str, start: int, end: int) -> str:
    try:
        raw = text.encode("utf-16-le")
        if end * 2 > len(raw):
            raise ValueError
        return raw[start * 2:end * 2].decode("utf-16-le")
    except (ValueError, UnicodeError):
        raise FeedbackInvalidError() from None


def project_anchors(comments: list[dict], before: str, after: str, same_document: bool) -> list[dict]:
    # Preserve only unchanged prefix/suffix ranges. A changed selection is never
    # rebound with a global text search, even if its quote occurs elsewhere.
    prefix, old_end, new_end = 0, len(before), len(after)
    while prefix < min(old_end, new_end) and before[prefix] == after[prefix]:
        prefix += 1
    while old_end > prefix and new_end > prefix and before[old_end - 1] == after[new_end - 1]:
        old_end -= 1
        new_end -= 1
    prefix = len(before[:prefix].encode("utf-16-le")) // 2
    old_end = len(before[:old_end].encode("utf-16-le")) // 2
    new_end = len(after[:new_end].encode("utf-16-le")) // 2
    result = deepcopy(comments)
    for comment in result:
        linked = same_document and not comment.get("orphaned", False)
        if linked and comment["end"] <= prefix:
            pass
        elif linked and comment["start"] >= old_end:
            comment["start"] += new_end - old_end
            comment["end"] += new_end - old_end
        else:
            linked = False
        if linked:
            try:
                linked = utf16_slice(after, comment["start"], comment["end"]) == comment["quote"]
            except FeedbackInvalidError:
                linked = False
        if not linked:
            comment.update(start=0, end=0, orphaned=True)
        else:
            comment["orphaned"] = False
    return result


class StudioFeedbackService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.documents = DocumentService(session)

    async def _context(self, project_id: UUID, chapter_id: UUID, region: FeedbackRegion):
        chapter = await studio_chapter(self.session, project_id, chapter_id)
        document_id = chapter.current_outline_document_id if region == "outline" else chapter.current_draft_document_id
        document = await self.session.scalar(select(Document).where(
            Document.id == document_id, Document.chapter_id == chapter_id,
            Document.project_id == project_id,
            Document.type == ("chapter_selected_outline" if region == "outline" else "chapter_draft"),
        ).with_for_update().execution_options(populate_existing=True)) if document_id else None
        if document_id and (document is None or document.current_version_id is None):
            raise ConflictError("The chapter source document is unavailable.")
        state = await self.session.get(StudioFeedback, (chapter_id, region), populate_existing=True)
        return chapter, document, state

    async def _comments(self, state: StudioFeedback | None, document: Document | None) -> list[dict]:
        if state is None:
            return []
        if document is None:
            return [dict(comment, start=0, end=0, orphaned=True) for comment in state.comments]
        before = await self.documents.read_version_content(state.document_id, state.source_version_id)
        after = before if state.source_version_id == document.current_version_id else await self.documents.read_version_content(document.id, document.current_version_id)
        return project_anchors(state.comments, before, after, state.document_id == document.id)

    async def _response(self, chapter: Chapter, document: Document | None, state: StudioFeedback | None, region: FeedbackRegion):
        readonly = document is None
        try:
            await ensure_studio_writable(self.session, chapter)
        except ConflictError:
            readonly = True
        return dict(chapter_id=chapter.id, region=region,
                    document_id=document.id if document else None,
                    source_version_id=document.current_version_id if document else None,
                    revision=state.revision if state else 0, comments=await self._comments(state, document),
                    requirements=state.requirements if state else "", read_only=readonly)

    async def read(self, project_id: UUID, chapter_id: UUID, region: FeedbackRegion):
        chapter, document, state = await self._context(project_id, chapter_id, region)
        return await self._response(chapter, document, state, region)

    async def write(self, project_id: UUID, chapter_id: UUID, region: FeedbackRegion, payload: FeedbackWriteRequest):
        chapter, document, state = await self._context(project_id, chapter_id, region)
        fingerprint = sha256(payload.model_dump_json().encode()).hexdigest()
        if state and state.request_id == payload.request_id:
            if state.request_fingerprint != fingerprint:
                raise ConflictError("The feedback request has different inputs.")
            return await self._response(chapter, document, state, region)
        await ensure_studio_writable(self.session, chapter)
        self._expected(document, state, payload)
        before = {item["id"]: item for item in await self._comments(state, document)}
        incoming = [item.model_dump(mode="json") for item in payload.comments]
        additions = [item for item in incoming if item["id"] not in before]
        if additions and (len(incoming) > 12 or len(before) > 12):
            raise FeedbackInvalidError("Each editor supports at most 12 comments.")
        content = await self.documents.read_version_content(document.id, document.current_version_id)
        for comment in incoming:
            old = before.get(comment["id"])
            if old is None or old["color"] != comment["color"]:
                if any(other["id"] != comment["id"] and other["color"] == comment["color"] for other in incoming):
                    raise FeedbackInvalidError("Comment colors must be unique.")
            if old:
                comment.update({key: old[key] for key in ("start", "end", "quote", "orphaned")})
                comment["submitted"] = old.get("submitted", False)
            elif comment["submitted"] or not comment["quote"].strip() or (
                (comment["start"] != 0 or comment["end"] != 0) if comment["orphaned"]
                else utf16_slice(content, comment["start"], comment["end"]) != comment["quote"]
            ):
                raise FeedbackInvalidError()
        if state is None:
            state = StudioFeedback(chapter_id=chapter_id, region=region, revision=0)
            self.session.add(state)
        state.document_id = document.id
        state.source_version_id = document.current_version_id
        state.comments, state.requirements = incoming, payload.requirements
        state.revision += 1
        state.request_id, state.request_fingerprint = payload.request_id, fingerprint
        await self.session.commit()
        return await self._response(chapter, document, state, region)

    @staticmethod
    def _expected(document, state, payload):
        if document is None or document.current_version_id != payload.expected_current_version_id:
            raise DocumentVersionConflictError()
        if (state.revision if state else 0) != payload.expected_revision:
            raise ConflictError("The feedback has changed. Reload before saving.")

    async def submit(self, project_id: UUID, chapter_id: UUID, region: FeedbackRegion, payload: FeedbackSubmitRequest):
        chapter, document, state = await self._context(project_id, chapter_id, region)
        fingerprint = sha256(payload.model_dump_json().encode()).hexdigest()
        previous = await self.session.get(StudioFeedbackSubmission, payload.request_id)
        if previous:
            if previous.chapter_id != chapter_id or previous.region != region or previous.request_fingerprint != fingerprint:
                raise ConflictError("The submission request has different inputs.")
            return previous
        await ensure_studio_writable(self.session, chapter)
        self._expected(document, state, payload)
        comments = await self._comments(state, document)
        ids = {str(item) for item in payload.comment_ids}
        if not ids.issubset({comment["id"] for comment in comments}) or state is None:
            raise FeedbackInvalidError()
        selected = [dict(comment, submitted=True) for comment in comments if comment["id"] in ids]
        if not selected and not state.requirements.strip():
            raise FeedbackInvalidError("There is no feedback to submit.")
        submission = StudioFeedbackSubmission(
            id=payload.request_id, chapter_id=chapter_id, region=region,
            document_id=document.id, source_version_id=document.current_version_id,
            feedback_revision=state.revision + 1, comments=deepcopy(selected),
            requirements=state.requirements, request_fingerprint=fingerprint,
        )
        state.comments = [dict(comment, submitted=True) if comment["id"] in ids else comment for comment in comments]
        state.document_id, state.source_version_id = document.id, document.current_version_id
        state.revision += 1
        # An older PUT retry must not return this post-submission state as its own result.
        state.request_id = payload.request_id
        state.request_fingerprint = fingerprint
        self.session.add(submission)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise ConflictError("The submission request has different inputs.") from None
        return submission

    async def read_submission(self, project_id: UUID, chapter_id: UUID, region: FeedbackRegion, submission_id: UUID):
        await studio_chapter(self.session, project_id, chapter_id)
        submission = await self.session.get(StudioFeedbackSubmission, submission_id)
        if submission is None or submission.chapter_id != chapter_id or submission.region != region:
            raise NotFoundError("Feedback submission not found.")
        return submission
