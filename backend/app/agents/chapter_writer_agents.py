"""Injected, provider-neutral adapters for chapter candidates."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from app.agents.chapter_writer_contracts import (
    CandidateChapterOutput,
    ChapterWriterRequest,
    InitialDraftRequest,
    ReviewDrivenRevisionRequest,
    SegmentDraftRequest,
    UserFeedbackRevisionRequest,
    validate_candidate_chapter_output,
)
from app.agents.errors import ProfileRegistryError
from app.agents.profiles import AgentProfile, ProfileRegistry
from app.llm.errors import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


_SAFE_PROVIDER_ERRORS = (
    ProfileRegistryError,
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def _safe_error_type(error: Exception) -> type[Exception]:
    for error_type in _SAFE_PROVIDER_ERRORS:
        if isinstance(error, error_type):
            return error_type
    return ProviderUnavailableError


@runtime_checkable
class InitialDraftProvider(Protocol):
    async def draft_initial(
        self, request: InitialDraftRequest, profile: AgentProfile
    ) -> object: ...


@runtime_checkable
class SegmentDraftProvider(Protocol):
    async def draft_segments(
        self, request: SegmentDraftRequest, profile: AgentProfile
    ) -> object: ...


@runtime_checkable
class UserFeedbackRevisionProvider(Protocol):
    async def revise_from_user_feedback(
        self, request: UserFeedbackRevisionRequest, profile: AgentProfile
    ) -> object: ...


@runtime_checkable
class ReviewDrivenRevisionProvider(Protocol):
    async def revise_from_review(
        self, request: ReviewDrivenRevisionRequest, profile: AgentProfile
    ) -> object: ...


class _CandidateAgent:
    def __init__(self, provider: object, registry: ProfileRegistry | None = None) -> None:
        self._provider = provider
        self._registry = registry or ProfileRegistry()

    @staticmethod
    def validate_output(
        raw_output: object, *, request: ChapterWriterRequest
    ) -> CandidateChapterOutput:
        return validate_candidate_chapter_output(raw_output, request=request)

    async def _call(
        self,
        *,
        request: ChapterWriterRequest,
        profile_name: str,
        mode: str,
        request_type: type[ChapterWriterRequest],
        provider_type: type[object],
        provider_method: str,
    ) -> CandidateChapterOutput:
        failure: type[Exception] | None = None
        try:
            if not isinstance(self._provider, provider_type):
                raise ProviderConfigurationError()
            if type(request) is not request_type:
                raise ProviderConfigurationError()
            try:
                request = request_type.model_validate(request.model_dump(mode="python"))
            except ValidationError:
                raise ProviderConfigurationError() from None
            profile = self._registry.load(profile_name, mode=mode)
            call = getattr(self._provider, provider_method)
            raw_output = await call(request, profile)
            result = self.validate_output(raw_output, request=request)
        except _SAFE_PROVIDER_ERRORS as error:
            failure = _safe_error_type(error)
        except Exception as error:
            failure = _safe_error_type(error)
        if failure is not None:
            raise failure() from None
        return result


class WriterAgent(_CandidateAgent):
    async def initial_draft(self, request: InitialDraftRequest) -> CandidateChapterOutput:
        return await self._call(
            request=request,
            profile_name="writer_agent",
            mode="initial_draft",
            request_type=InitialDraftRequest,
            provider_type=InitialDraftProvider,
            provider_method="draft_initial",
        )

    async def segment_draft(self, request: SegmentDraftRequest) -> CandidateChapterOutput:
        return await self._call(
            request=request,
            profile_name="writer_agent",
            mode="segment_draft",
            request_type=SegmentDraftRequest,
            provider_type=SegmentDraftProvider,
            provider_method="draft_segments",
        )


class RevisionAgent(_CandidateAgent):
    async def user_feedback_revision(
        self, request: UserFeedbackRevisionRequest
    ) -> CandidateChapterOutput:
        return await self._call(
            request=request,
            profile_name="revision_agent",
            mode="user_feedback_revision",
            request_type=UserFeedbackRevisionRequest,
            provider_type=UserFeedbackRevisionProvider,
            provider_method="revise_from_user_feedback",
        )

    async def review_driven_revision(
        self, request: ReviewDrivenRevisionRequest
    ) -> CandidateChapterOutput:
        return await self._call(
            request=request,
            profile_name="revision_agent",
            mode="review_driven_revision",
            request_type=ReviewDrivenRevisionRequest,
            provider_type=ReviewDrivenRevisionProvider,
            provider_method="revise_from_review",
        )


__all__ = [
    "InitialDraftProvider",
    "ReviewDrivenRevisionProvider",
    "RevisionAgent",
    "SegmentDraftProvider",
    "UserFeedbackRevisionProvider",
    "WriterAgent",
]
